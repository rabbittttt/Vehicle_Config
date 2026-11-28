from io import BytesIO
import unittest

from openpyxl import Workbook, load_workbook

from stage_workbook import DAILY, SUMMARY, apply_edits, empty_workbook, export_workbook, module_import_edits


def summary_cell(row, col, value):
    return dict(sheet=SUMMARY, row=row, col=col, value=value)


def daily_entry(stage, date, value):
    return dict(sheet=DAILY, model='测试车型', secondary=stage, date=date, value=value)


def change_export(data, selection, changes):
    exported = export_workbook(data, selection)
    book = load_workbook(BytesIO(exported))
    try:
        for sheet, address, value in changes:
            book[sheet][address] = value
        result = BytesIO()
        book.save(result)
        return result.getvalue()
    finally:
        book.close()


class ModuleImportTests(unittest.TestCase):
    def setUp(self):
        edits = [
            summary_cell(2, 1, '测试车型'), summary_cell(2, 2, '一期'), summary_cell(2, 3, '标准'),
            summary_cell(2, 26, 28), summary_cell(3, 1, '测试车型'), summary_cell(3, 2, '二期'),
            summary_cell(3, 3, '运动'), summary_cell(3, 26, 9),
            daily_entry('一期', '2026-11-01', 8), daily_entry('一期', '2026-11-02', 6),
            daily_entry('二期', '2026-11-01', 12),
        ]
        self.data = apply_edits(empty_workbook(), edits)

    def assert_import_matches_edits(self, raw, scope, expected_edits):
        edits = module_import_edits(self.data, raw, scope)
        actual = apply_edits(self.data, edits)
        expected = apply_edits(self.data, expected_edits)
        self.assertEqual({s['name']: s['rows'] for s in actual['sheets']},
                         {s['name']: s['rows'] for s in expected['sheets']})

    def test_schedule_round_trip_matches_stage_and_preserves_reporting(self):
        selection = {'scope': 'schedule', 'models': ['二期']}
        self.assert_import_matches_edits(export_workbook(self.data, selection), 'schedule', [])
        raw = change_export(self.data, selection, [(SUMMARY, 'C2', '高配')])
        self.assert_import_matches_edits(raw, 'schedule', [summary_cell(3, 3, '高配')])

    def test_schedule_import_ignores_other_sheets_and_business_metrics(self):
        book = load_workbook(BytesIO(export_workbook(self.data)))
        try:
            summary = book[SUMMARY]
            summary['C2'] = '增配'
            summary['P2'] = '=NA()'
            summary['AD1'] = '总小订'
            summary['AE1'] = '临时指标'
            summary['AE2'] = '#REF!'
            book.create_sheet('小订按天')['XFD1'] = '=NA()'
            out = BytesIO()
            book.save(out)
            self.assert_import_matches_edits(out.getvalue(), 'schedule', [summary_cell(2, 3, '增配')])
            summary['I2'] = '=1/0'
            out = BytesIO()
            book.save(out)
            with self.assertRaisesRegex(ValueError, '车型汇总 I2'):
                module_import_edits(self.data, out.getvalue(), 'schedule')
        finally:
            book.close()

    def test_reporting_daily_round_trip_preserves_other_dates_and_accepts_zero(self):
        selection = {'sheets': [DAILY], 'models': ['一期'], 'dateFrom': '2026-11-02', 'dateTo': '2026-11-02'}
        self.assert_import_matches_edits(export_workbook(self.data, selection), 'reporting', [])
        raw = change_export(self.data, selection, [(DAILY, 'B2', 0)])
        self.assert_import_matches_edits(raw, 'reporting', [daily_entry('一期', '2026-11-02', 0)])

    def test_reporting_summary_ignores_schedule_fields_and_updates_selected_stage(self):
        selection = {'sheets': [SUMMARY], 'models': ['二期']}
        raw = change_export(self.data, selection, [
            (SUMMARY, 'C2', '外部资料'), (SUMMARY, 'I2', '无效日程日期'),
            (SUMMARY, 'W1', '小订后退定\n 占比'), (SUMMARY, 'W2', '=NA()'),
            (SUMMARY, 'Z1', '首销期\n 留存大定'), (SUMMARY, 'Z2', 0),
            (SUMMARY, 'AC2', '=IFERROR(AB2/Z2,"")'),
        ])
        self.assert_import_matches_edits(raw, 'reporting', [summary_cell(3, 26, 0)])

    def test_reporting_import_reads_only_model_quantities_and_validates_their_formulas(self):
        name = '小转大进度'
        def workbook(daily_value, progress_value):
            book = Workbook()
            daily = book.active
            daily.title = DAILY
            daily.append(['代际名','2026-11-01','合计','二级代际名'])
            daily.append(['测试车型',daily_value,'=BROKEN()','一期'])
            daily.append(['总计','=BROKEN()','=BROKEN()'])
            daily.append(['代际名','2026-11-02','合计','二级代际名'])
            daily.append(['测试车型',0,'=BROKEN()','一期'])
            progress = book.create_sheet(name)
            progress.append(['代际名','二级代际名','留资','总小订','D1'])
            progress.append(['测试车型','一期','=NOW()','=BROKEN()',progress_value])
            progress.append(['合计',None,None,None,'=BROKEN()'])
            progress.append(['=BROKEN()','未填报',None,'=BROKEN()'])
            progress.append(['代际名','比例说明','D1'])
            progress.append(['测试车型',None,'=BROKEN()'])
            with BytesIO() as stream:
                book.save(stream)
                book.close()
                return stream.getvalue()

        raw = workbook(5, 7)
        self.assert_import_matches_edits(raw, 'reporting', [
            daily_entry('一期','2026-11-01',5), daily_entry('一期','2026-11-02',0),
            dict(sheet=name,model='测试车型',secondary='一期',day=1,value=7)])
        for daily_value, progress_value, address in (('=BROKEN()',7,'B2'), (5,'=BROKEN()','E2')):
            with self.subTest(address=address), self.assertRaisesRegex(ValueError,address):
                module_import_edits(self.data,workbook(daily_value,progress_value),'reporting')

    def test_duplicate_daily_observations_only_accept_unchanged_values(self):
        data = empty_workbook()
        daily = next(sheet for sheet in data['sheets'] if sheet['name'] == DAILY)
        daily['rows'] = [['代际名', '2026-11-01', '合计', '二级代际名'],
                         ['测试车型', 8, 8, '一期'], ['测试车型', 12, 12, '一期']]
        self.assertEqual(module_import_edits(data, export_workbook(data), 'reporting'), [])
        for changed in (16, 12):
            with self.subTest(changed=changed):
                raw = change_export(data, {}, [(DAILY, 'B2', changed)])
                with self.assertRaisesRegex(ValueError, '2026-11-01.*匹配多条记录'):
                    module_import_edits(data, raw, 'reporting')


if __name__ == '__main__':
    unittest.main()
