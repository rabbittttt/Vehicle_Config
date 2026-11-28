import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import load_workbook

from campaign_store import CampaignStore, encode
from campaign_merge import MergeConflict
from catalog import Conflict
from stage_workbook import export_workbook


def edit(column, value):
    return {'sheet': '车型汇总', 'row': 2, 'col': column, 'value': value}


class CampaignStoreTests(unittest.TestCase):
    def test_concurrent_new_progress_rows_keep_their_independent_end_dates(self):
        name = '小转大进度'
        def quantity(model, value):
            return dict(sheet=name,model=model,day=1,value=value)
        def progress(state):
            return next(sheet for sheet in state['sheets'] if sheet['name']==name)['rows']
        initial = self.store.save(0,[edit(1,'车型甲'),dict(sheet='车型汇总',row=3,col=1,value='车型乙')],'初始')
        revision = initial['revision']
        pending = [quantity('车型甲',4)]
        rows = progress(self.store.preview(revision,pending))
        row = next(i for i,r in enumerate(rows,1) if r and r[0]=='车型甲')
        col = rows[0].index('小转大结束日期')+1
        pending.append(dict(sheet=name,row=row,col=col,value='2026-12-21'))
        self.store.save(revision,[quantity('车型乙',9)],'编辑乙')
        visible = self.store.save(revision,pending,'编辑甲',merge=True)
        self.assertFalse(any(r and r[0]=='车型乙' for r in progress(visible)))
        shared = progress(self.store.get())
        day = shared[0].index('D1')
        section = shared[1:next(n for n,r in enumerate(shared[1:],1) if r and r[0]=='代际名')]
        values = {r[0]:(r[day],r[col-1]) for r in section if r and r[0]}
        self.assertEqual(values, {'车型甲':(4,'2026-12-21'),'车型乙':(9,None)})

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CampaignStore(self.temp.name)
        self.edits = [edit(1, '测试车型 2026款'), edit(5, '标准发布'),
                      edit(9, '2026-01-01'), edit(10, '2026-01-07'),
                      edit(11, '2026-01-08'), edit(12, '2026-01-31')]

    def test_daily_export_reimport_ignores_total_column_but_detects_changed_observations(self):
        book = load_workbook(BytesIO(export_workbook(self.store.preview(0, self.edits))))
        daily = book['小订by天']
        daily.delete_rows(1, daily.max_row)
        daily.append(['代际名', '2026-01-01', '总计', '2026-01-02', '二级代际名'])
        daily.append(['测试车型 2026款', 0, 999, 3])
        legacy = BytesIO()
        book.save(legacy)
        book.close()

        saved = self.store.import_legacy(0, legacy.getvalue(), '甲')
        revision = saved['revision']
        raw = self.store.export({}, source='draft')
        check = self.store.preview_import(revision, raw)
        self.assertTrue(check['identical'])
        self.assertFalse(check['requiresConfirmation'])
        self.assertFalse(any(t['changed'] for t in check['changes']['tables']))
        self.assertEqual(self.store.import_legacy(revision, raw, '甲'), saved)

        book = load_workbook(BytesIO(raw))
        daily = book['小订by天']
        self.assertEqual([cell.value for cell in daily[1]][-1], '二级代际名')
        self.assertNotIn('合计', [cell.value for cell in daily[1]])
        daily['B2'] = None  # Clearing a confirmed zero changes the observations.
        changed = BytesIO()
        book.save(changed)
        book.close()
        check = self.store.preview_import(revision, changed.getvalue())
        self.assertFalse(check['identical'])
        self.assertTrue(check['requiresConfirmation'])
        self.assertTrue(next(t for t in check['changes']['tables'] if t['name'] == '小订by天')['changed'])
        with self.assertRaisesRegex(ValueError, '确认替换'):
            self.store.import_legacy(revision, changed.getvalue(), '乙')

    def test_legacy_preview_and_import_never_open_unused_worksheets(self):
        quantities = [dict(sheet=sheet, model='测试车型 2026款', day=1, value=count)
                      for sheet, count in [('小转大进度', 3), ('直接大定进度', 4)]]
        data = self.store.preview(0, self.edits + quantities)
        workbook = load_workbook(BytesIO(export_workbook(data)))
        ignored_paths = set()
        for title in ('大定进度', '总大定进度', '小订进度', '比武参考'):
            sheet = workbook[title] if title in workbook.sheetnames else workbook.create_sheet(title)
            sheet['A1'] = '#REF!'
            ignored_paths.add(f'xl/worksheets/sheet{workbook.index(sheet) + 1}.xml')
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        raw = stream.getvalue()
        open_member = ZipFile.open

        def check_member(archive, name, *args, **kwargs):
            self.assertNotIn(getattr(name, 'filename', name), ignored_paths)
            return open_member(archive, name, *args, **kwargs)

        with patch.object(ZipFile, 'open', check_member):
            preview = self.store.preview_import(0, raw)
            self.assertFalse(preview['identical'])
            result = self.store.import_legacy(0, raw, '测试')
        self.assertEqual(result['sheets'][0]['rows'][1][0], '测试车型 2026款')
        gross = next(sheet for sheet in result['sheets'] if sheet['name'] == '大定进度')
        self.assertEqual(gross['rows'][1][gross['rows'][0].index('D1')], 7)

    def test_legacy_import_reads_daily_counts_and_skips_percentage_errors(self):
        inputs = [('小订期退订进度', '小订结束日退订率', 0, 0), ('小转大进度', '小订转化率', 7, .5),
                  ('直接大定进度', '直接大定占比', 3, .5), ('首销期退订进度', '退订率', 1, .05)]
        data = self.store.preview(0, self.edits + [edit(16, 20), edit(17, 14), edit(20, 6)])
        book = load_workbook(BytesIO(export_workbook(data)))
        try:
            for name, rate, count, _ in inputs:
                sheet = book[name]
                sheet.delete_rows(1, sheet.max_row)
                sheet.append(['代际名', '二级代际名', 'D1', 'D2', rate])
                sheet.append(['测试车型 2026款', None, count, None, '#REF!'])
                sheet.append(['代际名', '二级代际名', '旧百分比列'])
                sheet.append(['仅在百分比区', None, '=1/0'])
            book['首销期退订进度']['C10001'] = '#DIV/0!'
            stream = BytesIO()
            book.save(stream)
            book['小转大进度']['C2'] = '#REF!'
            invalid = BytesIO()
            book.save(invalid)
        finally:
            book.close()
        raw = stream.getvalue()
        self.assertFalse(self.store.preview_import(0, raw)['identical'])
        result = self.store.import_legacy(0, raw, '测试')
        sheets = {s['name']:s for s in result['sheets']}
        for name, _, count, expected_rate in inputs:
            with self.subTest(sheet=name):
                rows = sheets[name]['rows']
                quantity = dict(zip(*rows[:2]))
                self.assertEqual(quantity['D1'], count)
                self.assertIsNone(quantity.get('D2'))
                self.assertNotIn('仅在百分比区', [row[0] for row in rows if row])
                curve = rows[sheets[name]['headerRows'][1]]
                self.assertEqual(curve[rows[0].index('D1')], expected_rate)
        direct = sheets['直接大定进度']
        daily_share = direct['rows'][direct['headerRows'][2]]
        self.assertEqual(daily_share[direct['rows'][0].index('D1')], 3 / (3 + 7))
        revision = result['revision']
        self.assertTrue(self.store.preview_import(revision, raw)['identical'])
        with self.assertRaisesRegex(ValueError, '小转大进度.*C2.*#REF!'):
            self.store.preview_import(revision, invalid.getvalue())
        with self.assertRaisesRegex(ValueError, '小转大进度.*C2.*#REF!'):
            self.store.import_legacy(revision, invalid.getvalue(), '测试', confirm_replace=True)
        self.assertEqual(self.store.get(), result)

    def test_preview_draft_then_publish(self):
        initial = self.store.get()
        preview = self.store.preview(0, self.edits)
        self.assertTrue(preview['hasUnpublishedChanges'])
        self.assertEqual(self.store.get(), initial)
        draft = self.store.save(0, self.edits, '甲')
        self.assertEqual(draft['sheets'], preview['sheets'])
        self.assertFalse(self.store.snapshot.exists())
        self.assertEqual(self.store.published()['revision'], 0)
        with self.assertRaises(Conflict):
            self.store.save(0, self.edits, '乙', publish=True)
        state = self.store.save(1, [], '甲', publish=True)
        self.assertFalse(state['hasUnpublishedChanges'])
        self.assertEqual(state['publishedRevision'], 1)
        self.assertEqual(json.loads(self.store.snapshot.read_text('utf-8')), self.store.published())
        self.assertEqual(CampaignStore(self.temp.name).get(), state)
        self.assertFalse(list(Path(self.temp.name).rglob('*.xlsx')))

    def test_legacy_period_header_survives_edit_and_export(self):
        previous = self.store.save(0, [*self.edits, edit(6, '上午')], '甲', publish=True)
        legacy = {'sheets': previous['sheets']}
        legacy['sheets'][0]['rows'][0][5] = '小订发布时间'
        payload = encode(legacy)
        with self.store._write() as db:
            db.execute('UPDATE publications SET data=? WHERE revision=1', (payload,))
            db.execute('UPDATE draft SET data=?', (payload,))

        draft = self.store.get()
        self.assertFalse(draft['hasUnpublishedChanges'])
        self.assertEqual(self.store.save(draft['revision'], [], '乙')['revision'], draft['revision'])
        for source in ('draft', 'published'):
            with BytesIO(self.store.export({'scope':'schedule'}, source=source)) as stream:
                workbook = load_workbook(stream, read_only=True)
                header, row = workbook.active.values
                self.assertEqual((header[5], row[5]), ('小订发布时段', '上午'))
                workbook.close()
        self.store.save(draft['revision'], [edit(6, '晚上')], '乙', publish=True)
        rows = self.store.published()['sheets'][0]['rows']
        self.assertEqual((rows[0][5], rows[1][5], rows[1][8]), ('小订发布时段','晚上','2026-01-01'))
        self.assertEqual(self.store.published(1)['sheets'], legacy['sheets'])

    def test_old_cancel_rate_headers_in_stored_draft_and_published_edition(self):
        published = self.store.save(0, self.edits, '甲', publish=True)
        legacy = {'sheets': published['sheets']}
        name = '小订期退订进度'
        title = '小订结束日退订率'
        old = '小订期期结束日退订率'
        def rate_headers(data):
            sheet = next(s for s in data['sheets'] if s['name'] == name)
            return [sheet['rows'][number-1] for number in sheet['headerRows']]
        for header in rate_headers(legacy):
            header[header.index(title)] = old
        payload = encode(legacy)
        with self.store._write() as db:
            db.execute('UPDATE draft SET data=?', (payload,))
            db.execute('UPDATE publications SET data=? WHERE revision=1', (payload,))

        draft = self.store.get()
        self.assertFalse(draft['hasUnpublishedChanges'])
        self.assertEqual(self.store.publish_preview(draft['revision'], [])['changes'], [])
        for view in (draft, self.store.published(1, editor=True)):
            self.assertTrue(all(title in header and old not in header for header in rate_headers(view)))
        self.store.save(draft['revision'], [edit(6, '上午')], '乙', publish=True)
        self.assertTrue(all(title in header for header in rate_headers(self.store.published())))
        self.assertTrue(all(old in header for header in rate_headers(self.store.published(1))))

    def test_failed_publish_validation_keeps_existing_state(self):
        before = self.store.save(0, self.edits, '甲')
        with patch('campaign_store.validate_publication', side_effect=ValueError('日期错误')):
            with self.assertRaisesRegex(ValueError, '日期错误'):
                self.store.save(before['revision'], [], '甲', publish=True)
        self.assertEqual(self.store.get(), before)
        self.assertFalse(self.store.snapshot.exists())

    def test_publication_preview_combines_saved_and_local_changes_against_published(self):
        old_model = {**edit(1, '旧车型'), 'row':3}
        unrelated = {**edit(1, '其他车型'), 'row':4}
        baseline = self.store.save(0, [*self.edits, old_model, unrelated], '甲', publish=True)
        self.assertEqual(self.store.publish_preview(baseline['revision'], [])['changes'], [])
        draft = self.store.save(baseline['revision'], [edit(3, '新档位')], '乙')
        revision = draft['revision']
        saved_change, = self.store.publish_preview(revision, [])['changes']
        self.assertEqual((saved_change['name'], saved_change['updated'], saved_change['fields']),
                         ('车型汇总', 1, ['产品档位']))
        self.assertEqual(self.store.publish_preview(revision, [edit(3, None)])['changes'], [])

        preview = self.store.publish_preview(revision, [{**old_model, 'value':'新车型'}])
        self.assertEqual((preview['revision'], preview['publishedRevision'], preview['error']), (revision, 1, ''))
        change, = preview['changes']
        self.assertEqual((change['added'], change['updated'], change['removed']), (1, 1, 1))
        self.assertEqual((change['stageCount'], change['fieldCount']), (3, 2))
        self.assertCountEqual(change['stages'], [('测试车型 2026款', ''), ('旧车型', ''), ('新车型', '')])
        self.assertCountEqual(change['fields'], ['代际名', '产品档位'])
        self.assertEqual(self.store.get(), draft)
        self.assertEqual(self.store.history_page()['total'], 1)
        with self.assertRaises(Conflict):
            self.store.publish_preview(baseline['revision'], [])

    def test_invalid_publication_preview_returns_validation_and_preserves_draft_and_snapshot(self):
        draft = self.store.save(0, self.edits, '甲', publish=True)
        snapshot = self.store.snapshot.read_bytes()
        preview = self.store.publish_preview(draft['revision'], [edit(10, '2025-12-31')])
        self.assertIn('结束日期早于开始日期', preview['error'])
        self.assertEqual(preview['changes'][0]['updated'], 1)
        self.assertEqual(self.store.get(), draft)
        self.assertEqual(self.store.snapshot.read_bytes(), snapshot)
        self.assertEqual(self.store.history_page()['total'], 1)

    def test_existing_draft_backfills_without_changing_published_history(self):
        schedule = [*self.edits, edit(12, '2026-01-09'), edit(16, 20)]
        summary_before = self.store.preview(0, schedule)['sheets'][0]
        quantities = [{'sheet': '小转大进度', 'model': '测试车型 2026款', 'day': day, 'value': qty}
                      for day, qty in [(1, 0), (2, 5)]]
        self.store.save(0, schedule + quantities, '甲', publish=True)
        # Simulate the previous release: complete daily detail and an empty review field.
        old_data = {'sheets': self.store.published()['sheets']}
        old_data['sheets'][0] = summary_before
        with self.store._write() as db:
            db.execute('UPDATE draft SET data=?', (encode(old_data),))
            db.execute('UPDATE publications SET data=? WHERE revision=1', (encode(old_data),))
        store = CampaignStore(self.temp.name)
        old_snapshot = store.snapshot.read_bytes()
        draft = store.get()
        self.assertTrue(draft['hasUnpublishedChanges'])
        self.assertEqual(draft['sheets'][0]['rows'][1][16:18], [5, .25])
        self.assertEqual(store.preview(draft['revision'], [])['sheets'], draft['sheets'])
        for source, expected in [('published', None), ('draft', 5)]:
            file = BytesIO(store.export({'sheets': ['车型汇总']}, source))
            book = load_workbook(file, read_only=True, data_only=True)
            try:
                self.assertEqual(book['车型汇总']['Q2'].value, expected)
            finally:
                book.close()
        self.assertEqual(store.snapshot.read_bytes(), old_snapshot)
        state = store.save(draft['revision'], [], '乙', publish=True)
        self.assertEqual(state['publishedRevision'], 2)
        self.assertFalse(state['hasUnpublishedChanges'])
        self.assertEqual(store.published(1)['sheets'], old_data['sheets'])
        self.assertEqual(store.published(2)['sheets'], draft['sheets'])
        self.assertEqual(CampaignStore(self.temp.name).get(), state)

    def test_projection_failure_retains_old_snapshot_and_recovers(self):
        state = self.store.save(0, self.edits, '甲', publish=True)
        old = self.store.published()
        with patch.object(self.store, '_replace_snapshot', side_effect=PermissionError):
            state = self.store.save(state['revision'], [edit(12, '2026-02-01')], '甲', publish=True)
            self.assertTrue(state['projectionPending'])
            state = self.store.save(state['revision'], [edit(5, '新发布类型')], '甲', merge=True)
            self.assertTrue(state['projectionPending'])
            self.assertEqual(json.loads(self.store.snapshot.read_text('utf-8')), old)
        self.assertEqual(self.store.published(1), old)
        repaired = CampaignStore(self.temp.name)
        self.assertFalse(repaired.get()['projectionPending'])
        self.assertEqual(json.loads(repaired.snapshot.read_text('utf-8')), repaired.published(2))
        repaired.snapshot.write_text('broken', 'utf-8')
        CampaignStore(self.temp.name)
        self.assertEqual(json.loads(repaired.snapshot.read_text('utf-8')), repaired.published(2))

    def test_preview_replace_import_and_undo_preserve_published_and_unpublished_changes(self):
        published = self.store.save(0, self.edits, '甲', publish=True)
        previous_edition = self.store.snapshot.read_bytes()
        draft = self.store.save(published['revision'], [edit(12, '2026-02-10')], '乙')
        revision = draft['revision']
        replacement = self.store.preview(revision, [edit(1, '新车型'), edit(2, '纯电')])
        incoming = export_workbook({'sheets': replacement['sheets']})
        preview = self.store.preview_import(revision, incoming)
        self.assertTrue(preview['requiresConfirmation'])
        self.assertFalse(preview['identical'])
        self.assertEqual(preview['changes']['addedStages'], [{'generation':'新车型','subgeneration':'纯电'}])
        self.assertEqual(preview['changes']['removedStages'], [{'generation':'测试车型 2026款','subgeneration':''}])
        self.assertEqual(preview['changes']['tables'][0], {'name':'车型汇总','before':1,'after':1,'changed':True})
        self.assertEqual(self.store.get(), draft)
        for confirm in (False, 'false', 1):
            with self.assertRaisesRegex(ValueError, '确认'):
                self.store.import_legacy(revision, incoming, '甲', confirm_replace=confirm)
        imported = self.store.import_legacy(revision, incoming, '甲', confirm_replace=True)
        self.assertTrue(imported['canUndoImport'])
        self.assertTrue(imported['canImportLegacy'])
        self.assertTrue(imported['hasUnpublishedChanges'])
        self.assertEqual(self.store.snapshot.read_bytes(), previous_edition)
        with self.assertRaises(Conflict):
            self.store.import_legacy(revision, incoming, '甲', confirm_replace=True)
        check = self.store.preview_import(imported['revision'], incoming)
        self.assertTrue(check['identical'])
        self.assertFalse(check['requiresConfirmation'])
        self.assertEqual(self.store.import_legacy(imported['revision'], incoming, '甲'), imported)
        restored = CampaignStore(self.temp.name).undo_import(imported['revision'], '乙')
        self.assertEqual(restored['sheets'], draft['sheets'])
        self.assertEqual(self.store.snapshot.read_bytes(), previous_edition)
        self.assertFalse(restored['canUndoImport'])
        self.assertTrue(restored['hasUnpublishedChanges'])

    def test_undo_cannot_erase_a_later_change(self):
        initial = self.store.get()
        data = export_workbook(self.store.preview(0, self.edits))
        preview = self.store.preview_import(0, data)
        self.assertFalse(preview['requiresConfirmation'])
        self.assertEqual(self.store.get(), initial)
        imported = self.store.import_legacy(0, data, '甲')
        changed = self.store.save(imported['revision'], [edit(12, '2026-02-10')], '乙')
        self.assertFalse(changed['canUndoImport'])
        with self.assertRaises(Conflict):
            self.store.undo_import(imported['revision'], '甲')
        with self.assertRaisesRegex(ValueError, '没有可撤回'):
            self.store.undo_import(changed['revision'], '甲')
        self.assertEqual(self.store.get(), changed)

    def test_independent_clients_cannot_both_commit_one_revision(self):
        barrier = Barrier(2)

        def persist(actor):
            client = CampaignStore(self.temp.name)
            barrier.wait(timeout=10)
            try:
                client.save(0, [edit(1, actor)], actor)
                return 'saved'
            except Conflict:
                return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(persist, ['甲', '乙']), ['saved', 'conflict'])
        self.assertEqual(self.store.get()['revision'], 1)
        self.assertEqual(self.store.published()['revision'], 0)

    def test_simultaneous_nonoverlapping_saves_keep_the_editor_views_separate(self):
        start = self.store.save(0,self.edits,'初始化')
        barrier = Barrier(2)

        def save(change):
            barrier.wait(timeout=10)
            return self.store.save(start['revision'],[change],'编辑',merge=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            a,b = pool.map(save,[edit(3,'高配'),edit(4,'纯电')])
        self.assertEqual(a['sheets'][0]['rows'][1][2:4], ['高配',None])
        self.assertEqual(b['sheets'][0]['rows'][1][2:4], [None,'纯电'])
        latest = self.store.get()
        self.assertEqual(latest['sheets'][0]['rows'][1][2:4], ['高配','纯电'])
        detached = a if 'viewToken' in a else b
        self.assertEqual(detached['revision'],latest['revision'])
        with self.assertRaises(Conflict):
            self.store.save(detached['revision'],[],'编辑',publish=True,merge=True,view_token=detached['viewToken'])
        self.assertEqual(self.store.published()['revision'],0)

    def test_expired_view_recovers_from_verified_browser_copy_after_restart(self):
        first = self.store.save(0,self.edits,'初始化')
        self.store.save(first['revision'],[edit(3,'他人的档位')],'甲')
        private = self.store.save(first['revision'],[edit(4,'纯电')],'乙',merge=True)
        token = private['viewToken']
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE edit_bases SET saved_at=datetime('now','-31 days') WHERE token=?",(token,))
        self.store.save(private['revision'],[edit(5,'其他类型')],'甲')
        reopened = CampaignStore(self.temp.name)
        pending = [edit(6,'上午')]
        with self.assertRaises(MergeConflict) as refresh:
            reopened.preview(private['revision'],pending,merge=True,view_token=token)
        self.assertTrue(refresh.exception.details['needsBase'])
        with self.assertRaises(Conflict):
            reopened.save(private['revision'],pending,'乙',merge=True,view_token=token,
                          base_sheets=reopened.get()['sheets'])
        mine = reopened.save(private['revision'],pending,'乙',merge=True,view_token=token,
                             base_sheets=private['sheets'])
        self.assertEqual(mine['sheets'][0]['rows'][1][2:6],[None,'纯电','标准发布','上午'])
        self.assertEqual(reopened.get()['sheets'][0]['rows'][1][2:6],['他人的档位','纯电','其他类型','上午'])


if __name__ == '__main__':
    unittest.main()
