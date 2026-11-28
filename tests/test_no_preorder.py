"""Legacy campaign dates can explicitly say a model had no preorder period."""
from io import BytesIO
import unittest

from openpyxl import load_workbook

from stage_workbook import (DAILY, DIRECT, GROSS, SUMMARY, apply_edits, empty_workbook,
                            export_workbook, import_workbook, module_import_edits,
                            validate_publication)


def legacy_dates(start, end, with_counts=False, duration='=J2-I2+1'):
    book = load_workbook(BytesIO(export_workbook(empty_workbook())))
    try:
        summary = book[SUMMARY]
        summary.append(['车型甲', '首销', None, None, None, None, None, None,
                        start, end, '2026-05-10', '2026-05-11',
                        duration, '=L2-K2+1'])
        if with_counts:
            daily = book[DAILY]
            daily.delete_rows(1)
            daily.append(['代际名', '2026-05-01', '2026-05-02', '二级代际名'])
            daily.append(['车型甲', 3, 5, '首销'])
            direct = book[DIRECT]
            direct.insert_rows(2)
            values = {'代际名':'车型甲', '二级代际名':'首销', '小订开始日期':start,
                      '小订结束日期':end, '开始大定日期':'2026-05-10',
                      '小转大结束日期':'2026-05-11', 'D1':4, 'D2':0}
            for cell in direct[1]:
                if cell.value in values:
                    direct.cell(2,cell.column).value = values[cell.value]
        out = BytesIO()
        book.save(out)
        return out.getvalue()
    finally:
        book.close()


def summary_values(data):
    sheet = next(sheet for sheet in data['sheets'] if sheet['name']==SUMMARY)
    return dict(zip(sheet['rows'][0],sheet['rows'][1]))


class NoPreorderTests(unittest.TestCase):
    def test_na_means_no_preorder_without_affecting_launch_or_historical_counts(self):
        for marker in ('NA', '#N/A'):
            with self.subTest(marker=marker):
                data = import_workbook(legacy_dates(marker,marker,with_counts=True))
                validate_publication(data)
                for snapshot in (data, import_workbook(export_workbook(data))):
                    validate_publication(snapshot)
                    row = summary_values(snapshot)
                    self.assertEqual([row['小订开始日期'],row['小订结束日期']], ['NA','NA'])
                    self.assertIsNone(row.get('小订天数'))
                    self.assertIsNone(row.get('总小订'))
                    self.assertEqual([row['开始大定日期'],row['首销结束日期'],row['首销期天数']],
                                     ['2026-05-10','2026-05-11',2])
                    self.assertEqual(row['直接大定量'],4)
                    self.assertEqual(row['大定量'],4)
                    daily = next(s for s in snapshot['sheets'] if s['name']==DAILY)
                    self.assertEqual(daily['rows'][1][1:3],[3,5])

    def test_blank_preorder_dates_retain_existing_backfill(self):
        data = import_workbook(legacy_dates(None,None,with_counts=True))
        row = summary_values(data)
        self.assertEqual([row.get('小订开始日期'),row.get('小订结束日期')],[None,None])
        self.assertEqual(row['总小订'],8)
        self.assertEqual(row['直接大定量'],4)
        self.assertIsNone(row.get('大定量'))

    def test_missing_direct_order_day_is_unknown_even_without_preorders(self):
        initial = import_workbook(legacy_dates('NA','NA',with_counts=True))
        data = apply_edits(initial,[dict(sheet=DIRECT,model='车型甲',secondary='首销',day=2,value=None)])
        row = summary_values(data)
        self.assertIsNone(row.get('直接大定量'))
        self.assertIsNone(row.get('大定量'))
        gross = next(s for s in data['sheets'] if s['name']==GROSS)
        _,observation = gross['rows'][:2]
        days = dict(zip(gross['rows'][0],observation))
        self.assertEqual(days['D1'],4)
        self.assertIsNone(days.get('D2'))

    def test_one_marker_with_blank_partner_is_distinct_from_a_conflicting_real_date(self):
        for start,end in ((None,' n/a '),('=NA()',None)):
            with self.subTest(start=start,end=end):
                data = import_workbook(legacy_dates(start,end))
                row = summary_values(data)
                self.assertEqual([row['小订开始日期'],row['小订结束日期']],['NA','NA'])
        for start,end in (('NA','2026-05-11'),('2026-05-01','#N/A')):
            with self.subTest(start=start,end=end), self.assertRaisesRegex(ValueError,'车型汇总.*2'):
                import_workbook(legacy_dates(start,end))

    def test_invalid_first_sale_date_is_still_reported_for_a_no_preorder_model(self):
        book = load_workbook(BytesIO(legacy_dates('NA','NA')))
        try:
            book[SUMMARY]['K2'] = '#N/A'
            out = BytesIO()
            book.save(out)
        finally:
            book.close()
        with self.assertRaisesRegex(ValueError,'车型汇总 K2'):
            import_workbook(out.getvalue())

    def test_na_duration_formula_with_a_cached_excel_error(self):
        from tests.test_stage_workbook import CampaignWorkbookTests

        legacy = CampaignWorkbookTests().legacy
        fields = empty_workbook()['sheets'][0]['rows'][0]
        for marker,error in (('NA','#VALUE!'),('#N/A','#N/A')):
            with self.subTest(marker=marker):
                values = ['车型甲'] + [None]*7 + [marker,marker,'2026-05-10','2026-05-11',
                                                 '=J2-I2+1','=L2-K2+1']
                cache = {'M2':('e',error)}
                data = import_workbook(legacy(fields,values,cache))
                validate_publication(data)
                self.assertIsNone(summary_values(data).get('小订天数'))
                with self.assertRaisesRegex(ValueError,'车型汇总 N2'):
                    import_workbook(legacy(fields,values,{**cache,'N2':('e','#REF!')}))

    def test_schedule_import_can_set_and_reverse_an_explicit_no_preorder_period(self):
        initial = import_workbook(legacy_dates('2026-05-01','2026-05-02',with_counts=True))
        raw = legacy_dates('NA','NA')
        edits = module_import_edits(initial,raw,'schedule')
        disabled = apply_edits(initial,edits)
        validate_publication(disabled)
        self.assertEqual(module_import_edits(disabled,raw,'schedule'),[])
        row = summary_values(disabled)
        self.assertEqual([row['小订开始日期'],row['小订结束日期']],['NA','NA'])
        self.assertIsNone(row.get('小订天数'))
        self.assertIsNone(row.get('总小订'))
        self.assertEqual(row['直接大定量'],4)

        restore = legacy_dates('2026-05-01','2026-05-02')
        edits = module_import_edits(disabled,restore,'schedule')
        enabled = apply_edits(disabled,edits)
        validate_publication(enabled)
        self.assertEqual(module_import_edits(enabled,restore,'schedule'),[])
        row = summary_values(enabled)
        self.assertEqual([row['小订开始日期'],row['小订结束日期'],row['小订天数']],
                         ['2026-05-01','2026-05-02',2])
        self.assertEqual(row['总小订'],8)
        self.assertEqual(row['直接大定量'],4)

    def test_restoring_only_start_date_clears_na_and_old_duration_override(self):
        initial = import_workbook(legacy_dates('2026-05-01','2026-05-02',duration=7))
        self.assertEqual(summary_values(initial)['小订天数'],7)
        self.assertIn('2:13',initial['sheets'][0]['literalCells'])

        disabled = apply_edits(initial,[dict(sheet=SUMMARY,row=2,col=10,value='NA')])
        self.assertIsNone(summary_values(disabled).get('小订天数'))
        self.assertNotIn('2:13',disabled['sheets'][0]['literalCells'])
        partial = apply_edits(disabled,[dict(sheet=SUMMARY,row=2,col=9,value='2026-05-01')])
        validate_publication(partial)
        row = summary_values(partial)
        self.assertEqual(row['小订开始日期'],'2026-05-01')
        self.assertIsNone(row.get('小订结束日期'))
        self.assertIsNone(row.get('小订天数'))
        complete = apply_edits(partial,[dict(sheet=SUMMARY,row=2,col=10,value='2026-05-03')])
        self.assertEqual(summary_values(complete)['小订天数'],3)

    def test_independent_historical_preorder_window_stays_together(self):
        data = import_workbook(legacy_dates('2026-05-01','2026-05-02',with_counts=True))
        direct = next(s for s in data['sheets'] if s['name']==DIRECT)
        start_col = direct['rows'][0].index('小订开始日期')+1
        history = apply_edits(data,[dict(sheet=DIRECT,row=2,col=start_col,value='2026-04-30')])
        disabled = apply_edits(history,[dict(sheet=SUMMARY,row=2,col=9,value='NA')])
        for snapshot in (disabled, import_workbook(export_workbook(disabled))):
            validate_publication(snapshot)
            row = summary_values(snapshot)
            self.assertEqual([row['小订开始日期'],row['小订结束日期']],['NA','NA'])
            direct = next(s for s in snapshot['sheets'] if s['name']==DIRECT)
            detail = dict(zip(direct['rows'][0],direct['rows'][1]))
            self.assertEqual([detail['小订开始日期'],detail['小订结束日期']],
                             ['2026-04-30','2026-05-02'])
