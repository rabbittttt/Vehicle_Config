import json
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openpyxl import load_workbook
from catalog import Store
from server import make_server


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
