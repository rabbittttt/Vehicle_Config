from copy import deepcopy
from io import BytesIO
import unittest

from openpyxl import load_workbook

from stage_workbook import (
    DAILY, PRIMARY, SECONDARY, SUMMARY, apply_edits, empty_workbook, export_workbook,
    import_workbook, module_import_edits, validate_publication,
)


DAYS = ['2026-05-01', '2026-05-02']
HEADER = [PRIMARY, *DAYS, '合计', SECONDARY]


def legacy_workbook(daily_rows, models=(), *, daily_only=False):
    book = load_workbook(BytesIO(export_workbook(empty_workbook())))
    try:
        daily = book[DAILY]
        daily.delete_rows(1, daily.max_row)
        for row in daily_rows:
            daily.append(row)
        for model in models:
            book[SUMMARY].append([model, '首销'])
        if daily_only:
            for sheet in list(book):
                if sheet != daily:
                    book.remove(sheet)
        out = BytesIO()
        book.save(out)
        return out.getvalue()
    finally:
        book.close()


def make_daily_blocks(groups):
    rows = []
    expected = {}
    block_sums = []
    for group in groups:
        rows.append(HEADER)
        first = len(rows) + 1
        for number in group:
            model = f'车型{number}'
            counts = [number, number + 1]
            expected[model] = counts
            n = len(rows) + 1
            rows.append([model, *counts, f'=SUM(B{n}:C{n})', '首销'])
        last = len(rows)
        rows.append([])
        n = len(rows) + 1
        rows.append(['合计', f'=SUM(B{first}:B{last})', f'=SUM(C{first}:C{last})',
                     f'=SUM(B{n}:C{n})'])
        rows.append([])
        block_sums.append([sum(group), sum(group) + len(group)])
    return rows, expected, block_sums


class DailyLayoutTests(unittest.TestCase):
    def test_totals_follow_each_date_block_with_different_model_counts(self):
        for groups in ((range(2), range(2, 8)), (range(17), range(17, 20))):
            with self.subTest(models=[len(group) for group in groups]):
                rows, expected, block_sums = make_daily_blocks(groups)
                data = import_workbook(legacy_workbook(rows, expected))
                validate_publication(data)
                daily = next(sheet for sheet in data['sheets'] if sheet['name'] == DAILY)
                self.assertEqual(len(daily['headerRows']), 2)
                models, footers = [], []
                for row in daily['rows']:
                    if not row or row[0] == PRIMARY:
                        continue
                    if row[0] == '合计':
                        footers.append(row[1:4])
                    else:
                        models.append((row[0], row[1:5]))
                self.assertEqual(models, [(model, [*counts, sum(counts), '首销'])
                                          for model, counts in expected.items()])
                self.assertEqual(footers, [[*counts, sum(counts)] for counts in block_sums])
                summary = next(sheet for sheet in data['sheets'] if sheet['name'] == SUMMARY)
                self.assertEqual({r[0]: r[15] for r in summary['rows'][1:]},
                                 {model: sum(counts) for model, counts in expected.items()})

    def test_title_note_and_blank_lines_before_the_first_real_header(self):
        rows = [[], ['小订by天'], ['备注：统计首销阶段的每日数量'], [], HEADER,
                ['车型甲', 2, 0, 2, '首销']]
        data = import_workbook(legacy_workbook(rows, ['车型甲']))
        validate_publication(data)
        self.assertEqual(data['sheets'][1]['rows'], [HEADER, ['车型甲', 2, 0, 2, '首销']])

    def test_sparse_secondary_column_and_shorter_date_range_in_next_block(self):
        first_head = [PRIMARY, *DAYS, '合计', None, None, SECONDARY]
        next_head = [PRIMARY, DAYS[1], '合计', None, None, None, SECONDARY]
        rows = [first_head, ['车型甲', 2, 6, 8, None, None, '首销'], ['合计'], [], [],
                next_head, ['车型乙', 0, 0, None, None, None, '首销'], ['合计']]
        data = import_workbook(legacy_workbook(rows, ['车型甲', '车型乙']))
        validate_publication(data)
        daily = data['sheets'][1]
        headers = [daily['rows'][n - 1] for n in daily['headerRows']]
        self.assertEqual(headers, [first_head, next_head])
        quantities = [r[15] for r in data['sheets'][0]['rows'][1:]]
        self.assertEqual(quantities, [8, 0])

    def test_note_columns_are_skipped_without_shifting_daily_formula_ranges(self):
        rows = [['说明', '分阶段填报', '单位为单'],
                [PRIMARY, DAYS[0], '备注', DAYS[1], '合计', SECONDARY, '填报说明'],
                ['车型甲', 0, '已核对', 3, '=SUM(B3:D3)', '首销', '#REF!'],
                ['备注：统计范围', '含早班', '附加说明'],
                [None, None, '=BAD()'],
                ['车型乙', 1, '=BAD()', 5, 13, '首销'],
                ['合计', '=SUM(B3:B6)', None, '=SUM(D3:D6)', '=SUM(E3:E6)']]
        data = import_workbook(legacy_workbook(rows,['车型甲','车型乙']))
        daily = data['sheets'][1]
        self.assertEqual([row for row in daily['rows'] if row], [
            HEADER, ['车型甲',0,3,3,'首销'], ['车型乙',1,5,6,'首销'], ['合计',1,8,9]])
        current = apply_edits(empty_workbook(), [
            dict(sheet=SUMMARY,row=n,col=col,value=value)
            for n,model in enumerate(['车型甲','车型乙'],2)
            for col,value in [(1,model),(2,'首销')]])
        rows[1].append('总计')
        rows[2].append('=BAD()')
        rows[2][4] = '#REF!'
        edits = module_import_edits(current,legacy_workbook(rows,daily_only=True),'reporting')
        self.assertEqual([(e['model'],e['date'],e['value']) for e in edits], [
            ('车型甲',DAYS[0],0), ('车型甲',DAYS[1],3), ('车型乙',DAYS[0],1), ('车型乙',DAYS[1],5)])

    def test_only_dated_cells_supply_daily_counts_with_or_without_total_columns(self):
        models = ['零单', '未填', '已填']
        current = apply_edits(empty_workbook(), [
            dict(sheet=SUMMARY, row=n, col=col, value=value)
            for n, model in enumerate(models, 2)
            for col, value in ((1, model), (2, '首销'))
        ])
        reordered = [[PRIMARY, '总计', DAYS[1], SECONDARY, '合计', DAYS[0]],
                     ['零单', '#REF!', 0, '首销', '=BAD()'],
                     ['未填', '=NA()', None, '首销', 99],
                     ['已填', 123, 2, '首销', '未核对', 3],
                     ['总计', '=BAD()', '=SUM(C2:C4)', None, '#VALUE!', '=SUM(F2:F4)']]
        no_totals = [[PRIMARY, SECONDARY, *DAYS],
                     ['零单', '首销', None, 0], ['未填', '首销'], ['已填', '首销', 3, 2],
                     ['合计', None, '=SUM(C2:C4)', '=SUM(D2:D4)']]
        for rows in (reordered, no_totals):
            with self.subTest(header=rows[0]):
                data = import_workbook(legacy_workbook(rows, models))
                validate_publication(data)
                head, *records = data['sheets'][1]['rows']
                self.assertEqual(head.count('合计'), 1)
                records = {row[0]: dict(zip(head, row)) for row in records}
                for model, counts in {'零单': (None, 0, 0), '未填': (None, None, None),
                                      '已填': (3, 2, 5), '合计': (3, 2, 5)}.items():
                    self.assertEqual(tuple(records[model].get(key) for key in (*DAYS, '合计')), counts)
                edits = module_import_edits(current, legacy_workbook(rows, daily_only=True), 'reporting')
                self.assertCountEqual([(e['model'], e['date'], e['value']) for e in edits],
                                      [('零单', DAYS[1], 0), ('已填', DAYS[0], 3), ('已填', DAYS[1], 2)])

    def test_full_export_recalculates_stored_totals_without_changing_the_edition(self):
        data = empty_workbook()
        daily = data['sheets'][1]
        daily['rows'] = [HEADER.copy(), ['零单', 0, None, 100, '首销'],
                         ['未填', None, None, 123, '首销'], ['已填', 2, 3, 999, '首销'],
                         ['合计', 8, 9, 77]]
        original = deepcopy(data)
        book = load_workbook(BytesIO(export_workbook(data)), data_only=True)
        try:
            self.assertEqual([book[DAILY].cell(row, 4).value for row in range(2, 6)], [0, None, 5, 17])
            self.assertEqual([book[DAILY].cell(5, col).value for col in (2, 3)], [8, 9])
        finally:
            book.close()
        self.assertEqual(data, original)

    def test_note_handling_preserves_validation_of_dates_and_misplaced_quantities(self):
        for rows in ([[PRIMARY,'2026-05-32','说明',SECONDARY],['车型甲',4,'备注','首销']],
                     [['说明','0'],HEADER]):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError,'小订by天 第 1 行'):
                    import_workbook(legacy_workbook(rows,['车型甲']))
                with self.assertRaisesRegex(ValueError,'小订by天 第 1 行'):
                    module_import_edits(empty_workbook(),legacy_workbook(rows,daily_only=True),'reporting')
        rows = [[PRIMARY, DAYS[0], '备注'], ['车型甲', 1, '#REF!', 42]]
        with self.assertRaisesRegex(ValueError,'小订by天.*D2'):
            import_workbook(legacy_workbook(rows))
        rows = [[PRIMARY, '合计', DAYS[0], '备注', DAYS[1], SECONDARY],
                ['车型甲', '=SUM(C2:E2)', 1]]
        self.assertEqual(import_workbook(legacy_workbook(rows))['sheets'][1]['rows'][1], ['车型甲',1,1])

    def test_reporting_import_matches_stages_and_dates_across_both_blocks(self):
        current = apply_edits(empty_workbook(), [
            dict(sheet=SUMMARY, row=2, col=1, value='车型甲'),
            dict(sheet=SUMMARY, row=2, col=2, value='首销'),
            dict(sheet=SUMMARY, row=3, col=1, value='车型乙'),
            dict(sheet=SUMMARY, row=3, col=2, value='首销'),
        ])
        rows = [['小订by天'], [], HEADER, ['车型乙', 7, 0, '=SUM(B4:C4)', '首销'], [],
                [PRIMARY, SECONDARY, DAYS[1], DAYS[0], '合计'],
                ['车型甲', '首销', 5, 3, '=SUM(C7:D7)']]
        raw = legacy_workbook(rows, daily_only=True)
        edits = module_import_edits(current, raw, 'reporting')
        self.assertCountEqual([(e['model'], e['secondary'], e['date'], e['value']) for e in edits],
                              [('车型甲', '首销', DAYS[0], 3), ('车型甲', '首销', DAYS[1], 5),
                               ('车型乙', '首销', DAYS[0], 7), ('车型乙', '首销', DAYS[1], 0)])
        self.assertEqual(module_import_edits(apply_edits(current, edits), raw, 'reporting'), [])

    def test_invalid_data_surrounded_by_blank_rows_is_still_reported(self):
        for row in (['车型甲', '错误数量', 1, None, '首销'], ['车型甲', '=UNSUPPORTED()', 1, None, '首销']):
            with self.subTest(row=row):
                raw = legacy_workbook([HEADER, [], row], ['车型甲'])
                with self.assertRaisesRegex(ValueError, '小订by天.*(第 3 行|B3)'):
                    import_workbook(raw)
                with self.assertRaisesRegex(ValueError, '小订by天.*(第 3 行|B3)'):
                    module_import_edits(empty_workbook(), raw, 'reporting')
        raw = legacy_workbook([HEADER, [], [None, 0, 1]])
        with self.assertRaisesRegex(ValueError, '小订by天.*(第 3 行|B3)'):
            validate_publication(import_workbook(raw))
        raw = legacy_workbook([[], ['车型甲', 12], HEADER], ['车型甲'])
        with self.assertRaisesRegex(ValueError, '小订by天.*(第 2 行|B2)'):
            import_workbook(raw)

    def test_uncached_footer_cannot_include_the_previous_block(self):
        rows = [HEADER, ['车型甲', 3, 4, 7, '首销'], [], HEADER, ['车型乙', 7, 8, 15, '首销'],
                ['合计', '=SUM(B2:B5)']]
        with self.assertRaisesRegex(ValueError, '小订by天.*B6'):
            import_workbook(legacy_workbook(rows, ['车型甲', '车型乙']))
