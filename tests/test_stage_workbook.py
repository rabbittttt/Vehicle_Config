import unittest
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from stage_workbook import (DAILY, SUMMARY, apply_edits, empty_workbook, export_workbook,
                            import_workbook, validate_publication)


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

    def test_invalid_import_names_and_caches_locate_the_source(self):
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
        with self.assertRaisesRegex(ValueError,'小订by天.*其他汇总'):
            import_workbook(raw)

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
            return {'sheet':'小订进度','section':section,'day':62,'model':'=原样文本',
                    'secondary':'二期','value':value}
        edits = summary(2,**{'1':'=原样文本','2':'二期'}) + [entry(2,'2%'),entry(1,15)]
        data = apply_edits(empty_workbook(),edits)
        sheet = data['sheets'][2]
        self.assertEqual([1,3],sheet['headerRows'])
        self.assertEqual(['D62','二级代际名'],sheet['rows'][0][-2:])
        self.assertEqual([15,'二期'],sheet['rows'][1][-2:])
        self.assertEqual([.02,'二期'],sheet['rows'][3][-2:])
        raw = export_workbook(data)
        imported = import_workbook(raw)
        self.assertEqual(sheet,imported['sheets'][2])
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

    def test_export_absolute_dates_only_and_recalculate_visible_total(self):
        data = apply_edits(empty_workbook(),[daily('甲','','2026-03-01',10),
                                             daily('甲','','2026-03-02',3),
                                             daily('乙','','2026-04-01',17)])
        options = {'sheets':[DAILY,'大定进度'],'dateFrom':'2026-03-02','dateTo':'2026-03-02'}
        book = load_workbook(BytesIO(export_workbook(data,options)),read_only=True)
        self.assertEqual([('代际名',datetime(2026,3,2),'合计','二级代际名'),('甲',3,3,None)],
                         list(book[DAILY].values))
        self.assertIn('D1',next(book['大定进度'].values))
        book.close()
        with self.assertRaisesRegex(ValueError,'结束日期早于'):
            export_workbook(data,{**options,'dateFrom':'2026-04-01'})

    def test_daily_counts_drive_progress_rates_and_keep_unknown_distinct(self):
        def observation(sheet, day, value, section=1):
            return {'sheet':sheet,'model':'甲','section':section,'day':day,'value':value}
        edits = summary(2,**{'1':'甲','19':20,'20':8})
        edits += [observation('小订进度',1,10), observation('小订进度',2,4),
                  observation('直接大定进度',1,2),observation('大定进度',1,10)]
        result = apply_edits(empty_workbook(),edits)
        sheets = {s['name']:s for s in result['sheets']}
        s = sheets['小订进度']
        self.assertEqual([1,.4],s['rows'][3][1:3])
        self.assertIn('4:3',s['derivedCells'])
        direct = sheets['直接大定进度']
        self.assertEqual(.25,direct['rows'][3][12])
        self.assertEqual(.2,direct['rows'][5][12])
        with self.assertRaisesRegex(ValueError,'数量计算'):
            apply_edits(result,[observation('直接大定进度',1,.3,3)])
        corrected = apply_edits(result,[observation('大定进度',1,4),observation('小订进度',2,None)])
        direct = next(s for s in corrected['sheets'] if s['name']=='直接大定进度')
        self.assertEqual(.5,direct['rows'][5][12])
        self.assertIsNone(corrected['sheets'][2]['rows'][3][2])

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
