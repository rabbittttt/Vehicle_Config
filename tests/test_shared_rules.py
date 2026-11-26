import copy
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook
from shared_rules import RuleStore, parse_source, render_source, validate, Conflict
from rules_excel import rules_excel, preview_rules_import

SOURCE = b'# keep\r\nMERGE_GROUPS = {"A&B": ["A", "B"]}\r\nOTHER = 3\r\ndef keep():\r\n    return OTHER\r\n'

SOP_PROJECT = Path(__file__).resolve().parents[2] / 'SOP维护'
REJECTED_GROUPS = [
    {'外层': ['内层'], '内层': ['车型']},
    {'Ｓ９': ['车型甲'], 'S9': ['车型乙']},
    {'组一': ['品牌 车型 2026款'], '组二': ['品牌 车型 2026款BETA']},
    {'组一': ['车型', '车型（ＢＥＴＡ）']},
    {'组一': ['车型 2025款', '车型＿BETA＿2025款']},
    {'组一': ['BETA']},
    {'BETA': ['车型']},
]
VALID_MEMBERS = ['品牌 车型 BETA 2025款', '品牌 车型 2026款', '品牌 BETAMAX 2026款']

def workbook_file(name, sheet, rows):
    book = Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    out = BytesIO()
    book.save(out)
    return {'name': name, 'raw': out.getvalue()}


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'shared_config.py';self.path.write_bytes(SOURCE);self.store=RuleStore(self.root,self.path)
    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()
    def changed(self):
        data=copy.deepcopy(self.store.get()['draft']['data'])
        data['battery_rules']=[{'id':'b1','generation':['A','2026'],'version':['Ultra'],'sku':[],'capacity':'53度'}]
        data['option_prices']=[{'id':'p1','generation':'A','category':'外观','name':'白色','price':6000,'default':False,'versions':['Max'],'match_versions':[],'seat':'六座'}]
        return data
    def test_save_publish_backup_and_conflict(self):
        data=self.changed();self.store.save(data,0,'tester');self.assertEqual(self.path.read_bytes(),SOURCE)
        p=self.store.preview(data,1);self.assertFalse(p['errors']);self.assertIn('BATTERY_RULES',p['script_diff'])
        s=self.store.publish(data,1,p['source_hash'],'tester');self.assertEqual(s['published']['data'],data)
        self.assertEqual(next((self.root/'rules/backups').glob('*.py')).read_bytes(),SOURCE)
        text=self.path.read_bytes();self.assertIn(b'def keep():\r\n    return OTHER',text)
        parsed=parse_source(text);self.assertEqual(parsed['values']['OPTION_CONFIG']['A']['外观'][0],('白色',6000,False,{'versions':['Max'],'seat':'六座'}))
        with self.assertRaises(Conflict):self.store.save(data,1,'other')
        self.path.write_bytes(text+b'# external\r\n')
        with self.assertRaises(Conflict):self.store.publish(data,2,p['source_hash'],'tester')
    def test_excel_roundtrip_blank_and_conflicts(self):
        data=self.changed();p=preview_rules_import(data,[{'raw':rules_excel(data),'name':'test.xlsx'}]);self.assertEqual(p['errors'],[]);self.assertEqual(p['changes'],[])
        other=copy.deepcopy(data);other['option_prices'][0]['price']=7
        p=preview_rules_import(data,[{'raw':rules_excel(data)},{'raw':rules_excel(other)}]);self.assertTrue(any('冲突' in x for x in p['errors']))
        p=preview_rules_import(data,[{'raw':rules_excel(data,True)}]);self.assertEqual(p['data'],data)
    def test_literal_rejection_and_noop(self):
        self.assertEqual(render_source(parse_source(SOURCE),parse_source(SOURCE)['data']),SOURCE)
        for raw in [b'MERGE_GROUPS = dict()',b'MERGE_GROUPS = {"A": [], "A": []}',b'MERGE_GROUPS={}\nMERGE_GROUPS={}']:
            with self.assertRaises(ValueError):parse_source(raw)

    def test_invalid_source_price_identifies_rule_and_preserves_draft(self):
        old_draft = self.store.get()['draft']
        raw = ('OPTION_CONFIG = {"车型甲": {"外观": [("蓝", 0, False, {})],\n'
               '    "轮毂": [("旧轮毂", 6000, True, {}), ("样例轮毂",\n'
               '             "待询价", False, {})]}}').encode()
        self.path.write_bytes(raw)
        source = self.store.get()['source']
        self.assertFalse(source['available'])
        for detail in ('OPTION_CONFIG 第 3 行', '车型甲 / 轮毂 / 样例轮毂', "'待询价'", 'str', '请填写数字', str(self.path.resolve())):
            self.assertIn(detail, source['error'])
        with self.assertRaises(ValueError):
            self.store.reload(old_draft['revision'], source['hash'], 'tester')
        self.assertEqual(self.store.get()['draft'], old_draft)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_price_error_handles_non_finite_and_oversized_values(self):
        data = self.changed()
        for value, detail in [(True, 'bool'), (float('inf'), '有限数值'),
                              (1 << 17000, '范围'), (['x' * 10000], 'list')]:
            data['option_prices'][0]['price'] = value
            message, = validate(data)
            self.assertIn(detail, message)
            self.assertIn('A / 外观 / 白色', message)
            self.assertLess(len(message), 400)
    def test_renamed_option_matches_new_key_and_releases_old_key(self):
        data = self.changed()
        rename = workbook_file('改名.xlsx', '选配金', [
            ['编号', '名称', '默认选配'], ['p1', '雾蓝', None],
        ])
        prices = workbook_file('价格.xlsx', '选配金', [
            ['代际', '选配分类', '名称', '版本包含（每行一项）', '座位类型', '金额'],
            ['A', '外观', '雾蓝', 'Max', '六座', 42],
            ['A', '外观', '白色', 'Max', '六座', 18],
            ['A', '外观', '白色', 'Max', '六座', None],
        ])
        result = preview_rules_import(data, [rename, prices])
        self.assertEqual(result['issues'], [])
        blue, white = result['data']['option_prices']
        self.assertEqual(blue, data['option_prices'][0] | {'name':'雾蓝', 'price':42})
        self.assertNotEqual(white['id'], 'p1')
        self.assertEqual((white['name'], white['price']), ('白色', 18))
        self.assertEqual(result['summary'], {'added':1, 'updated':1, 'unchanged':0})
        self.assertEqual(data['option_prices'][0]['name'], '白色')

    def test_transient_ambiguous_key_and_cross_file_conflict(self):
        data = self.changed()
        data['gen_names'] = [
            dict(id='a', source='原甲', target='标准甲'),
            dict(id='b', source='原乙', target='标准乙'),
        ]
        rename = workbook_file('改名.xlsx', '代际显示名称', [
            ['编号', '原名称', '标准名称'],
            ['b', '原甲', ''], [None, '原甲', '无法判定'],
            ['a', '原丙', ''], [None, '原甲', '更新后'],
        ])
        conflict = workbook_file('合并.xlsx', '代际显示名称', [
            ['原名称', '标准名称'], ['原甲', '其他值'],
        ])
        result = preview_rules_import(data, [rename, conflict])
        self.assertEqual([(i['file'],i['row'],i['field'],i['message']) for i in result['issues']], [
            ('改名.xlsx', 3, '编号', '无法唯一匹配，请填写编号'),
            ('合并.xlsx', 2, '标准名称', '同一规则在本批文件中的值冲突'),
        ])
        self.assertEqual(result['data']['gen_names'], [
            dict(id='a', source='原丙', target='标准甲'),
            dict(id='b', source='原甲', target='更新后'),
        ])

    def test_rank_only_updates_are_counted(self):
        data = self.changed()
        data['battery_rules'].append(dict(data['battery_rules'][0], id='b2', generation=['B']))
        priorities = workbook_file('顺序.xlsx', '电池包规则', [
            ['编号', '优先级'], ['b2', 1], ['b1', 2],
        ])
        result = preview_rules_import(data, [priorities])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['summary'], {'added':0, 'updated':2, 'unchanged':0})
        self.assertEqual([r['id'] for r in result['data']['battery_rules']], ['b2', 'b1'])
    def test_publication_preserves_unrepresented_options(self):
        options=(b'OPTION_CONFIG = {  # keep formatting\n'
                 b'    "empty": {}, "A": {"empty": [], "color": [("red", 1, False, {"versions": [], "seat": ""})]}\n}\n')
        self.path.write_bytes(options)
        state=self.store.reload(0,self.store.get()['source']['hash'],'tester')
        data=copy.deepcopy(state['draft']['data'])
        data['gen_names'].append(dict(id='new',source='A',target='AA'))
        preview=self.store.preview(data,1)
        self.assertFalse(preview['errors'])
        self.assertNotIn('-OPTION_CONFIG',preview['script_diff'])
        state=self.store.publish(data,1,preview['source_hash'],'tester')
        self.assertTrue(self.path.read_bytes().startswith(options))
        data['option_prices'][0].update(name='blue',price=2)
        self.store.publish(data,state['draft']['revision'],state['source']['hash'],'tester')
        value=parse_source(self.path.read_bytes())['values']['OPTION_CONFIG']
        self.assertEqual(value,{'empty':{},'A':{'empty':[],'color':[('blue',2,False,{'versions':[],'seat':''})]}})

    def test_recovery_reload_requires_confirmation_and_keeps_intended_data(self):
        intended=self.changed()
        def external_edit(raw,expected):
            self.path.write_bytes(SOURCE+b'# edited elsewhere\r\n')
            raise OSError('interrupted')
        with patch.object(self.store,'_atomic_source',side_effect=external_edit), self.assertRaises(OSError):
            self.store.publish(intended,0,self.store.get()['source']['hash'],'author')
        before=self.store.get()
        self.assertEqual(before['sync']['status'],'recovery_required')
        for token in [None,'outdated']:
            with self.assertRaises(Conflict):
                self.store.reload(0,before['source']['hash'],'reviewer',token)
        self.assertEqual(before,self.store.get())
        result=self.store.reload(0,before['source']['hash'],'reviewer',before['sync']['pending_token'])
        self.assertEqual(result['sync']['status'],'synced')
        event=next(row for row in result['history'] if row['kind']=='中断的发布')
        self.assertEqual(event['actor'],'author')
        self.assertEqual(self.store.history(event['id'])['data'],intended)
        self.assertEqual(self.path.read_bytes(),SOURCE+b'# edited elsewhere\r\n')

    def test_order_encoding_and_reload(self):
        raw='# coding: gbk\r\nGEN_DISPLAY_NAME_MAP={"旧":"新"}\r\n'.encode('gbk')
        parsed=parse_source(raw);data=parsed['data'];data['gen_names'][0]['target']='标准'
        rewritten=render_source(parsed,data);self.assertIn('标准',rewritten.decode('gbk'));self.assertIn(b'\r\n',rewritten)
        self.path.write_bytes(raw);state=self.store.get();self.store.reload(0,state['source']['hash'],'tester')
        self.assertEqual(self.store.get()['draft']['data']['gen_names'][0]['target'],'新')
    def test_publication_checks_sop_generation_identity(self):
        before = self.store.get()
        for groups in REJECTED_GROUPS:
            with self.subTest(groups=groups):
                data = copy.deepcopy(before['draft']['data'])
                data['merge_groups'] = [
                    {'id': str(i), 'name': name, 'members': members}
                    for i, (name, members) in enumerate(groups.items())
                ]
                preview = self.store.preview(data, 0)
                self.assertTrue(preview['errors'])
                with self.assertRaises(ValueError):
                    self.store.publish(data, 0, preview['source_hash'], 'tester')
                self.assertEqual(self.path.read_bytes(), SOURCE)
                self.assertEqual(self.store.get()['published'], before['published'])

    @unittest.skipUnless((SOP_PROJECT / 'sop' / 'source_rules.py').is_file(), '需要同一工作区的 SOP 项目')
    def test_group_publication_matches_installed_sop_reader(self):
        with patch.object(sys, 'path', [str(SOP_PROJECT), *sys.path]):
            from sop.source_rules import MergeRuleError, load_merge_rules
        valid = {'Outer': VALID_MEMBERS, 'Inner': ['Car']}
        for groups in [*REJECTED_GROUPS, valid]:
            with self.subTest(groups=groups):
                raw = ('MERGE_GROUPS = ' + repr(groups)).encode()
                parsed = parse_source(raw)
                if groups == valid:
                    self.path.write_bytes(render_source(parsed, parsed['data']))
                    self.assertEqual(load_merge_rules(self.path)['groups'], valid)
                else:
                    self.path.write_bytes(raw)
                    with self.assertRaises(MergeRuleError):
                        load_merge_rules(self.path)
                    with self.assertRaises(ValueError):
                        render_source(parsed, parsed['data'])

    def test_repair_legacy_nested_groups_without_rewriting_members(self):
        self.path.write_bytes(b'MERGE_GROUPS = {"Outer": ["Inner"], "Inner": ["Car"]}\n')
        state = self.store.reload(0, self.store.get()['source']['hash'], 'tester')
        data = state['draft']['data']
        self.assertEqual(data['merge_groups'][0]['members'], ['Inner'])
        members = VALID_MEMBERS
        data['merge_groups'][0]['members'] = members
        saved = self.store.save(data, state['draft']['revision'], 'tester')
        preview = self.store.preview(data, saved['draft']['revision'])
        self.assertFalse(preview['errors'])
        self.store.publish(data, saved['draft']['revision'], preview['source_hash'], 'tester')
        self.assertEqual(parse_source(self.path.read_bytes())['values']['MERGE_GROUPS']['Outer'], members)

    def test_failure_and_recovery(self):
        data=self.changed();p=self.store.preview(data,0)
        with patch.object(self.store,'_atomic_source',side_effect=OSError('blocked')):
            with self.assertRaises(OSError):self.store.publish(data,0,p['source_hash'],'tester')
        self.assertEqual(self.path.read_bytes(),SOURCE);self.assertEqual(self.store.get()['draft']['revision'],0)
        real=self.store._finish_publish
        with patch.object(self.store,'_finish_publish',side_effect=OSError('db unavailable')):
            with self.assertRaises(OSError):self.store.publish(data,0,p['source_hash'],'tester')
        self.store.db.close();self.store=RuleStore(self.root,self.path)
        self.assertEqual(self.store.get()['published']['data'],data);self.assertIsNone(self.store._pending())

if __name__=='__main__':unittest.main()
