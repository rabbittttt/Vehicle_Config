"""Structured XLSX interchange for the five shared_config rule constants."""
import copy
import io
import json
import math
import uuid
import zipfile
from xml.etree.ElementTree import ParseError

from openpyxl import Workbook
from xlsx_input import WorkbookBatch, header_values
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from shared_rules import validate, diff_rules

FIELDS = {
    'gen_names': ['id', 'source', 'target'],
    'merge_groups': ['id', 'name', 'members'],
    'battery_rules': ['id', 'priority', 'generation', 'version', 'sku', 'capacity'],
    'option_names': ['id', 'source', 'target'],
    'option_prices': ['id', 'generation', 'category', 'name', 'price', 'default', 'versions', 'match_versions', 'seat'],
}
SHEETS = {'gen_names':'代际显示名称', 'merge_groups':'代际合并', 'battery_rules':'电池包规则', 'option_names':'选配名称合并', 'option_prices':'选配金'}
LABELS = {'id':'编号', 'source':'原名称', 'target':'标准名称', 'name':'名称', 'members':'源代际（每行一项）',
          'priority':'优先级', 'generation':'代际', 'version':'动力和版本关键词（每行一项）', 'sku':'SKU关键词（每行一项）',
          'capacity':'电池容量', 'category':'选配分类', 'price':'金额', 'default':'默认选配',
          'versions':'版本包含（每行一项）', 'match_versions':'版本精确（每行一项）', 'seat':'座位类型'}
LIST_FIELDS = {'members', 'version', 'sku', 'versions', 'match_versions'}


def is_list(table, field):
    return field in LIST_FIELDS or (table == 'battery_rules' and field == 'generation')


def label(table, field):
    return '代际关键词（每行一项）' if table == 'battery_rules' and field == 'generation' else LABELS[field]


def rules_excel(data, template=False):
    errors = validate(data)
    if errors: raise ValueError('\n'.join(errors))
    wb = Workbook(); wb.remove(wb.active)
    for table, fields in FIELDS.items():
        ws = wb.create_sheet(SHEETS[table]); ws.append([label(table, f) for f in fields])
        if not template:
            for i, item in enumerate(data[table], 1):
                row = []
                for field in fields:
                    value = i if field == 'priority' else item[field]
                    if is_list(table, field):
                        value = json.dumps(value, ensure_ascii=False) if not value or any('\n' in s or '\r' in s for s in value) else '\n'.join(value)
                    if field == 'default': value = '是' if value else '否'
                    row.append(value)
                ws.append(row)
        ws.freeze_panes = 'B2'; ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.font = Font(name='Microsoft YaHei', bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='2859F5')
        for i, field in enumerate(fields, 1):
            ws.column_dimensions[get_column_letter(i)].width = 38 if is_list(table, field) or field == 'id' else 25
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str): cell.data_type = 's'  # formula-like names are literal text
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        for field, kind, first in [('default','list','"是,否"'), ('price','decimal','0'), ('priority','whole','1')]:
            if field not in fields: continue
            dv = DataValidation(type=kind, formula1=first, operator='greaterThanOrEqual' if kind != 'list' else None, allow_blank=True)
            dv.showErrorMessage = True; dv.errorTitle = '值无效'; dv.error = '请填写有效值'
            ws.add_data_validation(dv); col = get_column_letter(fields.index(field)+1); dv.add(f'{col}2:{col}10001')
    out = io.BytesIO(); wb.save(out); return out.getvalue()


def _value(table, field, value):
    if is_list(table, field):
        text = str(value).strip()
        if text.startswith('['):
            result = json.loads(text)
            if not isinstance(result, list) or any(not isinstance(x, str) for x in result): raise ValueError('须为文本列表')
            return result
        return [x.strip() for x in text.splitlines() if x.strip()]
    if field == 'default':
        if isinstance(value, bool): return value
        text = str(value).strip().lower()
        if text in ['是','true','1']: return True
        if text in ['否','false','0']: return False
        raise ValueError('默认选配须为是或否')
    if field in ['price','priority']:
        if isinstance(value, bool): raise ValueError('须为数字')
        number = float(value)
        if not math.isfinite(number) or number < 0: raise ValueError('须为非负有限数字')
        if field == 'priority' and (number < 1 or not number.is_integer()): raise ValueError('优先级须为正整数')
        return int(number) if number.is_integer() else number
    if isinstance(value, float) and value.is_integer(): return str(int(value))
    return str(value).strip()


def _key(table, row):
    fields = {'gen_names':['source'], 'option_names':['source'], 'merge_groups':['name'],
              'battery_rules':['generation','version','sku'],
              'option_prices':['generation','category','name','versions','match_versions','seat']}[table]
    return json.dumps([row.get(f, [] if is_list(table, f) else '') for f in fields], ensure_ascii=False)


def preview_rules_import(base, files):
    result = copy.deepcopy(base); issues = []; records = []; batch = WorkbookBatch(files)
    def issue(source, field, message):
        issues.append({**source, 'field':field, 'message':message})
    for index, file in enumerate(files, 1):
        source = {'file':str(file.get('name', f'文件{index}.xlsx'))[:200], 'sheet':'', 'row':0} if isinstance(file, dict) else {'file':f'文件{index}', 'sheet':'', 'row':0}
        wb = None
        try:
            source['file'],raw=batch.decode(file)
            wb=batch.open(raw)
            for ws in wb:
                source = {**source, 'sheet':ws.title, 'row':1}
                table = next((t for t, title in SHEETS.items() if ws.title in [t, title]), None)
                if not table: issue(source, '', '未知规则工作表'); continue
                if ws.max_column and ws.max_column > len(FIELDS[table])+8: issue(source, '', '列数超出模板范围'); continue
                rows = ws.iter_rows(); headers = header_values(rows)
                aliases = {label(table,f):f for f in FIELDS[table]} | {f:f for f in FIELDS[table]}
                fields = [aliases.get(title) for title in headers]
                if not fields or None in fields or len(fields) != len(set(fields)):
                    issue(source, '', '表头缺失、重复或包含未知列'); continue
                for number, cells in enumerate(rows, 2):
                    source = {**source, 'row':number}
                    batch.count_row(number)
                    if any(c.value is not None for c in cells[len(fields):]): issue(source, '', '存在无表头的数据列'); continue
                    values = {}; invalid = False
                    for field, cell in zip(fields, cells):
                        value = cell.value
                        if value is None or (isinstance(value,str) and not value.strip()): continue
                        try:
                            if cell.data_type == 'f': raise ValueError('不接受公式，请粘贴为值')
                            if len(str(value)) > 8000: raise ValueError('单元格超过 8000 字符')
                            values[field] = _value(table, field, value)
                        except (ValueError, TypeError) as exc: issue(source, label(table,field), str(exc)); invalid = True
                    if values and not invalid: records.append((table, values, dict(source)))
        except (ValueError, TypeError, OSError, KeyError, zipfile.BadZipFile, ParseError) as exc:
            issue(source, '', str(exc))
        finally:
            if wb is not None: wb.close()
    seen = {}; touched = set()
    original = {table: {r['id']: r for r in rows} for table, rows in base.items()}
    by_id = {table: {r['id']: r for r in rows} for table, rows in result.items()}
    by_key = {table: {} for table in result}
    for table, rows in result.items():
        for row in rows:
            by_key[table].setdefault(_key(table, row), set()).add(row['id'])
    original_ranks = {r['id']: i for i, r in enumerate(base['battery_rules'], 1)}
    ranks = original_ranks.copy()
    for table, values, source in records:
        keys = by_key[table]
        ident = values.get('id')
        if not ident:
            candidates = keys.get(_key(table, values), ())
            if len(candidates) > 1:
                issue(source, '编号', '无法唯一匹配，请填写编号')
                continue
            ident = next(iter(candidates), None)
        item = by_id[table].get(ident)
        old_key = _key(table, item) if item is not None else None
        if item is None:
            defaults = {f:[] if is_list(table,f) else False if f=='default' else 0 if f=='price' else '' for f in FIELDS[table] if f!='priority'}
            ident = ident or str(uuid.uuid4())
            item = defaults | {'id': ident}
            result[table].append(item)
            by_id[table][ident] = item
            if table=='battery_rules': ranks[ident] = len(ranks)+1
        identity = (table,item['id']); touched.add(identity)
        for field, value in values.items():
            if field=='id': continue
            signature = (*identity,field)
            if signature in seen and seen[signature] != value:
                issue(source,label(table,field),'同一规则在本批文件中的值冲突'); continue
            seen[signature] = value
            if field=='priority': ranks[item['id']] = value
            else: item[field] = value
        new_key = _key(table, item)
        if new_key != old_key:
            if old_key is not None:
                keys[old_key].remove(ident)
                if not keys[old_key]:
                    del keys[old_key]
            keys.setdefault(new_key, set()).add(ident)
        row_errors = validate({t:[item] if t==table else [] for t in FIELDS})
        for error in row_errors: issue(source,'',error)
    if any(field=='priority' for _,_,field in seen):
        if len(set(ranks.values())) != len(ranks): issue({'file':'','sheet':SHEETS['battery_rules'],'row':0},'优先级','优先级重复，请为受影响规则设置不同序号')
        result['battery_rules'].sort(key=lambda r:ranks[r['id']])
    reported = {i['message'] for i in issues}
    for error in validate(result):
        if error not in reported:
            issue({'file':'','sheet':'','row':0},'',error)
            reported.add(error)
    changes = diff_rules(base,result)
    counts = {'added':0, 'updated':0, 'unchanged':0}
    final_ranks = {r['id']: i for i, r in enumerate(result['battery_rules'], 1)}
    for table, ident in touched:
        before = original[table].get(ident)
        after = by_id[table][ident]
        changed = before != after or (table=='battery_rules' and original_ranks.get(ident) != final_ranks[ident])
        counts['added' if before is None else 'updated' if changed else 'unchanged'] += 1
    return {'data':result,'changes':changes,'errors':[i['message'] for i in issues],'issues':issues,'summary':counts}
