import base64
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
from stage_workbook import DAILY, GROSS, PROGRESS_FIELDS, REVIEW_COLUMNS, SUMMARY, SUMMARY_FIELDS


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
                 {'sheet':'小订by天','model':'测试车型','date':'2026-11-26','value':12},
                 {'sheet':'小转大进度','model':'测试车型','day':1,'value':0},
                 {'sheet':'直接大定进度','model':'测试车型','day':1,'value':4}]
        with self.post('save', {'revision':0,'edits':edits,'owner':'测试编辑'}) as response:
            draft = json.load(response)
        with urlopen(self.base + 'campaign/published') as response:
            self.assertEqual(json.load(response)['revision'], 0)
        with self.post('publish', {'revision':draft['revision'],'edits':[],'owner':'测试发布'}) as response:
            self.assertEqual(json.load(response)['publishedRevision'], 1)
        with urlopen(self.base + 'campaign/published?revision=1') as response:
            edition = json.load(response)
            self.assertEqual(edition['sheets'][0]['rows'][1][0], '测试车型')
            gross = next(sheet for sheet in edition['sheets'] if sheet['name'] == '大定进度')
            self.assertEqual(gross['rows'][1][gross['rows'][0].index('D1')], 4)
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

    def test_module_templates_fill_and_import_without_copying_draft_rows(self):
        def template(scope):
            with urlopen(self.base+'campaign/template?scope='+scope) as response:
                self.assertEqual(response.headers.get_content_type(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                raw = response.read()
            self.assertEqual(preview(scope, raw), [])
            book = load_workbook(BytesIO(raw))
            self.addCleanup(book.close)
            self.assertTrue(all(sheet.max_row==1 for sheet in book))
            return book

        def preview(scope, raw):
            with self.post('module-import-preview', {
                'scope':scope, 'revision':revision, 'name':'已填写的模板.xlsx',
                'content':base64.b64encode(raw).decode('ascii'),
            }) as response:
                return json.load(response)['edits']

        def fill_and_save(scope, book):
            output = BytesIO()
            book.save(output)
            edits = preview(scope, output.getvalue())
            with self.post('save', {'revision':revision,'edits':edits,'owner':'验收'}) as response:
                return json.load(response)

        revision = 0
        schedule = template('schedule')
        self.assertEqual(schedule.sheetnames,[SUMMARY])
        self.assertEqual(list(next(schedule.active.values)),SUMMARY_FIELDS[:14])
        schedule.active.append(['车型甲','阶段一','高配','纯电','发布','下午','晚上',
                                datetime(2026,11,1),datetime(2026,11,2)])
        state = fill_and_save('schedule',schedule)
        revision = state['revision']
        self.assertEqual(state['sheets'][0]['rows'][1][7:9],['2026-11-01','2026-11-02'])
        template('schedule')  # The saved vehicle must not appear in a new template.

        report = template('reporting')
        self.assertEqual(set(report.sheetnames), {SUMMARY,DAILY,*PROGRESS_FIELDS} - {GROSS})
        self.assertIsInstance(report[DAILY]['C1'].value,datetime)
        report[DAILY]['C1'] = datetime(2026,11,7)
        report[DAILY].append(['车型甲','阶段一',0])
        for name in set(PROGRESS_FIELDS)-{GROSS}:
            self.assertEqual(report[name]['C1'].value,'D1')
            report[name].append(['车型甲','阶段一',5])
        review = report[SUMMARY]
        columns = list(next(review.values))
        self.assertEqual(columns, [*SUMMARY_FIELDS[:2],*[SUMMARY_FIELDS[i-1] for i in sorted(REVIEW_COLUMNS)]])
        review.append(['车型甲','阶段一'])
        review.cell(2,columns.index('首销期锁单')+1,7)
        state = fill_and_save('reporting',report)
        sheets = {item['name']:item['rows'] for item in state['sheets']}
        self.assertEqual(sheets[DAILY][1][sheets[DAILY][0].index('2026-11-07')],0)
        self.assertEqual(sheets[SUMMARY][1][SUMMARY_FIELDS.index('首销期锁单')],7)
        for name in set(PROGRESS_FIELDS)-{GROSS}:
            self.assertEqual(sheets[name][1][sheets[name][0].index('D1')],5)
        with self.assertRaises(HTTPError) as failed:
            urlopen(self.base+'campaign/template?scope=other')
        self.assertEqual(failed.exception.code,400)
        failed.exception.close()

    def test_schedule_only_exports_selected_stage_and_saved_edition(self):
        values = [(2, 1, '车型甲'), (2, 2, '甲一期'), (2, 3, '标准'),
                  (2, 9, '2026-11-04'), (2, 10, '2026-11-06'), (2, 16, 35),
                  (3, 1, '车型甲'), (3, 2, '甲二期')]
        edits = [{'sheet': SUMMARY, 'row': row, 'col': col, 'value': value}
                 for row, col, value in values]
        with self.post('publish', {'revision': 0, 'edits': edits, 'owner':'测试发布'}) as response:
            revision = json.load(response)['revision']
        with self.post('save', {'revision': revision, 'owner':'测试编辑', 'edits': [
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
        with self.post('save', {'revision': 0, 'owner':'测试编辑', 'edits': [
                {'sheet': SUMMARY, 'row': 2, 'col': 1, 'value': '车型甲'}]}) as response:
            draft_revision = json.load(response)['revision']
        self.assertEqual(history()['total'], 0)
        with self.post('publish', {'revision': draft_revision, 'edits': [], 'owner':'测试发布'}) as response:
            published_revision = json.load(response)['publishedRevision']
        entry, = history()['history']
        self.assertEqual((entry['id'], entry['kind']), (published_revision, '发布'))
        self.assertEqual(entry['actor'], '测试发布')
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
