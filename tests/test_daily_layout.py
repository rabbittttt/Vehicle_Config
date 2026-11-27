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
