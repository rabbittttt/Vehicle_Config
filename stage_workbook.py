"""Web-owned campaign schedules, daily orders and historical progress series."""
from bisect import bisect_right
from copy import deepcopy
from datetime import date, datetime, time
from io import BytesIO
import math
from pathlib import Path
from typing import NamedTuple
import re
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel

from xlsx_input import MAX_BYTES, WorkbookBatch


SUMMARY = '车型汇总'
DAILY = '小订by天'
LEGACY_PROGRESS = '小订进度'
GROSS = '大定进度'
CONVERTED = '小转大进度'
DIRECT = '直接大定进度'
PRIMARY = '代际名'
SECONDARY = '二级代际名'
SUMMARY_FIELDS = [PRIMARY, SECONDARY, '产品档位', '能源类型', '发布类型', '小订发布时段',
                  '首销发布时段', '留资', '小订开始日期', '小订结束日期', '开始大定日期',
                  '首销结束日期', '小订天数', '首销期天数', None, '总小订', '小订转大定量',
                  '小订转化率', '大定量', '直接大定量', '直接大定占比', '小订后退定', '小订后退定占比',
                  '小订待转单', '小订待转单占比', '首销期净大定', '净大定率', '首销期锁单',
                  '留存大定到锁单率', '大定到锁单率']
PROGRESS_FIELDS = {
    '小订期退订进度': ['总小订', '退订', '小订期期结束日退订率'],
    '小转大进度': ['总小订', '小订转大定', '小订转化率'],
    '直接大定进度': ['大定', '直接大定量', '直接大定占比'],
    '大定进度': ['大定', '直接大定量', '直接大定占比'],
    '首销期退订进度': ['总小订', '退订', '退订率', '首销期结束日退订率'],
}
PROGRESS_DATES = ['留资', '小订开始日期', '小订结束日期', '开始大定日期', '小转大结束日期']
DATE_FIELDS = set(PROGRESS_DATES + ['首销结束日期'])
RATIOS = {18:(17,16), 21:(20,19), 23:(22,16), 27:(26,16), 29:(28,26), 30:(28,19)}
COMPUTED = sorted({13, 14, 24, 25, *RATIOS})
RATE_FIELDS = {'小订转化率', '直接大定占比', '小订后退定占比', '小订待转单占比', '净大定率',
               '留存大定到锁单率', '大定到锁单率', '小订期期结束日退订率', '退订率', '首销期结束日退订率'}
MAX_COLUMNS = 1024
MAX_ROWS = 10000
MAX_CELLS = 1200000
SHEETS = [SUMMARY, DAILY, *PROGRESS_FIELDS]
IMPORT_ORDER = [SUMMARY, DAILY, LEGACY_PROGRESS, *PROGRESS_FIELDS]
SHEET_ALIASES = {'传播名汇总':SUMMARY, '小订按天':DAILY, '小订期退定进度':'小订期退订进度',
                 '小订转大定进度':'小转大进度', '总大定进度':'大定进度',
                 '首销期退定进度':'首销期退订进度'}
HEADER_ALIASES = {
    PRIMARY:('传播名','历史传播名','车型','车型名称'), SECONDARY:('二级代际',),
    '留资':('留资日期',), '小订发布时段':('小订发布时间',), '首销发布时段':('首销发布时间',),
    '小订开始日期':('小订开始','小订开启日期','小订发布日期','小订开始时间'), '小订结束日期':('小订结束','小订截止日期'),
    '开始大定日期':('大定开始日期','首销开始日期','发布日','上市日期'),
    '首销结束日期':('首销期结束日期','首销结束','首销截止日期','首销截止'),
    '小订天数':('小订期天数',), '首销期天数':('首销天数','首销周期天数'),
    '总小订':('小订总量','累计小订','总小订量'), '小订转大定量':('小订转大定','小转大','总小转大','小转大总量'),
    '小订转化率':('小转大率','小订转大定率','小转大转化率'), '大定量':('总大定','大定总量','首销期大定'),
    '直接大定量':('总直接大定','直接大定','直接大定总量'), '直接大定占比':('大定直接占比','直接大定比例'),
    '小订后退定':('小订后退订','退订','退订量','总退订'), '小订后退定占比':('小订后退订占比','退订率','退订占比'),
    '首销期净大定':('首销期留存大定','留存大定','净大定'), '净大定率':('留存大定率','首销期留存大定率','首销期净大定率'),
    '首销期锁单':('锁单量',)
}
ALIAS_TO_HEADER = {alias:field for field,aliases in HEADER_ALIASES.items() for alias in aliases}


def _sheet(name, rows):
    return {'name': name, 'rows': rows,
            'headerRows': [i for i,r in enumerate(rows, 1) if r and r[0] == PRIMARY],
            'computed': COMPUTED.copy() if name == SUMMARY else [], 'literalCells': []}


def empty_workbook():
    sheets = [_sheet(SUMMARY, [SUMMARY_FIELDS.copy()]), _sheet(DAILY, [[PRIMARY, '合计', SECONDARY]])]
    for name, metrics in PROGRESS_FIELDS.items():
        head = [PRIMARY]
        head += PROGRESS_DATES + ['小订天数', '首销期天数', None] + metrics
        head += [f'D{i}' for i in range(1, 71)] + [SECONDARY]
        blocks = 3 if name == '直接大定进度' else 2
        sheets.append(_sheet(name, [head.copy() for _ in range(blocks)]))
    return {'sheets': sheets}


def _get(row, index):
    return row[index] if 0 <= index < len(row) else None


def _put(row, index, value):
    if len(row) <= index:
        row.extend([None] * (index + 1 - len(row)))
    row[index] = value


def _trim(row):
    while row and row[-1] is None:
        row.pop()
    return row


def _date(value):
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError('日期须为 YYYY-MM-DD') from None


def _is_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        return bool(date.fromisoformat(value))
    except ValueError:
        return False


def _num(value, *, integer=False):
    if isinstance(value, bool):
        raise ValueError('请输入有效数字')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('请输入有效数字') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError('数字须为有限的非负值')
    if integer and not number.is_integer():
        raise ValueError('数量须为整数')
    return int(number) if number.is_integer() else number


def _text(value):
    if not isinstance(value, str) or len(value) > 1000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
        raise ValueError('文本格式无效或过长')
    return value.strip() or None


def _header(sheet, row_number):
    number = sheet['headerRows'][bisect_right(sheet['headerRows'], row_number) - 1]
    return sheet['rows'][number - 1]


def _key(row, secondary_index):
    return (_get(row,0), _get(row,secondary_index) or '')


def _detail_rows(sheet):
    """Legacy blocks use different secondary/date column coordinates in each header."""
    head = []
    for n,row in enumerate(sheet['rows'],1):
        primary = _get(row,0)
        if primary == PRIMARY:
            head = row
            secondary = head.index(SECONDARY)
        elif primary and primary != '合计':
            yield n,row,head,secondary


def _normalize_input(sheet, number, col, value):
    if value is None or value == '':
        return None
    title = _get(_header(sheet, number), col - 1)
    name = sheet['name']
    if title in (PRIMARY, SECONDARY) or (name == SUMMARY and col <= 7):
        return _text(value)
    if title in DATE_FIELDS:
        return _date(value)
    if title is None:
        raise ValueError('分隔列不可填写')
    is_progress_rate = (name in PROGRESS_FIELDS and isinstance(title, str) and re.fullmatch(r'D\d+', title)
                        and bisect_right(sheet['headerRows'], number) > 1)
    if title in RATE_FIELDS or is_progress_rate:
        if isinstance(value, str) and value.strip().endswith('%'):
            return _num(value.strip()[:-1])/100
        return _num(value)
    return _num(value, integer=True)


def _import_scalar(cell, field, epoch):
    value = cell.value
    if isinstance(value, datetime):
        return value.date().isoformat() if field in DATE_FIELDS or value.time() == time.min else value.isoformat()
    if isinstance(value, (time, date)):
        return value.isoformat()
    if field in DATE_FIELDS and isinstance(value, (float, int)) and not isinstance(value, bool):
        parsed = from_excel(value, epoch)
        if isinstance(parsed, datetime):
            return parsed.date().isoformat()
    if cell.data_type == 'e':
        raise ValueError(f'单元格 {cell.coordinate} 含 Excel 错误 {value}')
    if field in DATE_FIELDS and value not in (None,''):
        text = str(value).strip()
        if field == '小订开始日期' and re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:', text):
            return datetime.fromisoformat(text).date().isoformat()
        match = re.fullmatch(r'(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?',text)
        if match:
            return date(*map(int,match.groups())).isoformat()
        raise ValueError(f'{cell.coordinate} 无法识别日期 {value}')
    return value


def _sheet_sources(names):
    sources = {}
    unknown = []
    for raw in names:
        name = raw.strip()
        canonical = SHEET_ALIASES.get(name,name)
        if canonical not in IMPORT_ORDER:
            unknown.append(raw)
        elif canonical in sources:
            raise ValueError(f'{sources[canonical]} 与 {raw} 同时对应 {canonical}，请核对工作表')
        else:
            sources[canonical] = raw
    missing = [name for name in SHEETS if name != GROSS and name not in sources]
    issues = (['缺少工作表：'+'、'.join(missing)] if missing else []) + (['无法映射工作表：'+'、'.join(unknown)] if unknown else [])
    if issues:
        raise ValueError('；'.join(issues))
    return sources


def _summary_layout(cells):
    mapping = {}
    for j,cell in enumerate(cells):
        field = str(cell.value or '').strip()
        if not field:
            continue
        title = ALIAS_TO_HEADER.get(field,field)
        if title not in SUMMARY_FIELDS:
            raise ValueError(f'车型汇总 {cell.coordinate} 含未识别字段「{field}」，请核对表头')
        idx = SUMMARY_FIELDS.index(title)
        if idx in mapping.values() and title != '小订开始日期':
            raise ValueError(f'车型汇总 {cell.coordinate} 重复对应字段「{title}」')
        mapping[j] = idx
    if 0 not in mapping.values():
        raise ValueError('车型汇总缺少代际名或车型名称')
    return mapping


def _cached(cell, cache_cell, sheet, numeric):
    if cell.data_type != 'f':
        if cell.data_type == 'e':
            raise ValueError(f'{sheet} {cell.coordinate} 含 Excel 错误 {cell.value}')
        return cell
    if not numeric:
        raise ValueError(f'{sheet} {cell.coordinate} 的公式位于未识别的数值或日期字段，请核对')
    if cache_cell.value is None or cache_cell.data_type == 'e':
        raise ValueError(f'{sheet} {cell.coordinate} 缺少有效的公式结果，请先在 Excel 中重算、保存并核对该单元格')
    if not isinstance(cache_cell.value,(int,float,date,time)) or isinstance(cache_cell.value,bool):
        raise ValueError(f'{sheet} {cell.coordinate} 的公式缓存不是数值或日期，请先核对')
    return cache_cell


def _matches_canonical_formula(formula, col, n, mapping):
    refs = {idx+1:get_column_letter(source+1)+str(n) for source,idx in mapping.items()}
    try:
        if col in RATIOS:
            a,b = RATIOS[col]
            return formula == f'={refs[a]}/{refs[b]}'
        if col in (13,14):
            a,b = (9,10) if col==13 else (11,12)
            return any(formula == f'={refs[b]}-{get_column_letter(j+1)}{n}+1'
                       for j,idx in mapping.items() if idx == a-1)
        if col==24:
            return formula == f'=MAX({refs[16]}-{refs[17]}-{refs[22]},0)'
        if col==25:
            return formula == f'=IFERROR({refs[24]}/{refs[16]},0)'
    except KeyError:
        return False
    return False


def _summary_row(cells, caches, mapping, n, epoch):
    row = [None] * len(SUMMARY_FIELDS)
    override = set()
    for j,cell in enumerate(cells):
        if cell.value is None:
            continue
        if j not in mapping:
            raise ValueError(f'车型汇总 {cell.coordinate} 所在列有数据但缺少表头，请核对')
        idx = mapping[j]
        field = SUMMARY_FIELDS[idx]
        resolved = _cached(cell,caches[j],SUMMARY,field in DATE_FIELDS or idx>=12)
        try:
            value = _import_scalar(resolved,field,epoch)
            if field in (PRIMARY,SECONDARY) and value is not None:
                value = _text(value)
            if idx>=12 and value is not None:
                if field in RATE_FIELDS and isinstance(value,str) and value.strip().endswith('%'):
                    value = _num(value.strip()[:-1])/100
                else:
                    value = _num(value,integer=field not in RATE_FIELDS)
            if field == '小订开始日期':
                if value in (None, ''):
                    continue
                if row[idx] is not None and value != row[idx]:
                    raise ValueError('与同一行的另一列小订开始日期不一致')
            row[idx] = value
        except (TypeError,ValueError,OverflowError) as exc:
            raise ValueError(f'车型汇总 {cell.coordinate}「{field}」：{exc}') from exc
        if idx+1 in COMPUTED and cell.data_type=='f':
            formula = str(cell.value).upper().replace('$','').replace(' ','')
            if not _matches_canonical_formula(formula,idx+1,n,mapping):
                override.add(idx+1)
        elif idx+1 in (24,25,30):
            override.add(idx+1)
    for col,(a,b) in RATIOS.items():
        value,numerator,denominator = (row[i-1] for i in (col,a,b))
        if value is not None and (not denominator or numerator is None or
                                 not math.isclose(value,numerator/denominator,rel_tol=1e-9,abs_tol=1e-12)):
            override.add(col)
    for a,b,col in ((9,10,13),(11,12,14)):
        expected = _days(row[a-1],row[b-1])
        if col-1 not in mapping.values():
            row[col-1] = expected
        elif row[col-1] is not None and row[col-1]!=expected:
            override.add(col)
    return _trim(row),[f'{n}:{col}' for col in sorted(override)]


def _detail_head(name,cells,epoch):
    head = []
    for j,cell in enumerate(cells):
        value = _import_scalar(cell,None,epoch)
        if isinstance(value,str):
            value = value.strip() or None
        if j==0:
            value = PRIMARY
        elif value in ('二级代际',SECONDARY):
            value = SECONDARY
        elif name==DAILY:
            if value in ('总计','合计'):
                value = '合计'
            elif value is not None:
                value = _import_scalar(cell,'留资',epoch)
                if not _is_date(value):
                    raise ValueError(f'{cell.coordinate} 的日期列表头无法识别')
        elif value is not None:
            day = re.fullmatch(r'(?:[dD]|第)?0*(\d+)天?',str(value))
            if day and int(day[1])>0:
                value = 'D'+str(int(day[1]))
            elif value not in PROGRESS_DATES+PROGRESS_FIELDS.get(name,[])+['小订天数','首销期天数']:
                alias = ALIAS_TO_HEADER.get(value,value)
                if alias not in PROGRESS_DATES+PROGRESS_FIELDS.get(name,[])+['小订天数','首销期天数']:
                    raise ValueError(f'{cell.coordinate} 的进度字段「{value}」无法识别')
                value = alias
        if value is not None and value in head:
            raise ValueError(f'{cell.coordinate} 重复对应「{value}」')
        head.append(value)
    _trim(head)
    add_total = name==DAILY and '合计' not in head
    if add_total:
        head.append('合计')
    if SECONDARY not in head:
        head.append(SECONDARY)
    return head,add_total


def _read_detail(name,cells,caches,headers,epoch):
    row = []
    for j,(cell,cache_cell) in enumerate(zip(cells,caches)):
        field = _get(headers,j)
        numeric = field not in (None,PRIMARY,SECONDARY)
        cell = _cached(cell,cache_cell,name,numeric)
        value = _import_scalar(cell,field,epoch)
        if field in (PRIMARY,SECONDARY) and value is not None:
            try:
                value = _text(value)
            except ValueError as exc:
                raise ValueError(f'{cell.coordinate} 的代际名称无效') from exc
        if value is not None and field is None and _get(cells,0).value:
            raise ValueError(f'{cell.coordinate} 所在列有数据但缺少字段名')
        if numeric and value is not None and field not in DATE_FIELDS and cells[0].value not in ('合计','总计',None):
            if isinstance(value,str) and value.endswith('%'):
                value = _num(value[:-1])/100
            else:
                value = _num(value)
        row.append(value)
    if _get(row,0)=='总计':
        row[0] = '合计'
    return _trim(row)


def import_workbook(path_or_bytes):
    """Explicit migration only. No worker reads this source after import."""
    if isinstance(path_or_bytes, bytes):
        raw = path_or_bytes
    else:
        path = Path(path_or_bytes)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('历史文件超过 8MB')
        raw = path.read_bytes()
    try:
        book = WorkbookBatch([{}]).open(raw)
    except (BadZipFile, KeyError, SyntaxError, OSError) as exc:
        raise ValueError('无法读取历史 XLSX 工作簿') from exc
    cached = None
    try:
        sources = _sheet_sources(book.sheetnames)
        cached = load_workbook(BytesIO(raw), data_only=True, read_only=True, keep_links=False)
        result = []
        footprint = 0
        for name in IMPORT_ORDER:
            if name not in sources:
                continue
            ws = book[sources[name]]
            footprint += (ws.max_row or 0) * (ws.max_column or 0)
            if not ws.max_row or not ws.max_column or ws.max_row > MAX_ROWS or ws.max_column > MAX_COLUMNS or footprint > MAX_CELLS:
                raise ValueError(name + '的数据范围超出限制')
            rows, overrides, headers = [], [], []
            for n,(cells, cache_cells) in enumerate(zip(ws.rows, cached[sources[name]].rows), 1):
                if name==SUMMARY:
                    if n==1:
                        mapping = _summary_layout(cells)
                        rows.append(SUMMARY_FIELDS.copy())
                        continue
                    row,special = _summary_row(cells,cache_cells,mapping,n,book.epoch)
                    rows.append(row)
                    overrides.extend(special)
                    continue
                raw_label = cells[0].value
                label = raw_label.strip() if isinstance(raw_label,str) else raw_label
                is_head = label in (PRIMARY,*HEADER_ALIASES[PRIMARY]) or (label=='小订' and name==DAILY)
                try:
                    if is_head:
                        headers,add_total = _detail_head(name,cells,book.epoch)
                        width = max((j+1 for j,c in enumerate(cells) if c.value is not None),default=0)
                        rows.append(headers)
                    elif not headers:
                        raise ValueError('第一行须包含代际名或车型表头')
                    else:
                        row = _read_detail(name,cells,cache_cells,headers[:width],book.epoch)
                        if add_total and _get(row,0) not in ('合计',None):
                            _daily_total(row,headers)
                        rows.append(row)
                except (ValueError,TypeError,OverflowError) as exc:
                    raise ValueError(f'{name} 第 {n} 行：{exc}') from exc
            while rows and not rows[-1]:
                rows.pop()
            if not rows or not rows[0] or rows[0][0] != PRIMARY:
                raise ValueError(f'{name} 缺少代际名表头')
            sheet = _sheet(name, rows)
            sheet['literalCells'] = overrides
            for idx in sheet['headerRows']:
                head = rows[idx - 1]
                if name != SUMMARY and (SECONDARY not in head or
                                        (name == DAILY and '合计' not in head) or
                                        (name in PROGRESS_FIELDS and 'D1' not in head)):
                    raise ValueError(f'{name} 第 {idx} 行的表头无法识别')
            if name in PROGRESS_FIELDS:
                count = 3 if name=='直接大定进度' else 2
                if len(sheet['headerRows'])>count:
                    raise ValueError(f'{name} 有 {len(sheet["headerRows"])} 个分区，额外分区的业务含义无法识别')
                while len(sheet['headerRows'])<count:
                    rows.append(rows[0].copy())
                    sheet['headerRows'].append(len(rows))
            result.append(sheet)
        keys = {_key(row,1) for row in result[0]['rows'][1:]}
        for sheet in result[1:]:
            historic = {_key(row,idx) for _,row,_,idx in _detail_rows(sheet)} - keys
            if historic:
                sheet['historicalIdentities'] = [list(key) for key in sorted(historic)]
            if sheet['name'] == DAILY:
                footers = [n for n,row in enumerate(sheet['rows'],1) if _get(row,0)=='合计'
                           and any(isinstance(v,(int,float)) for v in row[1:])]
                if footers:
                    sheet['totalRows'] = footers
        data = backfill_totals({'sheets': result})
        _mark_derived({s['name']:s for s in data['sheets']})
        return data
    finally:
        book.close()
        if cached:
            cached.close()


def _days(start, end):
    if not start or not end:
        return None
    count = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return count


def _summary_calculate(sheet, n, changed):
    row = sheet['rows'][n-1]
    for a,b,col in ((9,10,13), (11,12,14)):
        if {a,b} & changed and f'{n}:{col}' not in sheet['literalCells']:
            _put(row, col - 1, _days(_get(row,a-1), _get(row,b-1)))
    def derive(col, inputs, calc):
        if not changed.intersection(inputs) or f'{n}:{col}' in sheet['literalCells']:
            return
        values = [_get(row,i-1) for i in inputs]
        _put(row, col-1, calc(*values) if all(isinstance(v,(int,float)) for v in values) else None)
        changed.add(col)
    derive(24, [16,17,22], lambda total, converted, cancelled: max(total-converted-cancelled,0))
    derive(25, [24,16], lambda pending,total: pending/total if total else 0)
    for col,(numerator,denominator) in RATIOS.items():
        derive(col, [numerator,denominator], lambda amount,total: amount/total if total else None)


def _locations(sheet, primary, secondary, section=None):
    rows = sheet['rows']
    for i,start in enumerate(sheet['headerRows']):
        if section is not None and i + 1 != section:
            continue
        header = rows[start - 1]
        secondary_idx = header.index(SECONDARY)
        end = sheet['headerRows'][i+1]-1 if i+1<len(sheet['headerRows']) else len(rows)
        for j in range(start,end):
            if _get(rows[j],0) == primary and (_get(rows[j],secondary_idx) or '') == secondary:
                yield j, start, end


def _refresh_headers(sheet):
    sheet['headerRows'] = [n for n,row in enumerate(sheet['rows'],1) if _get(row,0) == PRIMARY]


def _add_axis(sheet, start, end, label):
    rows = sheet['rows']
    head = rows[start-1]
    if label in head:
        return head.index(label)
    target = '合计' if sheet['name'] == DAILY else SECONDARY
    idx = head.index(target)
    if sheet['name'] == DAILY:
        idx = next((j for j,v in enumerate(head[:idx]) if _is_date(v) and v > label), idx)
    elif label.startswith('D'):
        idx = next((j for j,v in enumerate(head[:idx]) if isinstance(v,str) and re.fullmatch(r'D\d+',v) and int(v[1:])>int(label[1:])),idx)
    if len(head) >= MAX_COLUMNS:
        raise ValueError('日期或进度列超过上限')
    for row in rows[start-1:end]:
        if len(row) > idx:
            row.insert(idx,None)
    head[idx] = label
    return idx


def _daily_total(row, header):
    idx = header.index('合计')
    values = [_get(row,j) for j,v in enumerate(header) if _is_date(v)]
    numbers = [v for v in values if isinstance(v,(int,float))]
    _put(row,idx,sum(numbers) if numbers else None)


def _block_totals(sheet, n):
    rows = sheet['rows']
    section = bisect_right(sheet['headerRows'],n)
    start = sheet['headerRows'][section-1]
    end = sheet['headerRows'][section] - 1 if section < len(sheet['headerRows']) else len(rows)
    head = rows[start-1]
    for total_number,total in enumerate(rows[start:end],start+1):
        if _get(total,0) != '合计':
            continue
        if total_number not in sheet.get('totalRows',[]):
            if any(isinstance(v,(int,float)) for v in total[1:]):
                sheet.setdefault('totalRows',[]).append(total_number)
            else:
                continue  # Blank footer in original forms is a visual separator.
        for idx,title in enumerate(head):
            if _is_date(title) or title == '合计':
                vals = [_get(r,idx) for r in rows[start:end] if _get(r,0) not in (None,'合计')]
                # A total is known only once every contributing model has an entry.
                _put(total,idx,sum(vals) if vals and all(isinstance(v,(int,float)) for v in vals) else None)


# One-based columns of the review sheet backed by reported quantities.
PROGRESS_TOTALS = {'小转大进度':17, '大定进度':19, '直接大定进度':20}
CANCEL_SHEETS = ('小订期退订进度', '首销期退订进度')
BACKFILLED = {16, 22, *PROGRESS_TOTALS.values()}


def _quantity_records(sheet):
    """Only one physical quantity row may own a total for each stage."""
    records = {}
    for n,row,head,secondary in _detail_rows(sheet):
        if sheet['name'] != DAILY and len(sheet['headerRows']) > 1 and n >= sheet['headerRows'][1]:
            break
        key = _key(row,secondary)
        records[key] = None if key in records else (head,row)
    return records


def _whole_count(value):
    return type(value) is int and value >= 0 or type(value) is float and value >= 0 and value.is_integer()


def _daily_sum(record, stage):
    if record is None:
        return None
    head,row = record
    columns = [(day, _get(row,j)) for j,day in enumerate(head) if _is_date(day)]
    filled = {day:amount for day,amount in columns if amount is not None}
    if not filled or not all(_whole_count(amount) for amount in filled.values()):
        return None
    first,last = _get(stage,8),_get(stage,9)
    if first and last:
        complete = min(filled)==first and max(filled)==last and len(filled)==_days(first,last)
    else:
        complete = not first and not last and len(filled)==len(columns)
    return sum(filled.values()) if complete else None


class _ProgressQuantity(NamedTuple):
    start: str | None
    end: str | None
    quantities: dict
    complete: bool
    reported_cancellations: int | None


def _progress_observation(record, stage, name):
    if record is None:
        return None
    head,row = record
    fields = dict(zip(head,row))
    small = name == CANCEL_SHEETS[0]
    first = fields.get('小订开始日期' if small else '开始大定日期') or _get(stage,8 if small else 10)
    last = fields.get('小订结束日期' if small else '小转大结束日期') or _get(stage,9 if small else 11)
    length = _days(first,last)
    quantities = {int(k[1:]):v for k,v in fields.items()
                  if v is not None and isinstance(k,str) and re.fullmatch(r'D[1-9]\d*',k)}
    complete = bool(quantities and length == len(quantities) and max(quantities) == length
                    and all(_whole_count(amount) for amount in quantities.values()))
    return _ProgressQuantity(first,last,quantities,complete,fields.get('退订'))


def _cancel_sum(small, launch):
    if small is None or launch is None:
        return None
    # Older workbooks often repeat the same overall cancellation count in both
    # progress tables. Do not add it twice or trust a stale copied total.
    if small.reported_cancellations is not None or launch.reported_cancellations is not None:
        reported = small.reported_cancellations
        if not _whole_count(reported) or reported != launch.reported_cancellations:
            return None
        for obs in (small,launch):
            counted = sum(obs.quantities.values())
            if (not all(_whole_count(amount) for amount in obs.quantities.values())
                    or counted > reported or (obs.complete and counted != reported)):
                return None
        return reported
    if small.complete and launch.complete and small == launch:
        return sum(small.quantities.values())
    return None


def _observed_totals(sheets):
    sources = {name:_quantity_records(sheets[name]) for name in (DAILY,*PROGRESS_TOTALS,*CANCEL_SHEETS)}
    stages = {}
    for n,row in enumerate(sheets[SUMMARY]['rows'][1:],2):
        key = _key(row,1)
        if key[0]:
            stages[key] = None if key in stages else (n,row)
    totals = {}
    for key,stage in stages.items():
        if stage is None:
            continue
        n,row = stage
        amounts = {16:_daily_sum(sources[DAILY].get(key),row)}
        for name,col in PROGRESS_TOTALS.items():
            observation = _progress_observation(sources[name].get(key),row,name)
            if (observation and observation.complete and
                    (observation.start, observation.end) == (_get(row,10), _get(row,11))):
                amounts[col] = sum(observation.quantities.values())
        amounts[22] = _cancel_sum(*(_progress_observation(sources[name].get(key),row,name) for name in CANCEL_SHEETS))
        totals[n] = {col:value for col,value in amounts.items() if value is not None}
    return totals


def _reconcile_totals(sheets, previous, manual):
    summary = sheets[SUMMARY]
    current = _observed_totals(sheets)
    if previous is None:
        previous = current  # On initial backfill, only empty summary cells can change.
    for n in previous.keys() | current.keys():
        row = summary['rows'][n-1]
        changed = set()
        before,after = previous.get(n,{}),current.get(n,{})
        for col in before.keys() | after.keys():
            coord = f'{n}:{col}'
            value = _get(row,col-1)
            if coord in manual or (value is not None and value != before.get(col)):
                # Preserve an independent total even if a later detail edit happens to match it.
                if value is not None and value == after.get(col) and value != before.get(col):
                    manual.add(coord)
                continue
            if value != after.get(col):
                _put(row,col-1,after.get(col))
                changed.add(col)
        _summary_calculate(summary,n,changed)
    if manual:
        summary['reportedTotals'] = sorted(manual)
    else:
        summary.pop('reportedTotals',None)


def backfill_totals(data):
    """Fill previously empty draft totals without changing a published edition."""
    sheets = {s['name']:s for s in data['sheets']}
    if GROSS not in sheets:
        progress = next(sheet for sheet in empty_workbook()['sheets'] if sheet['name'] == GROSS)
        data['sheets'].insert(data['sheets'].index(sheets[DIRECT])+1,progress)
        sheets[GROSS] = progress
    summary = sheets[SUMMARY]
    before = _summary_refs(summary)
    curves = {}
    _sync_gross(sheets,curves,identify_reported=True)
    _reconcile_totals(sheets,None,set(summary.get('reportedTotals',[])))
    _refresh_progress_totals(sheets,before,curves)
    # Old quantity rows may have had no corresponding progress curve. Fill
    # only missing rates; independently recorded history remains in its cells.
    for name,section in ((GROSS,2),(DIRECT,3)):
        for key,row in _quantity_records(sheets[name]).items():
            if row is None:
                continue
            actual = _section_record(sheets[name],key,section)
            ratios = _ratios(sheets,name,key,section)
            missing = [day for day in ratios if actual.get(f'D{day}') is None]
            if missing:
                prior = curves.setdefault((name,section,key),ratios.copy())
                for day in missing:
                    prior.pop(day,None)
    if curves:
        _finish_curves(sheets,curves)
        _mark_derived(sheets)
    return data


STAGE_FIELDS = {8:'留资', 9:'小订开始日期', 10:'小订结束日期', 11:'开始大定日期',
                13:'小订天数', 14:'首销期天数'}
TOTAL_FIELDS = {16:'总小订', 17:'小订转大定', 18:'小订转化率', 19:'大定', 20:'直接大定量', 21:'直接大定占比'}


def _prefill_progress(sheets, sheet, header, key, row):
    matches = [r for r in sheets[SUMMARY]['rows'][1:] if _key(r,1)==key]
    if len(matches) != 1:
        return
    for col,field in {**STAGE_FIELDS,**TOTAL_FIELDS}.items():
        if field in header:
            _put(row, header.index(field), _get(matches[0],col-1))


def _series_edit(sheets, sheet, edit):
    rows = sheet['rows']
    primary = _text(edit.get('model'))
    if not primary or primary in (PRIMARY, '合计'):
        raise ValueError('请选择有效车型')
    secondary = _text(edit.get('secondary') or '') or ''
    daily = sheet['name'] == DAILY
    section = None if daily else edit.get('section',1)
    if not daily and (type(section) is not int or not 1 <= section <= len(sheet['headerRows'])):
        raise ValueError('请选择有效的进度分区')
    if daily:
        label = _date(edit.get('date'))
    else:
        day = edit.get('day')
        if type(day) is not int or not 1 <= day <= MAX_COLUMNS-20:
            raise ValueError('进度天数超出有效范围')
        label = f'D{day}'
    found = list(_locations(sheet,primary,secondary,section))
    if len(found)>1:
        raise ValueError('车型和二级代际匹配到多行，请在表格里定位具体记录')
    if not found and edit.get('value') in ('', None):
        return
    if found:
        j,start,end = found[0]
    elif daily:
        if len(rows)==1 and len(rows[0])==3:
            start = 1
        else:
            start = len(rows)+1
            rows.append([PRIMARY,'合计',SECONDARY])
        rows.append([primary,None,secondary or None])
        j, end = len(rows)-1,len(rows)
    else:
        start = sheet['headerRows'][section-1]
        end = sheet['headerRows'][section]-1 if section<len(sheet['headerRows']) else len(rows)
        j = end
        while j>start and not _get(rows[j-1],0):
            j -= 1
        row = [primary]
        head = rows[start-1]
        _put(row,head.index(SECONDARY),secondary or None)
        _prefill_progress(sheets,sheet,head,(primary,secondary),row)
        rows.insert(j,row)
        end += 1
    _refresh_headers(sheet)
    col = _add_axis(sheet,start,end,label)
    _put(rows[j],col,_normalize_input(sheet,j+1,col+1,edit['value']))
    if daily:
        _daily_total(rows[j],rows[start-1])
        _block_totals(sheet,j+1)


def _update_dependents(sheets, source, n, before):
    after = source['rows'][n-1]
    old_key, new_key = _key(before,1), _key(after,1)
    if not old_key[0]:
        return
    renaming = old_key != new_key
    # Some historical rows record a different rule/window. Only replace dates that
    # were explicitly the same. 小转大结束日期 is an independent observation.
    updates = {field:(before_value,_get(after,k-1)) for k,field in STAGE_FIELDS.items()
               if (before_value := _get(before,k-1)) != _get(after,k-1)}
    if not renaming and not updates:
        return
    matches = [(row,head,idx) for sheet in sheets.values() if sheet is not source
               for _,row,head,idx in _detail_rows(sheet) if _key(row,idx) == old_key]
    if not matches:
        return
    competing_keys = {_key(row,1) for j,row in enumerate(source['rows'][1:],2) if j != n}
    if old_key in competing_keys or (renaming and (not new_key[0] or new_key in competing_keys)):
        raise ValueError('该车型已有每日记录，无法清空或改为重复的代际与二级代际')
    for row,head,idx in matches:
        if renaming:
            _put(row,0,new_key[0])
            _put(row,idx,new_key[1] or None)
        for field,(old,new) in updates.items():
            if field in head:
                position = head.index(field)
                if _get(row,position) == old:
                    _put(row,position,new)


DENOMINATORS = {'小订期退订进度':'总小订', '小转大进度':'小订转大定',
                '直接大定进度':'直接大定量', '大定进度':'大定', '首销期退订进度':'总小订'}


def _section_record(sheet, key, section):
    found = list(_locations(sheet,*key,section))
    if len(found) == 1:
        j,start,_ = found[0]
        return dict(zip(sheet['rows'][start-1],sheet['rows'][j]))
    return {}


def _day_offset(start, reference):
    # D1 belongs to the row's own launch date. Two undated rows share the
    # stage-relative axis; a dated row cannot be joined to an undated one.
    if start == reference:
        return 0
    if start and reference:
        return (date.fromisoformat(start) - date.fromisoformat(reference)).days
    return None


def _stage_starts(sheets):
    return {_key(row,1):_get(row,10) for row in sheets[SUMMARY]['rows'][1:]}


def _sync_gross(sheets, pending, *, identify_reported=False):
    """Materialize daily gross from matched stage/dates, retaining independent imported observations."""
    gross = sheets[GROSS]
    records = [_quantity_records(sheets[name]) for name in (CONVERTED,DIRECT,GROSS)]
    stages = _stage_starts(sheets)
    reported = set(gross.get('reportedQuantities',[]))
    # Positions in this block remain stable when new stages/days are appended;
    # day labels, unlike physical column numbers, survive axis expansion.
    row_numbers = {_key(row,idx):n for n,row,_,idx in _detail_rows(gross)
                   if n < gross['headerRows'][1]}
    for key in sorted(set(records[2]) | (records[0].keys() & records[1].keys())):
        if key in records[2] and records[2][key] is None:
            continue  # Ambiguous historical stage; never overwrite either record.
        converted,direct,current = [dict(zip(*records_by_key[key])) if records_by_key.get(key) else {}
                                    for records_by_key in records]
        start = stages.get(key)
        axis = current.get('开始大定日期') or start
        if not axis:
            anchors = [source.get('开始大定日期') for source in (converted,direct)]
            if all(anchors) and (not current or anchors[0] == anchors[1]):
                axis = min(anchors)
        aligned = []
        for source in (converted,direct):
            offset = _day_offset(source.get('开始大定日期') or start,axis)
            aligned.append({int(field[1:])+offset:value for field,value in source.items()
                            if offset is not None and _whole_count(value) and re.fullmatch(r'D[1-9]\d*',str(field))})
        combined = {day:aligned[0][day]+aligned[1][day] for day in aligned[0].keys() & aligned[1].keys()
                    if 1 <= day <= MAX_COLUMNS-20}
        existing = {int(k[1:]):v for k,v in current.items() if v is not None and re.fullmatch(r'D[1-9]\d*',str(k))}
        for day in sorted(combined.keys() | existing.keys()):
            old, new = existing.get(day), combined.get(day)
            if old == new:
                continue
            coord = f'{row_numbers.get(key)}:D{day}'
            if identify_reported and old is not None:
                reported.add(coord)
            if coord in reported:
                continue
            _remember_curves(sheets,GROSS,key,pending)
            _series_edit(sheets,gross,dict(model=key[0],secondary=key[1],day=day,value=new))
            if not current.get('开始大定日期') and axis and not start:
                index,first,_ = next(_locations(gross,*key,1))
                _put(gross['rows'][index],gross['rows'][first-1].index('开始大定日期'),axis)
                current['开始大定日期'] = axis
        if key not in stages and combined and list(key) not in gross.get('historicalIdentities',[]):
            gross.setdefault('historicalIdentities',[]).append(list(key))
    if reported:
        gross['reportedQuantities'] = sorted(reported)


def _ratios(sheets, name, key, section):
    source = _section_record(sheets[name],key,1)
    days = sorted(int(h[1:]) for h in source if isinstance(h,str) and re.fullmatch(r'D\d+',h))
    if section == 3:
        totals = _section_record(sheets['大定进度'],key,1)
        stage_start = _stage_starts(sheets).get(key)
        offset = _day_offset(source.get('开始大定日期') or stage_start,totals.get('开始大定日期') or stage_start)
    else:
        denominator = source.get(DENOMINATORS[name])
    result, cumulative, contiguous = {}, 0, True
    for index,day in enumerate(days,1):
        field = f'D{day}'
        qty = source.get(field)
        if day != index or not isinstance(qty,(int,float)):
            contiguous = False
        if not isinstance(qty,(int,float)):
            continue
        cumulative += qty
        if section == 3:
            denominator = totals.get(f'D{day+offset}') if offset is not None else None
        if isinstance(denominator,(int,float)) and denominator > 0:
            if section == 3:
                result[day] = qty/denominator
            elif contiguous:
                result[day] = cumulative/denominator
    return result


def _curve_targets(name):
    yield name,2
    if name in ('直接大定进度','大定进度'):
        yield '直接大定进度',3


def _remember_curves(sheets,name,key,pending):
    for target,section in _curve_targets(name):
        coord = target,section,key
        if coord not in pending:
            pending[coord] = _ratios(sheets,target,key,section)


def _finish_curves(sheets,pending):
    for (target,section,key),before in pending.items():
        current = _ratios(sheets,target,key,section)
        sheet = sheets[target]
        if len(list(_locations(sheet,*key,section))) > 1:
            continue  # Ambiguous imported ratio rows retain their original values.
        recorded = _section_record(sheet,key,section)
        for day in sorted(current.keys() | before.keys()):
            old,new = before.get(day),current.get(day)
            if old == new:
                continue
            actual = recorded.get(f'D{day}')
            if actual is not None and (old is None or not math.isclose(old,actual,rel_tol=1e-9,abs_tol=1e-12)):
                continue  # Keep an independent historical ratio.
            _series_edit(sheets,sheet,{'model':key[0],'secondary':key[1],'section':section,'day':day,'value':new})


def _summary_refs(summary):
    return {n:tuple(_get(row,col-1) for col in TOTAL_FIELDS)
            for n,row in enumerate(summary['rows'][1:],2)}


def _refresh_progress_totals(sheets,before,curves):
    # Rates may be imported independently. Change a reference only when it was
    # blank or still agreed with the summary before this group of edits.
    changes = {}
    seen = set()
    for n,row in enumerate(sheets[SUMMARY]['rows'][1:],2):
        key = _key(row,1)
        if key in seen:
            changes.pop(key,None)
            continue
        seen.add(key)
        old = before.get(n,(None,)*len(TOTAL_FIELDS))
        fields = {field:(prior,_get(row,col-1)) for (col,field),prior in zip(TOTAL_FIELDS.items(),old)
                  if prior != _get(row,col-1)}
        if fields:
            changes[key] = fields
    if not changes:
        return
    for name,denominator in DENOMINATORS.items():
        progress = sheets[name]
        for n,row,head,idx in _detail_rows(progress):
            key = _key(row,idx)
            for field,(old,new) in changes.get(key,{}).items():
                if field not in head:
                    continue
                col = head.index(field)
                actual = _get(row,col)
                if actual == new or (actual is not None and actual != old):
                    continue
                if field == denominator and bisect_right(progress['headerRows'],n) == 1:
                    _remember_curves(sheets,name,key,curves)
                _put(row,col,new)


def _mark_derived(sheets):
    for name in PROGRESS_FIELDS:
        sheet = sheets[name]
        cells = []
        for n,row,head,idx in _detail_rows(sheet):
            section = bisect_right(sheet['headerRows'],n)
            if section == 1:
                continue
            ratios = _ratios(sheets,name,_key(row,idx),section)
            for j,field in enumerate(head):
                if not isinstance(field,str) or not re.fullmatch(r'D\d+',field):
                    continue
                expected,actual = ratios.get(int(field[1:])),_get(row,j)
                if isinstance(expected,(int,float)) and isinstance(actual,(int,float)) and math.isclose(expected,actual,rel_tol=1e-9,abs_tol=1e-12):
                    cells.append(f'{n}:{j+1}')
        if cells:
            sheet['derivedCells'] = cells
        else:
            sheet.pop('derivedCells',None)


def _progress_before(sheets, name, key, section, field, pending):
    if name not in PROGRESS_FIELDS or not key[0]:
        return None
    if section == 1:
        _remember_curves(sheets,name,key,pending)
        return
    if isinstance(field,str) and re.fullmatch(r'D\d+',field):
        expected = _ratios(sheets,name,key,section).get(int(field[1:]))
        actual = _section_record(sheets[name],key,section).get(field)
        if expected is not None and (actual is None or math.isclose(expected,actual,rel_tol=1e-9,abs_tol=1e-12)):
            raise ValueError('该进度比例由已填写的数量计算')
    return None


def apply_edits(data, edits):
    if not isinstance(edits,list) or len(edits)>20000:
        raise ValueError('单次最多修改 20000 个单元格')
    result = backfill_totals(deepcopy(data))
    if not edits:
        return result
    sheets = {s['name']:s for s in result['sheets']}
    previous = _observed_totals(sheets)
    before_refs = _summary_refs(sheets[SUMMARY])
    # Grid pastes address quantity rows by stage/day. Recalculate their curves
    # once per stage, while mixed/raw edits keep their original order.
    defer = all(isinstance(edit,dict) and 'model' in edit and edit.get('section',1)==1 for edit in edits)
    pending = {}
    manual = set(sheets[SUMMARY].get('reportedTotals',[]))
    for edit in edits:
        if not isinstance(edit,dict) or not isinstance(edit.get('sheet'),str) or edit['sheet'] not in sheets or 'value' not in edit:
            raise ValueError('修改记录缺少表名或值')
        sheet = sheets[edit['sheet']]
        if sheet['name'] == GROSS:
            raise ValueError('大定进度由小转大与直接大定数量计算，不可手工修改')
        if 'model' in edit:
            if sheet['name'] == SUMMARY:
                raise ValueError('车型汇总使用行和列定位')
            key = (_text(edit.get('model')),_text(edit.get('secondary') or '') or '')
            curves = pending if defer else {}
            _progress_before(sheets,sheet['name'],key,edit.get('section',1),f'D{edit.get("day")}',curves)
            _series_edit(sheets,sheet,edit)
            if not defer:
                _finish_curves(sheets,curves)
            continue
        n,col = edit.get('row'),edit.get('col')
        rows = sheet['rows']
        if type(n) is not int or type(col) is not int or not 2 <= n <= min(MAX_ROWS,len(rows)+1):
            raise ValueError('无效行列')
        if n in sheet['headerRows']:
            raise ValueError('表头不可直接修改')
        header = _header(sheet,n)
        if not 1 <= col <= len(header) or header[col-1] is None:
            raise ValueError('请选择有字段名的数据列')
        if (col in sheet['computed'] and f'{n}:{col}' not in sheet['literalCells']) or (sheet['name']==DAILY and header[col-1]=='合计'):
            raise ValueError('此项由系统计算')
        if n == len(rows)+1:
            rows.append([])
        if _get(rows[n-1],0) == '合计':
            raise ValueError('合计行不可填写')
        if col==1 and edit['value'] in (PRIMARY,'合计'):
            raise ValueError('该名称为保留表头')
        key = _key(rows[n-1],header.index(SECONDARY))
        curves = {}
        _progress_before(sheets,sheet['name'],key,bisect_right(sheet['headerRows'],n),header[col-1],curves)
        before = list(rows[n-1]) if sheet['name']==SUMMARY and col<=14 else []
        _put(rows[n-1],col-1,_normalize_input(sheet,n,col,edit['value']))
        if sheet['name'] == SUMMARY:
            if col in BACKFILLED:
                coord = f'{n}:{col}'
                if _get(rows[n-1],col-1) is None:
                    manual.discard(coord)
                else:
                    manual.add(coord)
            _summary_calculate(sheet,n,{col})
            if before:
                _update_dependents(sheets,sheet,n,before)
        elif sheet['name'] == DAILY:
            _daily_total(rows[n-1],header)
            _block_totals(sheet,n)
        elif curves:
            _finish_curves(sheets,curves)
    _sync_gross(sheets,pending)
    _reconcile_totals(sheets, previous, manual)
    _refresh_progress_totals(sheets,before_refs,pending)
    _finish_curves(sheets,pending)
    size = sum(len(row) for s in result['sheets'] for row in s['rows'])
    if size > MAX_CELLS or any(len(s['rows'])>MAX_ROWS for s in result['sheets']):
        raise ValueError('数据表超出大小限制')
    _mark_derived(sheets)
    return result


def validate_publication(data):
    seen = set()
    for n,row in enumerate(data['sheets'][0]['rows'][1:],2):
        if not any(v is not None for v in row):
            continue
        key = (_get(row,0),_get(row,1) or '')
        if not key[0] or key in seen:
            raise ValueError(f'车型汇总第 {n} 行缺少代际名或车型与二级代际重复')
        seen.add(key)
        for a,b in ((8,9),(10,11)):
            if _get(row,a) and _get(row,b) and _days(row[a],row[b]) < 1:
                raise ValueError(f'车型汇总第 {n} 行结束日期早于开始日期')
    for sheet in data['sheets'][1:]:
        allowed = seen | {tuple(key) for key in sheet.get('historicalIdentities',[])}
        for n,row,head,idx in _detail_rows(sheet):
            if _key(row,idx) not in allowed:
                raise ValueError(f'{sheet["name"]} 第 {n} 行的代际名及二级代际名无法匹配车型汇总')
        for n,row in enumerate(sheet['rows'],1):
            if not _get(row,0) and any(isinstance(v,(int,float)) for v in row):
                raise ValueError(f'{sheet["name"]} 第 {n} 行尚未填写车型')


def _selected_rows(sheet, models):
    if not models:
        return enumerate(sheet['rows'],1)
    selected = []
    rows = sheet['rows']
    for i,start in enumerate(sheet['headerRows']):
        end = sheet['headerRows'][i+1]-1 if i+1<len(sheet['headerRows']) else len(rows)
        head = rows[start-1]
        secondary = head.index(SECONDARY)
        matches = [(j+1,rows[j]) for j in range(start,end)
                   if _get(rows[j],0) in models or _get(rows[j],secondary) in models]
        # In progress sheets section order distinguishes quantities from rates;
        # keeping an empty first section prevents a rate moving into its position.
        if matches or sheet['name'] != DAILY:
            selected.extend([(start,head),*matches])
    return selected or [(1,rows[0])]


def _within_dates(sheet, start_date, end_date):
    rows, projected = sheet['rows'], []
    for i,start in enumerate(sheet['headerRows']):
        end = sheet['headerRows'][i+1]-1 if i+1<len(sheet['headerRows']) else len(rows)
        head = rows[start-1]
        dates = [j for j,v in enumerate(head) if _is_date(v) and
                 (not start_date or start_date <= v) and (not end_date or v <= end_date)]
        if not dates:
            continue
        secondary = head.index(SECONDARY)
        selected_head = [PRIMARY, *(head[j] for j in dates), '合计', SECONDARY]
        projected.append(selected_head)
        for row in rows[start:end]:
            primary = _get(row,0)
            if not primary or primary == '合计':
                continue
            selected = [primary, *(_get(row,j) for j in dates), None, _get(row,secondary)]
            _daily_total(selected,selected_head)
            projected.append(selected)
    return _sheet(DAILY,projected or [[PRIMARY,'合计',SECONDARY]])


def export_workbook(data, selection=None):
    selection = {} if selection is None else selection
    if not isinstance(selection,dict):
        raise ValueError('导出条件无效')
    scope = selection.get('scope', 'full')
    if scope not in ('full', 'schedule'):
        raise ValueError('未知的导出范围')
    available = [sheet['name'] for sheet in data['sheets']]
    sheets = [SUMMARY] if scope == 'schedule' else selection.get('sheets', available)
    models = selection.get('models', [])
    if not isinstance(sheets,list) or not sheets or any(not isinstance(v,str) or v not in available for v in sheets):
        raise ValueError('请至少选择一张有效的数据表')
    if not isinstance(models,list) or any(not isinstance(v,str) or not v for v in models):
        raise ValueError('导出车型条件无效')
    start = end = None
    if DAILY in sheets:
        start = _date(selection['dateFrom']) if selection.get('dateFrom') else None
        end = _date(selection['dateTo']) if selection.get('dateTo') else None
        if start and end and start > end:
            raise ValueError('导出结束日期早于开始日期')
    book = Workbook()
    book.remove(book.active)
    for sheet in data['sheets']:
        if sheet['name'] not in sheets:
            continue
        if scope == 'schedule':
            sheet = {**sheet, 'rows': [row[:14] for row in sheet['rows']]}
        if sheet['name'] == DAILY and (start or end):
            sheet = _within_dates(sheet,start,end)
        ws = book.create_sheet(sheet['name'])
        for target,(n,row) in enumerate(_selected_rows(sheet,set(models)),1):
            head = _header(sheet,n)
            is_head = n in sheet['headerRows']
            for j,v in enumerate(row,1):
                if v is None:
                    continue
                cell = ws.cell(target,j)
                title = _get(head,j-1)
                if _is_date(v) and (title in DATE_FIELDS or (is_head and sheet['name']==DAILY)):
                    cell.value = date.fromisoformat(v)
                    cell.number_format = 'yyyy-mm-dd'
                else:
                    cell.value = v
                    if isinstance(v,str):
                        cell.data_type = 's'
                    elif title in RATE_FIELDS or (sheet['name'] not in (SUMMARY, DAILY) and bisect_right(sheet['headerRows'],n)>1 and re.fullmatch(r'D\d+',str(title))):
                        cell.number_format = '0.00%'
                if is_head:
                    cell.font = Font(bold=True,color='FFFFFF')
                    cell.fill = PatternFill('solid',fgColor='36547C')
        ws.column_dimensions['A'].width = 34
        for j,title in enumerate(sheet['rows'][0][1:],2):
            ws.column_dimensions[get_column_letter(j)].width = 30 if title==SECONDARY else 14
        ws.freeze_panes = 'B2'
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()
