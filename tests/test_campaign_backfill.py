import unittest
from io import BytesIO

from openpyxl import load_workbook

from stage_workbook import SUMMARY, apply_edits, backfill_totals, empty_workbook, export_workbook, import_workbook


def header_edit(col, value, row=2):
    return dict(sheet=SUMMARY,row=row,col=col,value=value)


def observation(name, day, value):
    return dict(sheet=name,model='甲',secondary='一期',section=1,day=day,value=value)


def schedule():
    return [header_edit(col,value) for col,value in [
        (1,'甲'),(2,'一期'),(9,'2026-01-01'),(10,'2026-01-02'),
        (11,'2026-02-01'),(12,'2026-02-02'),
    ]]


def sheet(data,name):
    return next(s for s in data['sheets'] if s['name']==name)


def review(data,col):
    row = sheet(data,SUMMARY)['rows'][1]
    return row[col-1] if len(row)>=col else None


class BackfillTests(unittest.TestCase):
    def test_quantity_series_write_preview_totals_but_never_assume_missing_days(self):
        names = ['小转大进度','大定进度','直接大定进度']
        base = apply_edits(empty_workbook(),schedule()+[header_edit(16,10)])
        partial = apply_edits(base,[observation(name,1,qty) for name,qty in zip(names,[3,7,4])])
        self.assertEqual([review(partial,col) for col in (17,19,20)], [None]*3)
        full = apply_edits(partial,[observation(name,2,0) for name in names])
        self.assertEqual([review(full,col) for col in (17,19,20,18,21)], [3,7,4,.3,4/7])
        self.assertEqual([review(full,col) for col in (22,26,28)], [None]*3)
        direct = sheet(full,'直接大定进度')
        col = direct['rows'][0].index('D1')+1
        correction = dict(sheet='直接大定进度',row=2,col=col,value=0)
        self.assertEqual(review(apply_edits(full,[correction]),20),0)
        self.assertIsNone(review(apply_edits(full,[observation('大定进度',2,None)]),19))
        self.assertIsNone(review(apply_edits(full,[observation('大定进度',3,4)]),19))
        extended = apply_edits(full,[header_edit(12,'2026-02-03')])
        self.assertIsNone(review(extended,17))
        self.assertEqual(review(apply_edits(extended,[observation('小转大进度',3,0)]),17),3)

    def test_explicit_detail_cutoff_and_independent_review_value(self):
        name = '小转大进度'
        full = apply_edits(empty_workbook(),schedule()+[observation(name,1,2),observation(name,2,0)])
        col = sheet(full,name)['rows'][0].index('小转大结束日期')+1
        extended = apply_edits(full,[dict(sheet=name,row=2,col=col,value='2026-02-04')])
        self.assertIsNone(review(extended,17))
        changes = [observation(name,3,1),observation(name,4,7)]
        self.assertIsNone(review(apply_edits(extended,changes),17))
        extended = apply_edits(extended,[header_edit(12,'2026-02-04')])
        self.assertEqual(review(apply_edits(extended,changes),17),10)
        override = apply_edits(extended,[header_edit(17,10)]+changes)
        self.assertEqual(review(apply_edits(override,[observation(name,4,9)]),17),10)
        self.assertEqual(review(apply_edits(override,[header_edit(17,None),observation(name,4,9)]),17),12)

    def test_rates_follow_the_backfilled_denominator_and_preserve_independent_values(self):
        name = '大定进度'
        initial = apply_edits(empty_workbook(),schedule()+[observation(name,1,5),observation(name,2,0)])
        def read(data,section,field):
            progress = sheet(data,name)
            header = progress['headerRows'][section-1]-1
            return progress['rows'][header+1][progress['rows'][header].index(field)]
        for section in (1,2):
            self.assertEqual(read(initial,section,'大定'),5)
        self.assertEqual(read(initial,2,'D1'),1)
        corrected = apply_edits(initial,[observation(name,2,3)])
        self.assertEqual([read(corrected,2,key) for key in ['大定','D1','D2']],[8,5/8,1])
        progress = sheet(corrected,name)
        header = progress['headerRows'][1]-1
        progress['rows'][header+1][progress['rows'][header].index('D1')]=.4
        corrected = apply_edits(corrected,[observation(name,1,3)])
        self.assertEqual(read(corrected,2,'D1'),.4)
        self.assertEqual(read(corrected,2,'D2'),1)
        pos = sheet(corrected,name)['rows'][0].index('大定')+1
        custom = apply_edits(corrected,[dict(sheet=name,row=2,col=pos,value=20),observation(name,2,9)])
        self.assertEqual(read(custom,1,'大定'),20)
        self.assertEqual(read(custom,2,'D2'),12/20)

    def test_legacy_import_or_existing_draft_fills_blanks_only_for_unique_rows(self):
        name = '大定进度'
        complete = apply_edits(empty_workbook(),schedule()+[observation(name,1,5),observation(name,2,1)])
        sheet(complete,SUMMARY)['rows'][1][18] = None
        self.assertEqual(review(import_workbook(export_workbook(complete)),19),6)
        self.assertEqual(review(backfill_totals(complete),19),6)
        book = load_workbook(BytesIO(export_workbook(complete)))
        ws = book[name]
        ws.insert_rows(3)
        for i,cell in enumerate(ws[2],1):
            ws.cell(3,i).value = cell.value
        book[SUMMARY]['S2']=None
        output=BytesIO()
        book.save(output)
        book.close()
        self.assertIsNone(review(import_workbook(output.getvalue()),19))
        sheet(complete,SUMMARY)['rows'][1][18]=14
        self.assertEqual(review(apply_edits(complete,[observation(name,2,9)]),19),14)
        matched=apply_edits(complete,[observation(name,2,9)])
        self.assertEqual(review(apply_edits(matched,[observation(name,2,10)]),19),14)

    def test_cancellations_require_corroboration_and_are_never_added_twice(self):
        small,launch = '小订期退订进度','首销期退订进度'
        changes = [observation(name,day,value) for name in (small,launch) for day,value in [(1,2),(2,0)]]
        separate = apply_edits(empty_workbook(),schedule()+changes)
        self.assertIsNone(review(separate,22))
        agreed = apply_edits(separate,[header_edit(11,'2026-01-01'),header_edit(12,'2026-01-02')])
        self.assertEqual(review(agreed,22),2)
        self.assertIsNone(review(apply_edits(agreed,[observation(small,1,3)]),22))
        totals = [dict(sheet=name,row=2,col=sheet(separate,name)['rows'][0].index('退订')+1,value=2)
                  for name in (small,launch)]
        explicit = apply_edits(separate,totals)
        self.assertEqual(review(explicit,22),2)
        self.assertIsNone(review(apply_edits(explicit,[observation(launch,2,1)]),22))
        self.assertIsNone(review(apply_edits(explicit,[{**totals[1],'value':3}]),22))

        # Independently reported totals still exist when the old workbook
        # contains only a slice of either refund curve.
        incomplete = apply_edits(explicit,[observation(launch,2,None),observation(launch,1,1)])
        self.assertEqual(review(incomplete,22),2)
        self.assertIsNone(review(apply_edits(incomplete,[observation(launch,1,3)]),22))
        self.assertIsNone(review(apply_edits(incomplete,[observation(launch,2,0)]),22))
        blank = apply_edits(incomplete,[observation(launch,1,None)])
        self.assertEqual(review(blank,22),2)


if __name__=='__main__':
    unittest.main()
