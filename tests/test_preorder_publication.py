import json
import unittest
from contextlib import closing
from io import BytesIO
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from openpyxl import load_workbook

from campaign_store import CampaignStore
from stage_workbook import SUMMARY, empty_workbook, export_workbook


def legacy_source():
    book = load_workbook(BytesIO(export_workbook(empty_workbook())))
    try:
        ws = book[SUMMARY]
        ws['A2'], ws['I2'], ws['J2'], ws['K2'] = '直接首销', 'NA', 'NA', '2026-11-01'
        ws['A3'] = '待定车型'
        ws['A4'], ws['I4'] = '已开小订', '2026-10-01'
        progress = book['直接大定进度']
        progress.insert_rows(2)
        progress['A2'], progress['C2'], progress['D2'] = '直接首销', 'NA', 'NA'
        out = BytesIO()
        book.save(out)
        return out.getvalue()
    finally:
        book.close()


class PreorderPublicationTests(unittest.TestCase):
    def test_history_database_snapshot_and_excel_agree_after_restart(self):
        with TemporaryDirectory() as root:
            store = CampaignStore(root)
            raw = legacy_source()
            draft = store.import_legacy(0, raw, '测试')
            published = store.save(draft['revision'], [], '测试', publish=True)
            self.assertFalse(published['hasUnpublishedChanges'])
            self.assertEqual(store.published(1, editor=True)['sheets'], draft['sheets'])
            self.assertTrue(store.preview_import(published['revision'],raw)['identical'])

            with closing(store.connect()) as db:
                payload = json.loads(db.execute('SELECT data FROM publications WHERE revision=1').fetchone()[0])
            summary = payload['sheets'][0]
            self.assertEqual(summary['noPreorderRows'], [2])
            self.assertEqual(summary['rows'][0][-1], '有小订')
            self.assertIs(summary['rows'][1][-1], False)
            self.assertEqual(summary['rows'][1][8:11], [None, None, '2026-11-01'])
            self.assertEqual(summary['rows'][2], ['待定车型'])
            self.assertEqual(summary['rows'][3][8], '2026-10-01')
            progress = next(s for s in payload['sheets'] if s['name']=='直接大定进度')
            self.assertEqual(progress['noPreorderRows'], [2])
            self.assertEqual(progress['rows'][1][2:4], [None, None])
            self.assertEqual(store.published()['sheets'], payload['sheets'])

            edits = [dict(sheet=SUMMARY, row=2, col=5, value='调整发布安排')]
            store.save(published['revision'], edits, '测试', publish=True)
            store.snapshot.unlink()
            reopened = CampaignStore(root)
            self.assertEqual(json.loads(reopened.snapshot.read_text('utf-8')), reopened.published(2))
            self.assertEqual(reopened.published(1)['sheets'], payload['sheets'])
            self.assertEqual(reopened.published(1, editor=True)['sheets'], draft['sheets'])
            latest = reopened.get()
            self.assertEqual(latest['sheets'], reopened.published(2, editor=True)['sheets'])
            with self.assertRaisesRegex(ValueError,'没有需要发布'):
                reopened.save(latest['revision'], [], '测试', publish=True)
            book = load_workbook(BytesIO(reopened.export({}, source='published')))
            try:
                self.assertEqual((book[SUMMARY]['I2'].value, book[SUMMARY]['J2'].value), ('NA','NA'))
                self.assertNotIn('有小订', [c.value for c in book[SUMMARY][1]])
            finally:
                book.close()

    def test_public_api_returns_editor_revision_on_request(self):
        from tests import test_campaign_api
        client = test_campaign_api.CampaignApiTests()
        client.setUp()
        self.addCleanup(client.doCleanups)
        edits = [dict(sheet=SUMMARY,row=2,col=c,value=v) for c,v in ((1,'直接首销'),(9,'NA'),(10,'NA'))]
        with client.post('publish', {'revision':0, 'edits':edits, 'owner':'测试发布'}) as response:
            candidate = json.load(response)
        with urlopen(client.base+'campaign/published?revision=1&view=editor') as response:
            self.assertEqual(json.load(response)['sheets'], candidate['sheets'])
        with urlopen(client.base+'campaign/published?revision=1') as response:
            self.assertEqual(json.load(response)['sheets'][0]['rows'][1][8:10], [None,None])
