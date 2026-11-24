"""Human-readable XLSX interchange; imports are previews and never write storage."""
import copy
import io
import uuid
import zipfile
from datetime import date, datetime
from xml.etree.ElementTree import ParseError

from openpyxl import Workbook
from xlsx_input import WorkbookBatch, MAX_ROWS, header_values
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from catalog import TABLES, STATES, empty, validation_issues, diff, natural_key

SHEETS = {'models':'车型档案', 'phases':'发布阶段', 'versions':'版本', 'options':'配置项目',
          'matrix':'配置适用关系', 'aliases':'名称映射', 'groups':'汇总规则'}
LABELS = {'id':'编号','brand':'品牌','series':'车系','generation':'代际','subgeneration':'二级代际',
          'year':'年款','energy':'能源类型','tier':'产品档位','status':'状态','model_id':'车型',
          'name':'名称','presale_date':'小订日期','sale_date':'首销日期','power':'动力','battery':'电池包',
          'category':'类别','source':'来源','target_type':'对象类型','target_id':'标准对象',
          'scope':'生效范围','start_date':'生效日期','end_date':'截止日期','members':'成员',
          'version_id':'版本','option_id':'配置项目','state':'适用状态'}
REFS = {'model_id':'models','version_id':'versions','option_id':'options'}
REFERENCE_SHEETS = {'models':'引用_车型','versions':'引用_版本','options':'引用_配置'}
SEP = ' ｜ '


def model_name(row):
    return ' '.join(str(row.get(key,'')) for key in ['brand','series','generation','subgeneration'] if row.get(key)) + ((' '+row['year']+'款') if row.get('year') else '')


def reference_label(table, row, data):
    if table == 'models': return model_name(row)
    model = next((m for m in data['models'] if m['id'] == row.get('model_id')), {})
    parts = [model_name(model), row.get('category',''), row.get('name','')]
    return ' / '.join(p for p in parts if p)


def referenced(value, table, data):
    row = next((r for r in data.get(table, []) if r['id'] == value), None)
    return value+SEP+reference_label(table,row,data) if row else value


def _style_sheet(ws):
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = ws.dimensions
    ws.row_dimensions[1].height = 26
    for cell in ws[1]:
        cell.font = Font(name='Microsoft YaHei', bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='2859F5')
        cell.alignment = Alignment(vertical='center')
    for col in ws.columns:
        field = col[0].value
        ws.column_dimensions[col[0].column_letter].width = 40 if field in ['编号','车型','版本','配置项目','标准对象','成员','可选值'] else 20
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.data_type = 's'
            cell.font = Font(name='Microsoft YaHei', size=11)
            cell.alignment = Alignment(vertical='center')


def excel(data, template=False):
    wb = Workbook(); wb.remove(wb.active)
    for table, fields in TABLES.items():
        ws = wb.create_sheet(SHEETS[table]); ws.append([LABELS[f] for f in fields])
        if not template:
            for record in data[table]:
                row=[]
                for field in fields:
                    value=record.get(field,'')
                    if field in REFS: value=referenced(value,REFS[field],data)
                    elif field=='target_type': value=SHEETS.get(value,value)
                    elif field=='target_id': value=referenced(value,record.get('target_type',''),data)
                    row.append(value)
                ws.append(row)
        _style_sheet(ws)
        for field, choices in {'status':['规划','在售','停用'],'state':STATES,'category':['动力','电池包','外观','内饰','轮毂','其他'],'target_type':[SHEETS[k] for k in ['models','versions','options']]}.items():
            if field in fields:
                dv=DataValidation(type='list',formula1='"'+','.join(choices)+'"',allow_blank=True)
                dv.errorTitle='值不在候选项中';dv.error='请选择列表中的值';dv.showErrorMessage=field!='category';dv.errorStyle='stop'
                ws.add_data_validation(dv); col=get_column_letter(fields.index(field)+1);dv.add(f'{col}2:{col}{MAX_ROWS+1}')
        for field, kind in REFS.items():
            if field in fields and data[kind]:
                dv=DataValidation(type='list',formula1='Vehicle_'+kind,allow_blank=True)
                # Names of new rows in the same workbook remain allowed; import resolves them.
                dv.showErrorMessage=False;ws.add_data_validation(dv);col=get_column_letter(fields.index(field)+1);dv.add(f'{col}2:{col}{MAX_ROWS+1}')
    for table, name in REFERENCE_SHEETS.items():
        ws=wb.create_sheet(name);ws.append(['可选值','编号','名称'])
        for row in data[table]:ws.append([referenced(row['id'],table,data),row['id'],reference_label(table,row,data)])
        _style_sheet(ws)
        ws.protection.sheet=True
        if data[table]:wb.defined_names.add(DefinedName('Vehicle_'+table,attr_text=f"'{name}'!$A$2:$A${len(data[table])+1}"))
    stream=io.BytesIO();wb.save(stream);return stream.getvalue()


def issue(source, field, message):
    return {key:source.get(key,'') for key in ['file','sheet','row']} | {'field':LABELS.get(field,field),'message':message}


def cell_text(value):
    if value is None: return ''
    if isinstance(value,(date,datetime)): return value.strftime('%Y-%m-%d')
    if isinstance(value,float) and value.is_integer(): return str(int(value))
    return str(value).strip()


def field_key(table, header):
    if header in TABLES[table]: return header
    localized={LABELS[f]:f for f in TABLES[table]}
    for alias,field in {'车型编号':'model_id','车型名称':'model_id','版本编号':'version_id','配置编号':'option_id',
                        '标准对象编号':'target_id','版本名称':'name','配置名称':'name','阶段名称':'name','汇总组':'name'}.items():
        if field in TABLES[table]:localized[alias]=field
    return localized.get(header)


def parse_workbook(raw, filename, batch=None):
    records=[]; issues=[];source={'file':filename,'sheet':'','row':0}
    batch=batch or WorkbookBatch([raw])
    before=batch.expanded
    try:
        wb=batch.open(raw)
    except (ValueError,OSError,zipfile.BadZipFile,KeyError,ParseError) as exc:
        return [],[issue(source,'',str(exc))],0
    try:
        for ws in wb:
            source={'file':filename,'sheet':ws.title,'row':1}
            if ws.title in REFERENCE_SHEETS.values():continue
            table=next((key for key,value in SHEETS.items() if ws.title in [key,value]),None)
            if not table:issues.append(issue(source,'','未知工作表'));continue
            if ws.max_column and ws.max_column>len(TABLES[table])+8:
                issues.append(issue(source,'','工作表列数超出模板范围'));continue
            cells=ws.iter_rows()
            titles=header_values(cells)
            fields=[field_key(table,title) for title in titles]
            if not fields or None in fields or len(set(fields))!=len(fields):
                issues.append(issue(source,'','表头缺失、重复或包含未知列：'+', '.join(t for t,f in zip(titles,fields) if f is None)));continue
            for number,row in enumerate(cells,2):
                source={'file':filename,'sheet':ws.title,'row':number}
                batch.count_row(number)
                values={}
                if any(cell.value is not None and cell_text(cell.value) for cell in row[len(fields):]):
                    issues.append(issue(source,'','存在无表头的数据列'));continue
                for field,cell in zip(fields,row):
                    value=cell_text(cell.value)
                    if cell.data_type=='f' or value.startswith('='):
                        issues.append(issue(source,field,'不接受公式，请粘贴为值'));continue
                    if len(value)>4000:issues.append(issue(source,field,'单元格超过 4000 字符'));continue
                    if value:values[field]=value
                if not values:continue
                if values.get('target_type') in SHEETS.values():values['target_type']=next(k for k,v in SHEETS.items() if v==values['target_type'])
                records.append(source|{'table':table,'values':values})
    except (ValueError,OSError,ParseError) as exc:
        issues.append(issue(source,'',str(exc)))
    finally:wb.close()
    return records,issues,batch.expanded-before


def _encoded_id(value):
    return value.split(SEP,1)[0].strip() if SEP in value else value


def read_excel(raw):
    """Compatibility parser: old English workbooks and exported stable-ID references."""
    records,issues,_=parse_workbook(raw,'导入文件.xlsx')
    if issues:raise ValueError('\n'.join(format_issue(x) for x in issues))
    result={}
    for record in records:
        row=record['values'].copy()
        for field in REFS.keys()|{'target_id'}:
            if field in row:row[field]=_encoded_id(row[field])
        if 'members' in row:row['members']=','.join(_encoded_id(v.strip()) for v in row['members'].split(','))
        result.setdefault(record['table'],[]).append(row)
    return result


def format_issue(item):
    location=' / '.join(str(item[key]) for key in ['file','sheet'] if item.get(key))
    if item.get('row'):location+=' / 第 '+str(item['row'])+' 行'
    if item.get('field'):location+=' / '+item['field']
    return location+'：'+item['message']


def resolve_reference(value,table,data,model_id=None):
    if not value:raise ValueError('关联不能为空')
    identifier=_encoded_id(value)
    by_id=[row for row in data.get(table,[]) if row['id']==identifier]
    if by_id:return by_id[0]['id']
    if SEP in value:raise ValueError('关联编号不存在：'+identifier)
    choices=[]
    for row in data.get(table,[]):
        if model_id and row.get('model_id')!=model_id:continue
        labels={reference_label(table,row,data)}
        if table=='models':labels.add(row.get('series',''))
        else:labels.add(row.get('name',''))
        if value in labels:choices.append(row['id'])
    if len(choices)!=1:raise ValueError(('名称存在歧义：' if choices else '未匹配：')+value)
    return choices[0]


def preview_import(base, files):
    batch=WorkbookBatch(files)
    records=[];issues=[]
    for item in files:
        filename,raw=batch.decode(item)
        parsed,errors,_=parse_workbook(raw,filename,batch)
        records.extend(parsed);issues.extend(errors)
    result=copy.deepcopy(base);seen={};sources={};touched=set()
    # Processing by object type allows files to be selected in any order.
    for table in ['models','versions','options','phases','matrix','aliases','groups']:
        by_id={r['id']:r for r in result[table]}
        by_key={}
        for r in result[table]:
            key=natural_key(table,r)
            if key:by_key.setdefault(key,[]).append(r)
        for record in (r for r in records if r['table']==table):
            row=record['values'].copy();explicit_fields=set(row);bad=False
            existing=by_id.get(row.get('id'))
            context={**(existing or {}),**row}
            for field,kind in REFS.items():
                if field not in TABLES[table]:continue
                if field not in context:
                    issues.append(issue(record,field,'关联不能为空'));bad=True;continue
                try:
                    scope=None
                    if field=='option_id':scope=next((r['model_id'] for r in result['versions'] if r['id']==context.get('version_id')),None)
                    context[field]=row[field]=resolve_reference(context[field],kind,result,scope)
                except ValueError as exc:issues.append(issue(record,field,str(exc)));bad=True
            if table=='aliases':
                kind=context.get('target_type')
                if kind not in ['models','versions','options']:issues.append(issue(record,'target_type','对象类型无效'));bad=True
                else:
                    try:row['target_id']=resolve_reference(context.get('target_id',''),kind,result)
                    except ValueError as exc:issues.append(issue(record,'target_id',str(exc)));bad=True
            if table=='groups' and context.get('members'):
                members=context['members'].replace('，',',').replace(';',',').replace('；',',').split(',')
                try:row['members']=','.join(resolve_reference(v.strip(),'models',result) for v in members)
                except ValueError as exc:issues.append(issue(record,'members',str(exc)));bad=True
            if bad:continue
            context={**context,**row};key=natural_key(table,context)
            matches=by_key.get(key,[]) if key else []
            if not existing:
                if len(matches)>1:issues.append(issue(record,'id','同名对象不唯一，请填写编号'));continue
                if row.get('id') and matches and matches[0]['id']!=row['id']:issues.append(issue(record,'id','编号与已有同名对象冲突'));continue
                if not row.get('id'):
                    if not key:issues.append(issue(record,'id','缺少编号或用于匹配的必填字段'));continue
                    if matches:existing=matches[0]
            ident=existing['id'] if existing else row.get('id') or str(uuid.uuid4())
            row['id']=ident
            if not existing:
                if table in ['models','versions','options']:row.setdefault('status','规划')
                if table=='matrix':row.setdefault('state','未配置')
            checked={**(existing or {}),**row}
            if 'status' in TABLES[table] and checked.get('status') not in ['规划','在售','停用']:
                issues.append(issue(record,'status','状态无效'));continue
            if table=='matrix':
                if checked.get('state') not in STATES:issues.append(issue(record,'state','适用状态无效'));continue
                version=next(r for r in result['versions'] if r['id']==checked['version_id'])
                option=next(r for r in result['options'] if r['id']==checked['option_id'])
                if version['model_id']!=option['model_id']:issues.append(issue(record,'option_id','配置不可跨车型关联'));continue
            for field in [f for f in TABLES[table] if f.endswith('_date')]:
                if checked.get(field):
                    try:datetime.strptime(checked[field],'%Y-%m-%d')
                    except ValueError:issues.append(issue(record,field,'日期格式应为 YYYY-MM-DD'));bad=True
            if bad:continue
            conflict=False
            for field,value in row.items():
                if field not in explicit_fields:continue
                previous=seen.get((table,ident,field))
                if previous and previous[0]!=value:
                    issues.append(issue(record,field,'与 '+format_issue(previous[1]|{'field':'','message':''}).rstrip('：')+' 的同一对象值冲突'));conflict=True
                else:seen[(table,ident,field)]=(value,record)
            if conflict:continue
            if existing:
                old_key=natural_key(table,existing)
                if old_key and existing in by_key.get(old_key,[]):by_key[old_key].remove(existing)
                existing.update(row);stored=existing
            else:
                result[table].append(row);stored=row;by_id[ident]=row
            new_key=natural_key(table,stored)
            if new_key:by_key.setdefault(new_key,[]).append(stored)
            sources[(table,ident)]=record;touched.add((table,ident))
    if sum(len(row.get('members','').split(',')) for row in result['groups'])>2000:
        raise ValueError('汇总规则成员关系超过 2000 条，请缩小导入范围')
    for table,ident,message in validation_issues(result):
        source=sources.get((table,ident),{'file':'当前草稿','sheet':'','row':0})
        issues.append(issue(source,'',message))
    changes=diff(base,result)
    old={table:{r['id']:r for r in rows} for table,rows in base.items()}
    new={table:{r['id']:r for r in rows} for table,rows in result.items()}
    summary={'added':0,'updated':0,'unchanged':0}
    for table,ident in touched:
        category='added' if ident not in old[table] else 'updated' if old[table][ident]!=new[table][ident] else 'unchanged'
        summary[category]+=1
    return {'data':result,'changes':changes,'errors':[format_issue(x) for x in issues],'issues':issues,'summary':summary}
