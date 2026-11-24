import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from catalog import Store, empty, validate
from excel_io import excel, read_excel, preview_import, SHEETS
from server import make_server
from test_catalog import fixture


def workbook(sheets):
    wb=Workbook();wb.remove(wb.active)
    for name,rows in sheets.items():
        ws=wb.create_sheet(name)
        for row in rows:ws.append(row)
    out=io.BytesIO();wb.save(out);return out.getvalue()


def file(raw,name='导入.xlsx'):
    return {'name':name,'content':base64.b64encode(raw).decode()}


class ExcelTests(unittest.TestCase):
    def test_chinese_template_styles_dropdowns_and_no_example_rows(self):
        wb=load_workbook(io.BytesIO(excel(fixture(),template=True)))
        self.assertEqual(wb.sheetnames[:7],list(SHEETS.values()))
        self.assertEqual(wb['车型档案'].max_row,1)
        self.assertEqual(wb['车型档案']['B1'].value,'品牌')
        self.assertEqual(wb['版本'].freeze_panes,'B2')
        self.assertGreaterEqual(len(wb['版本'].data_validations.dataValidation),2)
        self.assertIn('Vehicle_models',wb.defined_names)
        self.assertEqual(wb.defined_names['Vehicle_models'].attr_text,"'引用_车型'!$A$2:$A$2")
        self.assertTrue(any(dv.formula1=='Vehicle_models' for dv in wb['版本'].data_validations.dataValidation))
        self.assertTrue(wb['引用_车型'].protection.sheet)
        self.assertIn('m1 ｜ ',wb['引用_车型']['A2'].value)
        wb.close()

    def test_lossless_chinese_export_and_legacy_english(self):
        data=fixture();result=preview_import(data,[file(excel(data))])
        self.assertEqual(result['errors'],[]);self.assertEqual(result['changes'],[])
        self.assertEqual(result['summary'],{'added':0,'updated':0,'unchanged':3})
        legacy=workbook({'models':[['id','brand','year'],['m1','更新品牌',2027]]})
        result=preview_import(data,[file(legacy)])
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['data']['models'][0]['brand'],'更新品牌')
        self.assertEqual(result['data']['models'][0]['year'],'2027')
        self.assertEqual(result['data']['versions'],data['versions'])
        self.assertEqual(read_excel(excel(data))['versions'][0]['model_id'],'m1')

    def test_cross_file_name_references_and_idempotent_reimport(self):
        models=workbook({'车型档案':[['品牌','车系','代际','年款','能源类型'],['测试','新车型','一代',2026,'纯电']]})
        versions=workbook({'版本':[['车型','名称','动力'],['新车型','标准版','单电机']],
                           '配置项目':[['车型','类别','名称'],['新车型','外观','珍珠白']],
                           '配置适用关系':[['版本','配置项目','适用状态'],['标准版','珍珠白','选配']]})
        first=preview_import(empty(),[file(versions,'配置.xlsx'),file(models,'车型.xlsx')])
        self.assertEqual(first['errors'],[])
        self.assertEqual(first['summary']['added'],4)
        self.assertEqual(first['data']['models'][0]['status'],'规划')
        self.assertEqual(first['data']['matrix'][0]['version_id'],first['data']['versions'][0]['id'])
        second=preview_import(first['data'],[file(models,'车型.xlsx'),file(versions,'配置.xlsx')])
        self.assertEqual(second['changes'],[]);self.assertEqual(second['summary']['unchanged'],4)

    def test_blank_cells_and_missing_rows_preserve_existing(self):
        raw=workbook({'车型档案':[['编号','品牌','产品档位'],['m1','','高端']]})
        result=preview_import(fixture(),[file(raw)])
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['data']['models'][0]['brand'],'测试')
        self.assertEqual(result['data']['models'][0]['tier'],'高端')
        self.assertEqual(result['data']['versions'],fixture()['versions'])

    def test_conflicting_file_values_have_precise_source(self):
        one=workbook({'车型档案':[['编号','品牌'],['m1','品牌一']]})
        two=workbook({'车型档案':[['编号','品牌'],['m1','品牌二']]})
        original=fixture();result=preview_import(original,[file(one,'一.xlsx'),file(two,'二.xlsx')])
        self.assertEqual(original,fixture())
        found=next(x for x in result['issues'] if '冲突' in x['message'])
        self.assertEqual((found['file'],found['sheet'],found['row'],found['field']),('二.xlsx','车型档案',2,'品牌'))

    def test_global_validation_uses_table_and_full_id(self):
        models=workbook({'models':[['id','brand'],['m1','更新']]})
        phases=workbook({'phases':[['id','name','model_id','presale_date','sale_date'],
                                  ['m10','首发','m1','2026-02-20','2026-01-01']]})
        result=preview_import(fixture(),[file(models,'车型.xlsx'),file(phases,'阶段.xlsx')])
        issue=next(i for i in result['issues'] if '日期顺序' in i['message'])
        self.assertEqual((issue['file'],issue['sheet'],issue['row']),('阶段.xlsx','phases',2))

    def test_ambiguous_name_and_cross_model_matrix_fail(self):
        data=fixture();other=copy.deepcopy(data['models'][0]);other.update(id='m2',year='2027');data['models'].append(other)
        raw=workbook({'版本':[['车型','名称'],['车型','新版本']]})
        result=preview_import(data,[file(raw)])
        self.assertTrue(any('歧义' in x['message'] for x in result['issues']))
        data['options'].append({'id':'o2','model_id':'m2','category':'外观','name':'黑色','status':'规划'})
        raw=workbook({'matrix':[['version_id','option_id','state'],['v1','o2','标配']]})
        result=preview_import(data,[file(raw)])
        self.assertTrue(any('跨车型' in x['message'] and x['row']==2 for x in result['issues']))

    def test_dates_formulas_invalid_states_and_bad_headers(self):
        raw=workbook({'发布阶段':[['车型','名称','小订日期'],['m1','首发',datetime(2026,11,17)]]})
        result=preview_import(fixture(),[file(raw)])
        self.assertEqual(result['errors'],[]);self.assertEqual(result['data']['phases'][0]['presale_date'],'2026-11-17')
        raw=workbook({'版本':[['车型','名称','状态'],['m1','版本二','无效'],['m1','=1+1','规划']]})
        result=preview_import(fixture(),[file(raw)])
        self.assertTrue(any(x['row']==2 and x['field']=='状态' for x in result['issues']))
        self.assertTrue(any(x['row']==3 and '公式' in x['message'] for x in result['issues']))
        bad=workbook({'车型档案':[['编号','不存在的字段'],['m1','x']]})
        self.assertEqual(preview_import(fixture(),[file(bad)])['issues'][0]['row'],1)

    def test_missing_header_data_is_reported_and_defaults_do_not_conflict(self):
        raw=workbook({'车型档案':[['编号',None],['m1','误放数据']]})
        result=preview_import(fixture(),[file(raw)])
        self.assertTrue(any('无表头' in x['message'] and x['row']==2 for x in result['issues']))
        one=workbook({'车型档案':[['品牌','车系','代际','年款'],['测试','新车型','一代',2027]]})
        two=workbook({'车型档案':[['品牌','车系','代际','年款','状态'],['测试','新车型','一代',2027,'在售']]})
        result=preview_import(empty(),[file(one,'一.xlsx'),file(two,'二.xlsx')])
        self.assertEqual(result['errors'],[]);self.assertEqual(len(result['data']['models']),1)
        self.assertEqual(result['data']['models'][0]['status'],'在售')

    def test_group_members_roundtrip_even_when_display_name_has_comma(self):
        data=fixture();data['models'][0]['series']='车型, 特别版'
        data['groups']=[{'id':'g1','name':'组一','scope':'汇总','members':'m1','start_date':'','end_date':''}]
        result=preview_import(data,[file(excel(data))])
        self.assertEqual(result['errors'],[]);self.assertEqual(result['changes'],[])

    def test_unrecognized_reference_id_never_falls_back_to_label(self):
        raw=workbook({'版本':[['车型','名称'],['missing ｜ 车型','版本二']]})
        result=preview_import(fixture(),[file(raw)])
        self.assertTrue(any('编号不存在' in x['message'] for x in result['issues']))

    def test_missing_option_category_only_allowed_in_draft(self):
        raw=workbook({'options':[['id','model_id','name'],['extra','m1','新增配置']]})
        preview=preview_import(fixture(),[file(raw)])
        self.assertFalse(preview['errors'])
        self.assertFalse(validate(preview['data']))
        self.assertTrue(any('category' in error for error in validate(preview['data'],True)))

    def test_batches_count_blank_rows_in_both_editors(self):
        from shared_rules import empty_rules
        from rules_excel import preview_rules_import
        for preview,base,sheet in [(preview_import,empty(),'models'),(preview_rules_import,empty_rules(),'gen_names')]:
            raw=workbook({sheet:[['id'],[None],['example']]})
            with patch('xlsx_input.MAX_TOTAL_ROWS',3):
                result=preview(base,[file(raw),file(raw)])
            self.assertTrue(any('20000' in msg for msg in result['errors']),result['errors'])

    def test_limits_and_malformed_files(self):
        with self.assertRaises(ValueError):preview_import(empty(),[])
        with self.assertRaises(ValueError):preview_import(empty(),[{'name':'x.xlsx','content':'!bad!'}])
        malformed=preview_import(empty(),[file(b'not a zip')]);self.assertTrue(malformed['errors'])
        raw=workbook({'车型档案':[['品牌','车系','代际','年款'],['测试','车型','一代',2026]]})
        with patch('xlsx_input.MAX_EXPANDED',100):self.assertTrue(preview_import(empty(),[file(raw)])['errors'])


class ImportHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
        self.server=make_server(self.store,port=0);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.state=self.request('/api/state')
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.store.db.close();self.tmp.cleanup()
    def request(self,path,body=None,raw=False):
        req=urllib.request.Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers={'X-CSRF-Token':getattr(self,'state',{}).get('csrf','')})
        with urllib.request.urlopen(req) as response:return response.read() if raw else json.load(response)
    def test_preview_does_not_write_and_stale_revision_is_conflict(self):
        data=fixture();book=file(excel(data));before=self.store.get()
        result=self.request('/api/import-batch',{'files':[book],'revision':0})
        self.assertFalse(result['errors']);self.assertEqual(result['summary']['added'],3);self.assertEqual(self.store.get(),before)
        self.store.save(data,0,'tester')
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/import-batch',{'files':[book],'revision':0})
        self.assertEqual(ctx.exception.code,409);ctx.exception.close()
        bad=file(workbook({'版本':[['车型','名称'],['不存在','新版本']]}))
        before=self.store.get();result=self.request('/api/import-batch',{'files':[bad],'revision':1});self.assertTrue(result['issues']);self.assertEqual(self.store.get(),before)
    def test_readonly_allows_templates_and_export_but_blocks_import(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.server=make_server(self.store,port=0,readonly=True);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port);self.state=self.request('/api/state')
        for path in ['/api/template','/api/export']:
            self.assertTrue(self.request(path,raw=True).startswith(b'PK'))
        self.assertTrue(self.request('/api/export',{'data':fixture()},raw=True).startswith(b'PK'))
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.request('/api/import-batch',{'files':[file(excel(fixture()))],'revision':0})
        self.assertEqual(ctx.exception.code,403);ctx.exception.close()

if __name__=='__main__':unittest.main()
