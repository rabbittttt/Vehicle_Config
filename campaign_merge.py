"""Three-way merging of editable campaign observations, identified by business keys."""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
import re
import zlib

from catalog import Conflict
from stage_workbook import (BACKFILLED, DAILY, NO_PREORDER, PREORDER_DATES, PRIMARY, SECONDARY, SUMMARY,
                            _date, _detail_rows, _get, _header, _is_date, _key, _text)


class MergeConflict(Conflict):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def _digest(data):
    # Browsers serialize integral floats as integers after a JSON round trip.
    def normalize(value):
        if isinstance(value,float) and value.is_integer():
            return int(value)
        if isinstance(value,list):
            return [normalize(v) for v in value]
        if isinstance(value,dict):
            return {k:normalize(v) for k,v in value.items()}
        return value
    raw = json.dumps(normalize(data),sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def remember_base(db,revision,data,private=False):
    digest = _digest(data)
    token = f'view:{revision}:{digest}' if private else str(revision)
    payload = zlib.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),level=1)
    db.execute("""INSERT INTO edit_bases(token,revision,digest,data) VALUES (?,?,?,?)
                  ON CONFLICT(token) DO UPDATE SET data=excluded.data, saved_at=CURRENT_TIMESTAMP""",
               (token,revision,digest,payload))
    db.execute("""UPDATE edit_bases SET data=NULL WHERE saved_at<datetime('now','-30 days') AND data IS NOT NULL""")
    return token


def recalled_base(db,revision,token,submitted):
    if token is not None and (not isinstance(token,str) or not token.startswith(f'view:{revision}:')):
        raise ValueError('编辑视图标识无效')
    row = db.execute('SELECT digest,data FROM edit_bases WHERE token=? AND revision=?',
                     (token or str(revision),revision)).fetchone()
    if row is None:
        raise Conflict('此编辑版本不可用，请重新载入草稿后核对')
    if row['data'] is not None:
        return json.loads(zlib.decompress(row['data']))
    if submitted is None:
        raise MergeConflict('请重传打开时的编辑基线',needsBase=True)
    data = {'sheets':submitted}
    if _digest(data)!=row['digest']:
        raise Conflict('本地基线与打开时的草稿不一致，请重新载入后核对')
    return data


@dataclass(frozen=True)
class Cell:
    sheet: str
    model: str
    secondary: str
    section: int
    field: str

    @property
    def record(self):
        return self.sheet,self.model,self.secondary,self.section

    @property
    def id(self):
        return json.dumps([*self.record,self.field],ensure_ascii=False,separators=(',',':'))


class Touches:
    """Observe coordinates during authoritative replay, before later edits shift them."""
    def __init__(self, base):
        self.summary = {}
        self.detail = {}
        self.stage_rows = defaultdict(set)
        summary = next(sheet for sheet in base['sheets'] if sheet['name']==SUMMARY)
        for n,row in enumerate(summary['rows'][1:],2):
            self.stage_rows[_key(row,1)].add(n)

    def __call__(self, sheet, edit):
        name = sheet['name']
        if 'model' in edit:
            field = _date(edit['date']) if name==DAILY else f"D{edit['day']}"
            cell = Cell(name,_text(edit['model']),_text(edit.get('secondary') or '') or '',
                        0 if name==DAILY else edit.get('section',1),field)
        else:
            n = edit['row']
            head = _header(sheet,n)
            field = head[edit['col']-1]
            if name==SUMMARY:
                self.summary[n,field] = None
                if field in (PRIMARY, SECONDARY):
                    old = _key(sheet['rows'][n-1], 1)
                    value = _text(edit['value']) if edit['value'] not in (None,'') else None
                    new = (value,old[1]) if field==PRIMARY else (old[0],value or '')
                    self.stage_rows[old].discard(n)
                    self.stage_rows[new].add(n)
                return
            key = _key(sheet['rows'][n-1],head.index(SECONDARY))
            section = 0 if name==DAILY else bisect_right(sheet['headerRows'],n)
            cell = Cell(name,*key,section,field)
        owners = self.stage_rows[cell.model,cell.secondary]
        stage_row = next(iter(owners)) if len(owners)==1 else None
        self.detail[cell,stage_row] = None


class Records:
    def __init__(self, data):
        self.sheets = {s['name']:s for s in data['sheets']}
        self.records = defaultdict(list)
        self.new_rows = {}
        self.new_series = set()
        self.manual = set(self.sheets[SUMMARY].get('reportedTotals', []))
        for sheet in data['sheets']:
            name = sheet['name']
            headers = {id(sheet['rows'][n-1]):{field:i for i,field in enumerate(sheet['rows'][n-1])}
                       for n in sheet['headerRows']}
            for n,row,head,secondary in _detail_rows(sheet):
                section = 0 if name in (DAILY,SUMMARY) else bisect_right(sheet['headerRows'],n)
                self.records[name,*_key(row,secondary),section].append((n,row,headers[id(head)]))

    def row(self, cell):
        choices = self.records.get(cell.record,[])
        if len(choices)>1:
            choices = [entry for entry in choices if cell.field in entry[2]]
            if len(choices)!=1:
                raise Ambiguous
        return choices[0] if choices else None

    def value(self, cell, field=None):
        entry = self.row(cell)
        field = field or cell.field
        value = None
        if entry:
            n,row,columns = entry
            value = _get(row,columns.get(field,-1))
        if cell.sheet==SUMMARY:
            col = self.sheets[SUMMARY]['rows'][0].index(field)+1
            if col in BACKFILLED:
                return {'value':value, 'manual':bool(entry and f'{n}:{col}' in self.manual)}
        return value

    def patch(self, cell, value):
        if isinstance(value,dict):
            value = value['value'] if value['manual'] else None
        entry = self.row(cell)
        if entry and cell.field in entry[2]:
            return dict(sheet=cell.sheet,row=entry[0],col=entry[2][cell.field]+1,value=value)
        if cell.sheet==SUMMARY:
            sheet = self.sheets[SUMMARY]
            n = self.new_rows.setdefault(cell.record,len(sheet['rows'])+len(self.new_rows)+1)
            return dict(sheet=SUMMARY,row=n,col=sheet['rows'][0].index(cell.field)+1,value=value)
        if cell.sheet==DAILY and _is_date(cell.field):
            axis = {'date':cell.field}
        elif re.fullmatch(r'D[1-9]\d*',cell.field):
            axis = {'day':int(cell.field[1:]),'section':cell.section}
        elif entry is None and cell.record in self.new_series:
            # The quantity edit creates this row. Its independent end date can
            # only be located once that insertion has actually taken place.
            return FollowupField(cell,value)
        else:
            raise Ambiguous
        if entry is None and value is not None:
            self.new_series.add(cell.record)
        return dict(sheet=cell.sheet,model=cell.model,secondary=cell.secondary,value=value,**axis)


class Ambiguous(Exception):
    pass


@dataclass(frozen=True)
class FollowupField:
    cell: Cell
    value: object


def _apply_patches(data, patches, apply):
    # Physical addresses are valid before date columns or quantity rows are inserted.
    immediate = [p for p in patches if not isinstance(p,FollowupField)]
    if immediate:
        data = apply(data, sorted(immediate,key=lambda edit:'model' in edit))
    followup = [p for p in patches if isinstance(p,FollowupField)]
    if followup:
        records = Records(data)
        data = apply(data, [records.patch(p.cell,p.value) for p in followup])
    return data


def merge_workbooks(base, latest, edits, resolutions, revision, apply):
    if base==latest and not resolutions:
        local = apply(base,edits)
        return local,local
    touches = Touches(base)
    local = apply(base, edits, observe=touches)
    if local==latest:
        return local,local
    conflicts = []
    discarded = False

    def decide(cell, original, remote, own, structural):
        nonlocal discarded
        resolution = resolutions.get(cell.id)
        if (isinstance(resolution,dict) and 'current' in resolution and resolution['current']==remote
                and resolution.get('choice') in (('theirs',) if structural else ('theirs','mine'))):
            if resolution['choice']=='theirs':
                discarded = True
            return resolution['choice']
        conflicts.append(dict(id=cell.id,sheet=cell.sheet,model=cell.model,secondary=cell.secondary,
                              field=cell.field,base=original,current=remote,local=own,structural=structural))
        return None

    before, shared, mine = (Records(data) for data in (base,latest,local))
    base_rows = before.sheets[SUMMARY]['rows']
    local_rows = mine.sheets[SUMMARY]['rows']
    skipped = set()
    # Align local renames before comparing fields. Use the original business key
    # to find the shared row even after an import has moved it to a new position.
    for n in dict.fromkeys(n for n,_ in touches.summary):
        old = _key(base_rows[n-1],1) if n<=len(base_rows) else (None,'')
        new = _key(local_rows[n-1],1)
        if not old[0] or old==new:
            continue
        original = Cell(SUMMARY,*old,0,'车型与阶段')
        remote_rows = shared.records.get(original.record,[])
        target_rows = shared.records.get((SUMMARY,*new,0),[])
        destination = any((model,secondary)==new for _,model,secondary,_ in shared.records)
        # A second editor may have performed the identical rename. Only reuse
        # that target if it was absent at opening and its stage data still matches.
        already_renamed = (not remote_rows and new[0] and len(target_rows)==1
                           and not any((model,secondary)==new for _,model,secondary,_ in before.records)
                           and target_rows[0][1][2:] in (base_rows[n-1][2:],local_rows[n-1][2:]))
        if (not new[0] or len(before.records[original.record])!=1
                or (not already_renamed and (len(remote_rows)!=1 or destination))):
            decide(original,' · '.join(filter(None,old)),
                   '原记录已更名、移除或目标名称无法唯一定位',
                   ' · '.join(filter(None,new)) or '清空名称',True)
            skipped.add(new)
            continue
        def rename(data, row):
            changes = [dict(sheet=SUMMARY,row=row,col=i+1,value=name or None)
                       for i,name in enumerate(new) if old[i]!=name]
            return apply(data,changes)
        try:
            aligned_latest = latest if already_renamed else rename(latest,remote_rows[0][0])
            aligned_base = rename(base,n)
        except ValueError:
            decide(original,' · '.join(filter(None,old)), '关联填报记录无法唯一定位',
                   ' · '.join(filter(None,new)),True)
            skipped.add(new)
            continue
        latest,base = aligned_latest,aligned_base
        before,shared = Records(base),Records(latest)

    patch_shared, patch_visible = [], []
    cells = {}
    for n,field in touches.summary:
        cells[Cell(SUMMARY,*_key(local_rows[n-1],1),0,field)] = None
    for cell,stage_row in touches.detail:
        if stage_row:
            key = _key(local_rows[stage_row-1],1)
            cell = replace(cell,model=key[0],secondary=key[1])
        cells[cell] = None

    coupled = set()
    for cell in cells:
        if (cell.model,cell.secondary) in skipped:
            continue
        try:
            if not cell.model or (cell.sheet!=SUMMARY and cell.field in (PRIMARY,SECONDARY)):
                raise Ambiguous
            shared_row = shared.row(cell)
            base_row = before.row(cell)
            if (base_row and not shared_row) or (cell.sheet!=SUMMARY and
                    before.records.get((SUMMARY,cell.model,cell.secondary,0)) and
                    not shared.records.get((SUMMARY,cell.model,cell.secondary,0))):
                raise Ambiguous
            # Moving to or from NA changes BOTH preorder endpoints. Compare them as
            # one item, while ordinary edits to the start/end dates remain independent.
            fields = [cell.field]
            if cell.field in PREORDER_DATES and any(
                    index.value(cell,field)==NO_PREORDER for index in (before,shared,mine) for field in PREORDER_DATES):
                if cell.record in coupled:
                    continue
                coupled.add(cell.record)
                fields = list(PREORDER_DATES)
            group = len(fields)>1
            def values(index):
                return [index.value(cell,f) for f in fields] if group else index.value(cell)
            original, remote, own = (values(index) for index in (before,shared,mine))
            if own==original:
                continue
            display = Cell(*cell.record,'小订起止日期') if group else cell
            choice = 'mine'
            if own!=remote and (original!=remote or display.id in resolutions):
                choice = decide(display,original,remote,own,False)
            if choice:
                chosen = own if choice=='mine' else remote
                mine_patches, visible_patches = [], []
                for field,amount in zip(fields,chosen if group else [chosen]):
                    address = replace(cell,field=field)
                    if choice=='mine' and own!=remote:
                        mine_patches.append(shared.patch(address,amount))
                    visible_patches.append(before.patch(address,amount))
                patch_shared.extend(mine_patches)
                patch_visible.extend(visible_patches)
        except Ambiguous:
            decide(cell,'原记录','记录已删除、改名或无法唯一定位','本地修改',True)
    if conflicts:
        raise MergeConflict('请选择每处冲突的处理方式', revision=revision, conflicts=conflicts)
    merged = _apply_patches(latest,patch_shared,apply)
    visible = local
    if discarded:
        visible = _apply_patches(base,patch_visible,apply)
    return merged,visible
