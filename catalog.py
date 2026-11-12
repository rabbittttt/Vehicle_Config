"""Versioned vehicle catalog. IDs are stable; records are retired, never removed."""
import copy, json, sqlite3, threading, uuid, os
from pathlib import Path
from datetime import datetime, timezone

TABLES = {
 'models': ['id','brand','series','generation','subgeneration','year','energy','tier','status'],
 'phases': ['id','model_id','name','presale_date','sale_date'],
 'versions': ['id','model_id','name','power','battery','status'],
 'options': ['id','model_id','category','name','status'],
 'matrix': ['id','version_id','option_id','state'],
 'aliases': ['id','source','name','target_type','target_id'],
 'groups': ['id','name','scope','start_date','end_date','members'],
}
STATES = ['未配置','标配','选配','不可选']
def empty(): return {key: [] for key in TABLES}
def now(): return datetime.now(timezone.utc).isoformat()
class Conflict(Exception): pass

def validate(data, publish=False):
    errors=[]
    if not isinstance(data,dict) or set(data)!=set(TABLES): return ['配置表结构不完整']
    ids={}
    for table,fields in TABLES.items():
        rows=data[table]
        if not isinstance(rows,list): return [table+' 必须为列表']
        ids[table]=set()
        for row in rows:
            if not isinstance(row,dict) or set(row)-set(fields): return [table+' 存在未知字段']
            if any(not isinstance(v,str) or len(v)>4000 for v in row.values()): return [table+' 字段必须为文本且不超过 4000 字符']
            if not row.get('id') or row['id'] in ids[table]: errors.append(table+' 编号缺失或重复')
            ids[table].add(row.get('id'))
    if errors:return errors
    def ref(row,field,table):
        if row.get(field) not in ids[table]: errors.append(row['id']+'：'+field+' 关联不存在')
    for table in ['phases','versions','options']:
        for row in data[table]: ref(row,'model_id','models')
    names=set()
    for table in ['models','versions','options','phases','groups']:
        for row in data[table]:
            required={'models':['brand','series','generation','year','energy','status'],'groups':['name','scope','members']}.get(table,['name'])
            if publish:
                for field in required:
                    if not row.get(field,'').strip():errors.append(row['id']+'：缺少 '+field)
            if table in ['models','versions','options'] and row.get('status') not in ['在售','规划','停用']: errors.append(row['id']+'：状态无效')
            key=(table,row.get('model_id',''),row.get('category',''),row.get('name','')) if table!='models' else (table,*(row.get(f,'') for f in ['brand','series','generation','subgeneration','year']))
            if key in names:errors.append(row['id']+'：名称重复')
            names.add(key)
    versions={r['id']:r for r in data['versions']}; options={r['id']:r for r in data['options']}
    pairs=set()
    for row in data['matrix']:
        ref(row,'version_id','versions');ref(row,'option_id','options')
        pair=(row.get('version_id'),row.get('option_id'))
        if pair in pairs: errors.append('配置适用关系重复')
        pairs.add(pair)
        if row.get('state') not in STATES:errors.append('配置状态无效')
        if pair[0] in versions and pair[1] in options and versions[pair[0]]['model_id']!=options[pair[1]]['model_id']:errors.append('配置不可跨车型关联')
    aliases=set()
    for row in data['aliases']:
        key=(row.get('source','').strip().casefold(),row.get('name','').strip().casefold())
        if key in aliases:errors.append('名称映射冲突：'+row.get('name',''))
        aliases.add(key)
        if publish and (not key[0] or not key[1]):errors.append('名称映射缺少来源或名称')
        kind=row.get('target_type')
        if kind not in ['models','versions','options'] or row.get('target_id') not in ids.get(kind,set()):errors.append('名称映射未匹配：'+row.get('name',''))
    for table in ['phases','groups']:
        for row in data[table]:
            for field in [f for f in TABLES[table] if f.endswith('_date')]:
                if row.get(field):
                    try:datetime.strptime(row[field],'%Y-%m-%d')
                    except ValueError:errors.append(row['id']+'：日期格式应为 YYYY-MM-DD')
            a,b=('start_date','end_date') if table=='groups' else ('presale_date','sale_date')
            if row.get(a) and row.get(b) and row[a]>row[b]:errors.append(row['id']+'：日期顺序错误')
    membership=[]
    for row in data['groups']:
        for member in row.get('members','').split(','):
            member=member.strip()
            if member not in ids['models']:errors.append(row['id']+'：汇总成员必须是车型编号（不允许嵌套或循环）')
            for old,m in membership:
                if m==member and old.get('scope')==row.get('scope') and max(old.get('start_date') or '0000',row.get('start_date') or '0000')<=min(old.get('end_date') or '9999',row.get('end_date') or '9999'):errors.append(member+'：生效范围内重复归属')
            membership.append((row,member))
    return errors

def diff(old,new):
    out=[]
    for table in TABLES:
        before={r['id']:r for r in old[table]};after={r['id']:r for r in new[table]}
        for ident,row in after.items():
            if ident not in before:out.append({'table':table,'id':ident,'field':'新增','before':'','after':row})
            else:
                for field in TABLES[table]:
                    if before[ident].get(field,'')!=row.get(field,''):out.append({'table':table,'id':ident,'field':field,'before':before[ident].get(field,''),'after':row.get(field,'')})
        for ident in before.keys()-after.keys():out.append({'table':table,'id':ident,'field':'移除','before':before[ident],'after':''})
    return out

class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'catalog.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, revision INTEGER, data TEXT); CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT, actor TEXT, time TEXT, data TEXT, changes TEXT);')
        with self.db:
            for ident in [1,2]:self.db.execute('INSERT OR IGNORE INTO state VALUES (?,?,?)',(ident,0,json.dumps(empty())))
        self.project()
    def state(self,ident):
        row=self.db.execute('SELECT revision,data FROM state WHERE id=?',(ident,)).fetchone();return {'revision':row[0],'data':json.loads(row[1])}
    def get(self):
        with self.lock:return {'draft':self.state(1),'published':self.state(2),'history':[dict(zip(['id','kind','actor','time'],r)) for r in self.db.execute('SELECT id,kind,actor,time FROM events ORDER BY id DESC')], 'consumers':'尚未接入'}
    def history(self,ident):
        with self.lock:
            row=self.db.execute('SELECT data,changes FROM events WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('版本不存在')
            return {'data':json.loads(row[0]),'changes':json.loads(row[1])}
    def atomic(self,path,obj):
        path.parent.mkdir(exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)
    def project(self):
        self.atomic(self.root/'current.json',self.state(2))
        self.atomic(self.root/'drafts'/'current.json',self.state(1))
        for row in self.db.execute('SELECT id,kind,actor,time,data,changes FROM events'):
            path=self.root/'history'/f'{row[0]}.json'
            if not path.exists():self.atomic(path,dict(zip(['id','kind','actor','time','data','changes'],[ *row[:4],json.loads(row[4]),json.loads(row[5]) ])))
    def save(self,data,revision,actor,publish=False):
        with self.lock:
            draft=self.state(1);published=self.state(2)
            if revision!=draft['revision']:raise Conflict('草稿已被其他人修改，请导出当前编辑后重新载入')
            errors=validate(data,publish)
            # IDs survive edits. Missing rows must never silently delete historical objects.
            for table in TABLES:
                if {r['id'] for r in draft['data'][table]}-{r['id'] for r in data[table]}:errors.append(table+' 不允许删除记录，请停用或修改关系')
            if errors:raise ValueError('\n'.join(errors))
            changes=diff(published['data'] if publish else draft['data'],data)
            with self.db:
                self.db.execute('UPDATE state SET revision=revision+1,data=? WHERE id=1',(json.dumps(data,ensure_ascii=False),))
                if publish:self.db.execute('UPDATE state SET revision=revision+1,data=? WHERE id=2',(json.dumps(data,ensure_ascii=False),))
                self.db.execute('INSERT INTO events(kind,actor,time,data,changes) VALUES (?,?,?,?,?)',('发布' if publish else '草稿',actor,now(),json.dumps(data,ensure_ascii=False),json.dumps(changes,ensure_ascii=False)))
            self.project();return self.get()

def merge(base,incoming):
    result=copy.deepcopy(base)
    for table,rows in incoming.items():
        if table not in TABLES:raise ValueError('未知表：'+table)
        index={r['id']:r for r in result[table]};seen=set()
        for row in rows:
            ident=row.get('id') or str(uuid.uuid4());row={k:str(v) for k,v in row.items() if v is not None and str(v).strip()}
            if ident in seen:raise ValueError('导入编号重复：'+ident)
            seen.add(ident);row['id']=ident
            if ident in index:index[ident].update(row)
            else:result[table].append(row)
    return result
