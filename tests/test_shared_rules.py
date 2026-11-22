import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from shared_rules import RuleStore, parse_source, render_source, Conflict
from rules_excel import rules_excel, preview_rules_import

SOURCE = b'# keep\r\nMERGE_GROUPS = {"A&B": ["A", "B"]}\r\nOTHER = 3\r\ndef keep():\r\n    return OTHER\r\n'

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
