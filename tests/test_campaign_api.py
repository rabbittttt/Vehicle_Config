import json
from datetime import datetime
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openpyxl import load_workbook
from catalog import Store
from server import make_server
from stage_workbook import SUMMARY, SUMMARY_FIELDS


class CampaignApiTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        catalog = Store(temp.name)
        self.addCleanup(catalog.db.close)
        self.server = make_server(catalog, port=0)
        self.addCleanup(self.server.server_close)
        worker = Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}/vehicle-config/api/'
        with urlopen(self.base + 'state') as response:
            self.token = json.load(response)['csrf']

    def post(self, action, body, token=None):
        return urlopen(Request(self.base + 'campaign/' + action, data=json.dumps(body).encode(),
                               headers={'X-CSRF-Token': self.token if token is None else token}))

    def test_publish_versions_and_custom_export_without_excel_source(self):
        edits = [{'sheet':'车型汇总','row':2,'col':1,'value':'测试车型'},
                 {'sheet':'小订by天','model':'测试车型','date':'2026-11-25','value':0},
                 {'sheet':'小订by天','model':'测试车型','date':'2026-11-26','value':12}]
        with self.post('save', {'revision':0,'edits':edits}) as response:
            draft = json.load(response)
        with urlopen(self.base + 'campaign/published') as response:
            self.assertEqual(json.load(response)['revision'], 0)
        with self.post('publish', {'revision':draft['revision'],'edits':[]}) as response:
            self.assertEqual(json.load(response)['publishedRevision'], 1)
        with urlopen(self.base + 'campaign/published?revision=1') as response:
            self.assertEqual(json.load(response)['sheets'][0]['rows'][1][0], '测试车型')
        with self.post('export', {'sheets':['小订by天'],'models':['测试车型'],
                                  'dateFrom':'2026-11-25','dateTo':'2026-11-25'}) as response:
            book = load_workbook(BytesIO(response.read()), data_only=True)
        try:
            self.assertEqual(book.sheetnames, ['小订by天'])
            entries = list(book.active.values)
            self.assertIn(0, entries[1])
            self.assertNotIn(12, entries[1])
        finally:
            book.close()

    def test_schedule_only_exports_selected_stage_and_saved_edition(self):
        values = [(2, 1, '车型甲'), (2, 2, '甲一期'), (2, 3, '标准'),
                  (2, 9, '2026-11-04'), (2, 10, '2026-11-06'), (2, 16, 35),
                  (3, 1, '车型甲'), (3, 2, '甲二期')]
        edits = [{'sheet': SUMMARY, 'row': row, 'col': col, 'value': value}
                 for row, col, value in values]
        with self.post('publish', {'revision': 0, 'edits': edits}) as response:
            revision = json.load(response)['revision']
        with self.post('save', {'revision': revision, 'edits': [
                {'sheet': SUMMARY, 'row': 2, 'col': 3, 'value': '新档位'}]}) as response:
            self.assertTrue(json.load(response)['hasUnpublishedChanges'])
        for source, tier in [('draft', '新档位'), ('published', '标准')]:
            with self.post('export', {'scope': 'schedule', 'models': ['甲一期'], 'source': source,
                                      'sheets': [SUMMARY, '大定进度']}) as response:
                book = load_workbook(BytesIO(response.read()), read_only=True)
            try:
                self.assertEqual(book.sheetnames, [SUMMARY])
                headers, row = list(book.active.values)
                self.assertEqual(headers, tuple(SUMMARY_FIELDS[:14]))
                self.assertEqual(row[:3], ('车型甲', '甲一期', tier))
                self.assertEqual(row[8], datetime(2026, 11, 4))
                self.assertEqual(row[12], 3)
            finally:
                book.close()

    def test_campaign_publication_history_excludes_initial_version_and_drafts(self):
        def history(query=''):
            with urlopen(self.base + 'campaign/history' + query) as response:
                return json.load(response)

        self.assertEqual(history(), {'history': [], 'total': 0, 'page': 1})
        with self.post('save', {'revision': 0, 'edits': [
                {'sheet': SUMMARY, 'row': 2, 'col': 1, 'value': '车型甲'}]}) as response:
            draft_revision = json.load(response)['revision']
        self.assertEqual(history()['total'], 0)
        with self.post('publish', {'revision': draft_revision, 'edits': []}) as response:
            published_revision = json.load(response)['publishedRevision']
        entry, = history()['history']
        self.assertEqual((entry['id'], entry['kind']), (published_revision, '发布'))
        self.assertTrue(entry['actor'])
        self.assertIsInstance(datetime.fromisoformat(entry['time']), datetime)
        self.assertEqual(history('?q=does-not-match')['total'], 0)

    def test_invalid_upload_and_missing_csrf_leave_empty_draft(self):
        for body, token, status in (({'revision':0,'content':'bad'}, None, 400),
                                    ({'revision':0,'content':'anything'}, 'invalid', 403)):
            with self.assertRaises(HTTPError) as failed:
                self.post('import', body, token)
            self.assertEqual(failed.exception.code, status)
            failed.exception.close()
        with urlopen(self.base + 'campaign/state') as response:
            state = json.load(response)
            self.assertEqual(state['revision'], 0)
            self.assertFalse(state['hasUnpublishedChanges'])
