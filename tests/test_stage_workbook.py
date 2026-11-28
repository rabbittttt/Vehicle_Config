import unittest
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from stage_workbook import (DAILY, SUMMARY, SUMMARY_FIELDS, apply_edits, empty_workbook, export_workbook,
                            import_workbook, module_import_edits, validate_publication)


def summary(row, **columns):
    return [{'sheet':SUMMARY,'row':row,'col':int(col),'value':v} for col,v in columns.items()]


def daily(model, secondary, day, count):
    return {'sheet':DAILY,'model':model,'secondary':secondary,'date':day,'value':count}


class CampaignWorkbookTests(unittest.TestCase):
    def legacy(self, columns, body, cache=None, configure=None):
        book = load_workbook(BytesIO(export_workbook(empty_workbook())))
        main = book[SUMMARY]
        main.delete_rows(1)
        main.append(columns)
        main.append(body)
        if configure:
            configure(book)
        out = BytesIO()
        book.save(out)
        book.close()
        if not cache:
            return out.getvalue()
        packed = BytesIO()
        with ZipFile(out) as src, ZipFile(packed,'w') as dest:
            for item in src.infolist():
                raw = src.read(item)
                if item.filename=='xl/worksheets/sheet1.xml':
                    root = ET.fromstring(raw)
                    ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    for address,(kind,value) in cache.items():
                        cell = root.find(f'.//s:c[@r="{address}"]',ns)
                        cell.set('t',kind)
                        cell.find('s:v',ns).text = value
                    raw = ET.tostring(root,encoding='utf-8')
                dest.writestr(item,raw)
        return packed.getvalue()

    def test_reordered_legacy_aliases_and_missing_optional_fields(self):
        def variants(book):
            book[SUMMARY].title = '传播名汇总'
            sheet = book[DAILY]
            sheet.delete_rows(1)
            sheet.append(['小订',datetime(2025,1,2),datetime(2025,1,3)])
            sheet.append(['车型甲',0,9])
        raw = self.legacy(['小订发布时段','小订开启日期','车型名称','小订截止日期','留资日期'],
                          ['下午','2025/1/2','车型甲',datetime(2025,1,3),'2024-12-31'],configure=variants)
        data = import_workbook(raw)
        row = data['sheets'][0]['rows'][1]
        self.assertEqual(('车型甲','下午','2024-12-31','2025-01-02',2),(row[0],row[5],row[7],row[8],row[12]))
        self.assertEqual(['车型甲',0,9,9],data['sheets'][1]['rows'][1])
        validate_publication(data)

    def test_cached_formulas_and_historical_override_in_reordered_columns(self):
        raw = self.legacy(['代际名','小订转化率','总小订','小订转大定量'],
                          ['甲','=IF(C2,D2/C2,0)','=SUM(1,9)',2],
                          {'B2':('n','0.2'),'C2':('n','10')})
        data = import_workbook(raw)
        self.assertIn('2:18',data['sheets'][0]['literalCells'])
        edited = apply_edits(data,summary(2,**{'16':20}))
        self.assertEqual(.2,edited['sheets'][0]['rows'][1][17])
        raw = self.legacy(['代际名','小订转化率','总小订','小订转大定量'],
                          ['甲','=D2/C2',10,2],{'B2':('n','0.2')})
        edited = apply_edits(import_workbook(raw),summary(2,**{'16':20}))
        self.assertEqual(.1,edited['sheets'][0]['rows'][1][17])

    def test_wrapped_canonical_and_legacy_summary_headers(self):
        for header in ('小订后退定占比', '小订后退定\n 占比', '小订后退订\r\n\u3000占比'):
            with self.subTest(header=header):
                fields = SUMMARY_FIELDS.copy()
                fields[22] = header
                row = ['甲'] + [None] * 21 + [.25]
                sheet = import_workbook(self.legacy(fields, row))['sheets'][0]
                self.assertEqual(sheet['rows'][0][22], '小订后退定占比')
                self.assertEqual(sheet['rows'][1][22], .25)
                fields[29] = '退订\n占比'
                with self.assertRaisesRegex(ValueError, 'AD1.*重复对应.*小订后退定占比'):
                    import_workbook(self.legacy(fields, row))

    def test_cancel_rate_aliases_do_not_import_percentages_from_either_section(self):
        name = '小订期退订进度'
        title = '小订结束日退订率'
        for source_title in (title, '小订期期结束日退订率', '小订期结束日退订率'):
            with self.subTest(source_title=source_title):
                def configure(book):
                    sheet = book[name]
                    sheet.delete_rows(1, sheet.max_row)
                    sheet.append(['代际名', source_title, 'D1', '参考百分比'])
                    sheet.append(['甲', '12.5%', 4, '#REF!'])
                    sheet.append(['代际名', source_title, '二级代际名', 'D1'])
                    sheet.append(['仅在百分比区', 0, None, .75])
                raw = self.legacy(['代际名'], ['甲'], configure=configure)
                data = import_workbook(raw)
                sheet = next(s for s in data['sheets'] if s['name']==name)
                quantity = dict(zip(*sheet['rows'][:2]))
                self.assertEqual(quantity['D1'], 4)
                self.assertIsNone(quantity.get('二级代际名'))
                self.assertIsNone(quantity.get(title))
                self.assertNotIn('仅在百分比区', [row[0] for row in sheet['rows'] if row])

    def test_sparse_progress_import_checks_only_columns_used_by_each_scope(self):
        name = '直接大定进度'
        target = apply_edits(empty_workbook(), summary(2, **{'1':'甲','2':'一期'}))

        def source(days, extra_fields=(), extra_values=()):
            def configure(book):
                progress = book[name]
                progress.delete_rows(1,progress.max_row)
                progress.append(['代际名',' 二级代际名 ',*days,*extra_fields])
                progress.append(['甲','一期',*[0 for _ in days],*extra_values])
                progress.append(['代际名','二级代际名','D1'])
                progress.append(['甲','一期','=1/0'])
            return self.legacy(['代际名','二级代际名'],['甲','一期'],configure=configure)

        raw = source(['第2天'], ['小订天数','小订天数'], ['#REF!','#REF!'])
        imported = next(s for s in import_workbook(raw)['sheets'] if s['name']==name)
        self.assertEqual(dict(zip(*imported['rows'][:2]))['D2'],0)
        self.assertNotIn('D1', imported['rows'][0])
        raw = source(['第2天'], ['开始大定日期','开始大定日期','大定','大定'], ['#REF!']*4)
        self.assertEqual(module_import_edits(target,raw,'reporting'), [
            dict(sheet=name,model='甲',secondary='一期',day=2,value=0)])
        with self.assertRaisesRegex(ValueError, '重复对应「开始大定日期」'):
            import_workbook(raw)
        for days, message in ((['D3','第3天'],'重复对应「D3」'), (['D100000'],'进度天数超出'), ([], '表头无法识别')):
            with self.subTest(days=days):
                raw = source(days)
                with self.assertRaisesRegex(ValueError,message):
                    module_import_edits(target,raw,'reporting')
                with self.assertRaisesRegex(ValueError,message):
                    import_workbook(raw)

    def test_legacy_ac31_can_recover_its_ratio_without_a_valid_cache(self):
        def fill(book):
            sheet = book[SUMMARY]
            sheet['A31'] = '车型乙'
            sheet['Z31'] = 8
            sheet['AB31'] = 2
            sheet['AC31'] = '=$AB31/$Z31'
            sheet['AC32'] = '=AB32/Z32'
            sheet['M32'] = '=J32-I32+1'

        for cache in (None, {'AC31': ('e', '#DIV/0!'), 'AC32': ('e', '#DIV/0!'), 'M32': ('n', '1')}):
            with self.subTest(cache=cache):
                data = import_workbook(self.legacy(SUMMARY_FIELDS, ['甲'], cache, fill))
                sheet = data['sheets'][0]
                self.assertEqual(len(sheet['rows']), 31)
                self.assertEqual(sheet['rows'][30][28], .25)
                self.assertNotIn('31:29', sheet['literalCells'])
                edited = apply_edits(data, summary(31, **{'26': 4}))
                self.assertEqual(edited['sheets'][0]['rows'][30][28], .5)

    def test_reordered_zero_denominator_and_explicit_iferror_fallback(self):
        fields = ['代际名', '留存大定到锁单率', '首销期锁单', '净大定']
        for formula, expected in [('=C2/D2', None), ('=IFERROR(C2/D2,0)', 0),
                                  ('=IFERROR(C2/D2,"")', None)]:
            with self.subTest(formula=formula):
                data = import_workbook(self.legacy(fields, ['甲', formula, 2, 0]))
                sheet = data['sheets'][0]
                self.assertEqual(dict(zip(SUMMARY_FIELDS, sheet['rows'][1])).get(fields[1]), expected)
                self.assertNotIn('2:29', sheet['literalCells'])
                edited = apply_edits(data, summary(2, **{'26': 8}))
                self.assertEqual(edited['sheets'][0]['rows'][1][28], .25)
                reset = apply_edits(edited, summary(2, **{'26': 0}))
                row = reset['sheets'][0]['rows'][1]
                self.assertEqual(dict(zip(SUMMARY_FIELDS, row)).get(fields[1]), expected)

    def test_lock_ratio_respects_valid_independent_cache_and_rejects_unknown_formulas_without_it(self):
        fields = ['代际名', '首销期锁单', '净大定', '留存大定到锁单率']
        for formula in ('=B2/C3', '=IFERROR(B2/C2,0.8)'):
            with self.subTest(formula=formula):
                with self.assertRaisesRegex(ValueError, '车型汇总 D2'):
                    import_workbook(self.legacy(fields, ['甲', 2, 8, formula]))
        data = import_workbook(self.legacy(fields, ['甲', 2, 8, '=B2/C2'], {'D2': ('n', '0.8')}))
        self.assertIn('2:29', data['sheets'][0]['literalCells'])
        edited = apply_edits(data, summary(2, **{'26': 4}))
        self.assertEqual(edited['sheets'][0]['rows'][1][28], .8)

    def test_actual_ac31_legacy_division_uses_its_referenced_column(self):
        # The archived file divides by physical column B, even when B is not net orders.
        cases = [
            ('二级代际名', None, ('e', '#DIV/0!'), None),
            ('总小订', 0, ('e', '#DIV/0!'), None),
            ('总小订', 10, None, .2),
            ('总小订', 10, ('n', '0.8'), .8),
        ]
        for heading, denominator, cache, expected in cases:
            with self.subTest(heading=heading, denominator=denominator, cache=cache):
                fields = SUMMARY_FIELDS.copy()
                if heading != '二级代际名':
                    fields[1], fields[15] = fields[15], fields[1]
                def fill(book):
                    sheet = book[SUMMARY]
                    sheet['A31'] = '车型乙'
                    sheet['B31'] = denominator
                    sheet['Z31'] = 4
                    sheet['AB31'] = 2
                    sheet['AC31'] = '=AB31/B31'

                source = self.legacy(fields, ['车型甲'], {'AC31': cache} if cache else None, fill)
                data = import_workbook(source)
                sheet = data['sheets'][0]
                self.assertEqual(sheet['rows'][1][0], '车型甲')
                self.assertEqual(sheet['rows'][30][0], '车型乙')
                field = '留存大定到锁单率'
                self.assertEqual(dict(zip(SUMMARY_FIELDS, sheet['rows'][30])).get(field), expected)
                self.assertIn('31:29', sheet['literalCells'])
                edited = apply_edits(data, summary(31, **{'26': 8, '28': 1}))
                row = edited['sheets'][0]['rows'][30]
                self.assertEqual(dict(zip(SUMMARY_FIELDS, row)).get(field), expected)

    def test_other_excel_errors_and_cross_row_division_still_locate_the_cell(self):
        fields = ['代际名', '首销期锁单', '净大定', '留存大定到锁单率']
        cases = [('=B2/C3', '#DIV/0!'), ('=B2/C2', '#REF!'), ('=C2/B2', '#VALUE!')]
        for formula, error in cases:
            with self.subTest(formula=formula, error=error):
                with self.assertRaisesRegex(ValueError, '车型汇总 D2'):
                    import_workbook(self.legacy(fields, ['甲', 2, 0, formula], {'D2': ('e', error)}))

    def test_an_unnamed_row_with_an_entered_zero_is_not_an_empty_template(self):
        def fill(book):
            sheet = book[SUMMARY]
            sheet['AB3'] = 0
            sheet['AC3'] = '=AB3/Z3'
            sheet['AC4'] = '=AB4/Z4'

        source = self.legacy(SUMMARY_FIELDS, ['甲'], configure=fill)
        data = import_workbook(source)
        self.assertEqual(len(data['sheets'][0]['rows']), 3)
        self.assertEqual(data['sheets'][0]['rows'][2][27], 0)
        with self.assertRaisesRegex(ValueError, '第 3 行缺少代际名'):
            validate_publication(data)

    def test_release_date_and_start_time_share_one_date(self):
        columns = ['代际名','小订发布日期','小订开始时间','小订结束日期','小订天数','小订发布时间']
        for published, start in [(None, '2026-01-02 09:00'), ('2026/1/2', None),
                                 ('2026/1/2', datetime(2026,1,2,14))]:
            formula = '=D2-C2+1' if published is None else '=D2-B2+1'
            source = self.legacy(columns, ['甲', published, start, '2026-01-04', formula, '下午'],
                                 cache={'E2':('n','3')})
            data = import_workbook(source)
            header, row = data['sheets'][0]['rows']
            self.assertEqual(header[5], '小订发布时段')
            self.assertEqual((row[5], row[8], row[12]), ('下午','2026-01-02',3))
            edited = apply_edits(data, summary(2, **{'9':'2026-01-03'}))
            self.assertEqual(edited['sheets'][0]['rows'][1][12], 2)
        with self.assertRaisesRegex(ValueError, 'C2.*不一致'):
            import_workbook(self.legacy(columns, ['甲','2026-01-02','2026-01-03']))

    def test_invalid_import_names_and_caches_locate_the_source(self):
        raw = self.legacy(['备注','代际名','说明','总小订'],['#REF!','甲','=BROKEN()',0])
        imported = import_workbook(raw)['sheets'][0]['rows'][1]
        self.assertEqual((imported[0], imported[15]), ('甲', 0))
        for fields,body,expected in [
            (['代际名','车型名称'],['甲','乙'],'重复对应'),
            (['代际名','锁单率'],['甲',.3],'锁单率'),
            (['代际名',None],['甲',9],'B2'),
            (['代际名','总小订'],['甲','=1+1'],'B2')]:
            with self.assertRaisesRegex(ValueError,expected):
                import_workbook(self.legacy(fields,body))
        raw=self.legacy(['代际名','总小订'],['甲','=0/0'],{'B2':('e','#DIV/0!')})
        with self.assertRaisesRegex(ValueError,'B2'):
            import_workbook(raw)
        raw=self.legacy(['代际名'],['甲'],configure=lambda b:setattr(b[DAILY],'title','其他汇总'))
        with self.assertRaisesRegex(ValueError,'缺少工作表：小订by天'):
            import_workbook(raw)

    def test_formatting_only_outside_the_data_range_does_not_block_import(self):
        def format_tail(book):
            for sheet in book:
                sheet['XFD20000'].number_format = '0.00'
        fields = ['代际名', '二级代际名', '产品档位']
        source = self.legacy(fields, ['甲', '一期', '基础'], configure=format_tail)
        self.assertEqual(import_workbook(source), import_workbook(self.legacy(fields, ['甲', '一期', '基础'])))
        edits = module_import_edits(empty_workbook(), source, 'schedule')
        self.assertIn({'sheet':SUMMARY,'row':2,'col':3,'value':'基础'}, edits)
        def oversized(book):
            book[DAILY]['B20000'] = 1
        with self.assertRaisesRegex(ValueError, '小订by天.*范围超出'):
            import_workbook(self.legacy(fields, ['甲','一期','基础'], configure=oversized))

    def test_full_import_only_reads_used_sheets(self):
        def configure(book):
            book[DAILY].delete_rows(1)
            book[DAILY].append(['代际名', '二级代际名', '2026-06-01'])
            book[DAILY].append(['甲', '首发', 7])
            for title in ('小订进度', ' 小订进度 ', '临时对照'):
                sheet = book.create_sheet(title)
                sheet['XFD1'] = '=NA()'
        raw = self.legacy(['代际名', '二级代际名'], ['甲', '首发'], configure=configure)
        data = import_workbook(raw)
        sheets = {sheet['name']: sheet for sheet in data['sheets']}
        self.assertEqual(set(sheets), {sheet['name'] for sheet in empty_workbook()['sheets']})
        self.assertEqual(sheets[DAILY]['rows'][1], ['甲', '首发', 7, 7])
        self.assertEqual(sheets[SUMMARY]['rows'][1][15], 7)

        duplicate = self.legacy(['代际名'], ['甲'], configure=lambda book: book.create_sheet('小订按天'))
        with self.assertRaisesRegex(ValueError, '同时对应 小订by天'):
            import_workbook(duplicate)

    def test_export_keeps_previously_stored_progress_sheet(self):
        historical = empty_workbook()
        historical['sheets'].append({
            'name':'小订进度', 'rows':[['代际名','二级代际名','D1'], ['甲','首发',7]],
            'headerRows':[1], 'computed':[], 'literalCells':[],
        })
        book = load_workbook(BytesIO(export_workbook(historical)), read_only=True, data_only=True)
        try:
            self.assertEqual(list(book['小订进度'].values)[1], ('甲','首发',7))
        finally:
            book.close()

    def test_inclusive_windows_calculated_and_guarded(self):
        base = empty_workbook()
        edits = summary(2, **{'1':'测试代际', '9':'2024-02-28','10':'2024-03-01',
                              '11':'2024-03-01','12':'2024-03-01','16':100,'17':30,'22':20})
        data = apply_edits(base,edits)
        validate_publication(data)
        row = data['sheets'][0]['rows'][1]
        self.assertEqual((3,1,.3,50,.5),(row[12],row[13],row[17],row[23],row[24]))
        self.assertEqual(1,len(base['sheets'][0]['rows']))
        with self.assertRaisesRegex(ValueError,'系统计算'):
            apply_edits(data,summary(2, **{'13':2}))
        backwards = apply_edits(data,summary(2, **{'10':'2024-02-20'}))
        with self.assertRaisesRegex(ValueError,'结束日期早于'):
            validate_publication(backwards)
        duplicate = apply_edits(data,summary(3, **{'1':'测试代际'}))
        with self.assertRaisesRegex(ValueError,'重复'):
            validate_publication(duplicate)

    def test_distinct_secondary_campaigns_and_sorted_daily_columns(self):
        data = apply_edits(empty_workbook(),[daily('A','一期','2024-04-02',8),
                                             daily('A','二期','2024-06-01',4),
                                             daily('A','一期','2024-04-01',0),
                                             daily('A','一期','2024-04-02',5)])
        sheet = data['sheets'][1]
        self.assertEqual([1,3],sheet['headerRows'])
        self.assertEqual(['代际名','2024-04-01','2024-04-02','合计','二级代际名'],sheet['rows'][0])
        self.assertEqual(['A',0,5,5,'一期'],sheet['rows'][1])
        self.assertEqual(['A',4,4,'二期'],sheet['rows'][3])
        narrowed = export_workbook(data,{'sheets':[DAILY],'models':['二期']})
        book = load_workbook(BytesIO(narrowed),read_only=True,data_only=True)
        self.assertEqual([DAILY],book.sheetnames)
        self.assertEqual(datetime(2024,6,1),book.active['B1'].value)
        self.assertEqual(4,book.active['B2'].value)
        self.assertEqual(2,book.active.max_row)
        book.close()

    def test_progress_section_insert_and_extend(self):
        def entry(section,value):
            return {'sheet':'小订期退订进度','section':section,'day':72,'model':'=原样文本',
                    'secondary':'二期','value':value}
        edits = summary(2,**{'1':'=原样文本','2':'二期'}) + [entry(2,'2%'),entry(1,15)]
        data = apply_edits(empty_workbook(),edits)
        sheet = data['sheets'][2]
        self.assertEqual([1,3],sheet['headerRows'])
        self.assertEqual(['D72','二级代际名'],sheet['rows'][0][-2:])
        self.assertEqual([15,'二期'],sheet['rows'][1][-2:])
        self.assertEqual([.02,'二期'],sheet['rows'][3][-2:])
        raw = export_workbook(data)
        book = load_workbook(BytesIO(raw))
        try:
            rate = book[sheet['name']].cell(4, sheet['rows'][0].index('D72')+1)
            self.assertEqual((rate.value, rate.number_format), (.02, '0.00%'))
        finally:
            book.close()
        imported = import_workbook(raw)['sheets'][2]
        quantity = dict(zip(*imported['rows'][:2]))
        self.assertEqual((quantity['D72'], quantity['二级代际名']), (15, '二期'))
        rates = imported['rows'][imported['headerRows'][1]:]
        self.assertFalse(any(.02 in row for row in rates))
        with self.assertRaisesRegex(ValueError,'整数'):
            apply_edits(data,[entry(1,1.1)])

    def test_preserve_imported_literal_overrides(self):
        edits = summary(2, **{'1':'测试','16':100,'17':10,'22':25,'19':50,'28':20})
        base = apply_edits(empty_workbook(),edits)
        book = load_workbook(BytesIO(export_workbook(base)))
        book[SUMMARY]['X2'] = 41
        book[SUMMARY]['Y2'] = .41
        memory = BytesIO()
        book.save(memory)
        book.close()
        imported = import_workbook(memory.getvalue())
        edited = apply_edits(imported,summary(2,**{'17':23,'22':32}))
        self.assertEqual([41,.41],edited['sheets'][0]['rows'][1][23:25])
        self.assertEqual(.23,edited['sheets'][0]['rows'][1][17])

    def test_ignore_empty_semantic_clear_and_reject_invalid_inputs(self):
        base = empty_workbook()
        self.assertEqual(base,apply_edits(base,[daily('A','','2026-01-01',None)]))
        for bad in (-1,1.5,'NaN',float('inf'),True):
            with self.assertRaises(ValueError):
                apply_edits(base,[daily('A','','2026-01-01',bad)])
        with self.assertRaisesRegex(ValueError,'日期'):
            apply_edits(base,[daily('A','','2026-02-30',3)])
        with self.assertRaisesRegex(ValueError,'有效的数据表'):
            export_workbook(base,{'sheets':['缺失']})

    def test_raw_correction_after_adding_daily_record_in_one_save(self):
        edits = [daily('甲','','2026-01-01',10),daily('乙','','2026-01-02',20),
                 {'sheet':DAILY,'row':4,'col':2,'value':0}]
        data = apply_edits(empty_workbook(),edits)
        self.assertEqual(['乙',0,0,None],data['sheets'][1]['rows'][3])

    def test_rename_updates_both_identity_columns_and_only_its_stage(self):
        edits = (summary(2,**{'1':'甲','2':'一期'}) + summary(3,**{'1':'甲','2':'二期'})
                 + [daily('甲','一期','2026-01-01',0),daily('甲','二期','2026-01-01',7)])
        data = apply_edits(empty_workbook(),edits)
        renamed = apply_edits(data,summary(2,**{'1':'甲改名','2':'一期改名'}))
        self.assertEqual(['甲改名',0,0,'一期改名'],renamed['sheets'][1]['rows'][1])
        self.assertEqual(['甲',7,7,'二期'],renamed['sheets'][1]['rows'][3])
        validate_publication(renamed)
        with self.assertRaisesRegex(ValueError,'重复'):
            apply_edits(data,summary(2,**{'2':'二期'}))
        orphan = apply_edits(data,[daily('无关联车型','','2026-01-01',5)])
        with self.assertRaisesRegex(ValueError,'无法匹配'):
            validate_publication(orphan)

    def test_export_absolute_dates_only_without_total_columns(self):
        data = apply_edits(empty_workbook(),[daily('甲','','2026-03-01',10),
                                             daily('甲','','2026-03-02',3),
                                             daily('乙','','2026-04-01',17)])
        options = {'sheets':[DAILY,'大定进度'],'dateFrom':'2026-03-02','dateTo':'2026-03-02'}
        book = load_workbook(BytesIO(export_workbook(data,options)),read_only=True)
        self.assertEqual([('代际名',datetime(2026,3,2),'二级代际名'),('甲',3,None)],
                         list(book[DAILY].values))
        self.assertIn('D1',next(book['大定进度'].values))
        book.close()
        with self.assertRaisesRegex(ValueError,'结束日期早于'):
            export_workbook(data,{**options,'dateFrom':'2026-04-01'})

    def test_daily_counts_drive_progress_rates_and_keep_unknown_distinct(self):
        def observation(sheet, day, value, section=1):
            return {'sheet':sheet,'model':'甲','section':section,'day':day,'value':value}
        edits = summary(2,**{'1':'甲','19':20,'20':8})
        edits += [observation('直接大定进度',1,2),observation('小转大进度',1,8)]
        result = apply_edits(empty_workbook(),edits)
        sheets = {s['name']:s for s in result['sheets']}
        self.assertNotIn('小订进度',sheets)
        direct = sheets['直接大定进度']
        self.assertEqual(.25,direct['rows'][3][12])
        self.assertEqual(.2,direct['rows'][5][12])
        with self.assertRaisesRegex(ValueError,'数量计算'):
            apply_edits(result,[observation('直接大定进度',1,.3,3)])
        corrected = apply_edits(result,[observation('小转大进度',1,2)])
        direct = next(s for s in corrected['sheets'] if s['name']=='直接大定进度')
        self.assertEqual(.5,direct['rows'][5][12])
        cleared = apply_edits(corrected,[observation('直接大定进度',1,None)])
        direct = next(s for s in cleared['sheets'] if s['name']=='直接大定进度')
        self.assertIsNone(direct['rows'][5][12])

    def test_daily_block_footer_aggregates_if_previously_numeric(self):
        base = empty_workbook()
        sheet = base['sheets'][1]
        sheet['rows'] = [['代际名','2026-01-01','合计','二级代际名'],
                         ['甲',3,3],['乙',4,4],['合计',7,7]]
        result = apply_edits(base,[daily('甲','','2026-01-01',0)])
        self.assertEqual(['合计',4,4],result['sheets'][1]['rows'][3])

    def test_complete_daily_detail_updates_summary_without_overwriting_separate_total(self):
        schedule = summary(2,**{'1':'甲','9':'2026-01-01','10':'2026-01-02','17':4,'22':1})
        first = daily('甲','','2026-01-01',5)
        second = daily('甲','','2026-01-02',3)
        combined = apply_edits(empty_workbook(),schedule+[first,second])
        self.assertEqual(8,combined['sheets'][0]['rows'][1][15])
        corrected = apply_edits(combined,[daily('甲','','2026-01-02',0)])
        row = corrected['sheets'][0]['rows'][1]
        self.assertEqual((5,.8,0),(row[15],row[17],row[23]))
        absent = apply_edits(corrected,[daily('甲','','2026-01-02',None)])
        self.assertIsNone(absent['sheets'][0]['rows'][1][15])
        external = apply_edits(empty_workbook(),schedule+summary(2,**{'16':100})+[first,second])
        self.assertEqual(100,external['sheets'][0]['rows'][1][15])


if __name__ == '__main__':
    unittest.main()
