import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from campaign_store import CampaignStore, encode
from catalog import Conflict
from stage_workbook import export_workbook


def edit(column, value):
    return {'sheet': '车型汇总', 'row': 2, 'col': column, 'value': value}


class CampaignStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CampaignStore(self.temp.name)
        self.edits = [edit(1, '测试车型 2026款'), edit(5, '标准发布'),
                      edit(9, '2026-01-01'), edit(10, '2026-01-07'),
                      edit(11, '2026-01-08'), edit(12, '2026-01-31')]

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

    def test_failed_publish_validation_keeps_existing_state(self):
        before = self.store.save(0, self.edits, '甲')
        with patch('campaign_store.validate_publication', side_effect=ValueError('日期错误')):
            with self.assertRaisesRegex(ValueError, '日期错误'):
                self.store.save(before['revision'], [], '甲', publish=True)
        self.assertEqual(self.store.get(), before)
        self.assertFalse(self.store.snapshot.exists())

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


if __name__ == '__main__':
    unittest.main()
