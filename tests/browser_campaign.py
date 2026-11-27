"""Browser acceptance for separated configuration, rule and daily reporting views."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

from browser_rules_check import fill_owner, isolated_server, ready
from campaign_store import CampaignStore
from stage_workbook import BACKFILLED, SUMMARY_FIELDS, apply_edits, empty_workbook, export_workbook


def confirm(page):
    page.locator('#confirm').click()
    ready(page)
    expect(page.locator('#dialog')).to_be_hidden()


def tab(page, name):
    page.locator(f'header nav [data-tab="{name}"]').click()
    ready(page)


def view(page, name):
    page.locator(f'[data-view="{name}"]').click()
    ready(page)


def action(page, kind):
    return page.locator(f'[data-campaign-action="{kind}"]')


def report_row(page, model):
    return page.locator(f'.report-matrix tbody tr[data-model="{model}"]')


def report_cell(row, key):
    return row.locator(f'[data-campaign-action=report-cell][data-key="{key}"]')


def legacy_file(model):
    fields = {1: model, 2: '纯电版', 3: '中型', 4: '纯电', 5: '标准发布',
              9: '2026-11-01', 10: '2026-11-02', 16: 160, 17: 60, 19: 77,
              20: 20, 22: 5, 26: 40, 28: 30}
    edits = [{'sheet': '车型汇总', 'row': 2, 'col': col, 'value': value}
             for col, value in fields.items()]
    edits.append({'sheet': '小转大进度', 'model': model, 'secondary': '纯电版',
                  'section': 1, 'day': 1, 'value': 6})
    book = load_workbook(BytesIO(export_workbook(apply_edits(empty_workbook(), edits))))
    # These independently maintained legacy values must survive a trip through the editor.
    summary = book['车型汇总']
    summary['X2'], summary['Y2'], summary['AA2'] = 91, .59, .46
    curve = book['小转大进度']
    d1 = next(cell.column for cell in curve[1] if cell.value == 'D1')
    curve.cell(curve.max_row, d1).value = .18
    history = book.create_sheet('小订进度')
    for value in (63, .37):
        history.append(['代际名', 'D1', '二级代际名'])
        history.append([model, value, '纯电版'])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def legacy_preview(page):
    page.locator('#data-tools').click()
    with page.expect_file_chooser() as event:
        page.get_by_role('button', name='导入小订首销旧表').click()
    event.value.set_files({'name': '迁入验收.xlsx',
                           'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           'buffer': legacy_file('替换车型')})
    expect(page.get_by_role('heading', name='旧表导入预览')).to_be_visible()
    expect(page.locator('#dialog-body')).to_contain_text('移出车型阶段')
    expect(page.locator('#confirm')).to_have_text('替换草稿')


def download(page):
    with page.expect_download() as received:
        confirm(page)
    book = load_workbook(BytesIO(Path(received.value.path()).read_bytes()), read_only=True, data_only=True)
    try:
        return {sheet.title: list(sheet.values) for sheet in book}
    finally:
        book.close()


def check_configuration(page):
    expect(page.locator('[data-view=vehicleIdentity]')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#content thead th')).to_have_text(['代际名', '二级代际名', '产品档位', '能源类型', '操作'])
    expect(page.locator('#content')).to_contain_text('历史车型')
    expect(page.locator('header nav [data-tab=models], header nav [data-tab=matrix], [data-view=versions]')).to_have_count(0)
    action(page, 'identity').click()
    expect(page.get_by_label('代际名', exact=True)).to_have_value('历史车型')
    expect(page.locator('#dialog [name=f4], #dialog [name=f18]')).to_have_count(0)
    page.get_by_label('产品档位').fill('中大型')
    confirm(page)
    view(page, 'launchSchedule')
    expect(page.locator('#content')).not_to_contain_text('大定量')
    action(page, 'stage').click()
    expect(page.locator('#dialog [name=f2], #dialog [name=f18]')).to_have_count(0)
    page.get_by_label('小订结束日期').fill('2026-11-03')
    confirm(page)
    action(page, 'stage').click()
    expect(page.locator('label').filter(has_text='小订天数').locator('output')).to_have_text('3')
    page.locator('#cancel').click()
    page.locator('#save').click()
    page.get_by_label('本次维护人', exact=True).fill('本机验收')
    confirm(page)
    ready(page)
    page.locator('#export').click()
    ready(page)
    expect(page.locator('[name=sheets], [name=dateFrom]')).to_have_count(0)
    sheets = download(page)
    assert len(sheets) == 1
    headers, row = next(iter(sheets.values()))
    assert list(headers) == SUMMARY_FIELDS[:14]
    assert row[2] == '中大型' and row[12] == 3


def check_result_review(page):
    view(page, 'businessTotals')
    expect(page.locator('[data-view=businessTotals]')).to_have_text('结果核对')
    expect(page.locator('#content')).not_to_contain_text('小订待转单')
    expect(page.locator('#content')).not_to_contain_text('小订转化率')
    metrics = {SUMMARY_FIELDS[col-1]: '可自动回填' if col in BACKFILLED else '手动填报'
               for col in sorted(BACKFILLED | {26, 28})}
    headings = page.locator('.campaign-totals thead th')
    for i, (metric, mode) in enumerate(metrics.items(), 1):
        expect(headings.nth(i)).to_contain_text(metric)
        expect(headings.nth(i).locator('.review-mode')).to_have_text(mode)
    action(page, 'totals').click()
    dialog = page.locator('#dialog')
    expect(dialog.locator('input[type=number]')).to_have_count(7)
    for metric, mode in metrics.items():
        field = dialog.get_by_label(metric, exact=True)
        note_id = field.get_attribute('aria-describedby').split()[0]
        expect(dialog.locator('#' + note_id)).to_have_text(mode)
    expect(dialog).not_to_contain_text('小订待转单')
    rates = dialog.locator('details').filter(has=page.locator('summary', has_text='查看比例'))
    toggle = rates.locator('summary')
    expect(toggle).to_be_visible()
    assert not rates.evaluate('el => el.open')
    toggle.click()
    for rate in ['小订转化率', '直接大定占比', '小订后退定占比', '净大定率', '留存大定到锁单率', '大定到锁单率']:
        expect(rates.get_by_text(rate, exact=True)).to_be_visible()
    expect(rates.locator('input')).to_have_count(0)
    net_rate = rates.locator('div').filter(has=page.get_by_text('净大定率', exact=True))
    expect(net_rate.locator('output')).to_have_text('46%')
    expect(net_rate).to_contain_text('历史值，按数量计算 25%')
    expect(net_rate).to_contain_text('首销期净大定 40 ÷ 总小订 160')
    for rate, formula in [('留存大定到锁单率', '首销期锁单 30 ÷ 首销期净大定 40'),
                          ('大定到锁单率', '首销期锁单 30 ÷ 大定量 77')]:
        entry = rates.locator('div').filter(has=page.get_by_text(rate, exact=True))
        expect(entry).to_contain_text(formula)
    toggle.click()
    gross = page.get_by_label('大定量', exact=True)
    direct = page.get_by_label('直接大定量', exact=True)
    expect(gross).to_have_value('77')
    direct.fill('')
    expect(dialog.get_by_text('差额', exact=False).filter(visible=True)).to_have_count(0)
    direct.fill('0')
    expect(dialog).to_contain_text('+17')
    direct.fill('20')
    gross.fill('80')
    expect(dialog.get_by_text('差额', exact=False).filter(visible=True)).to_have_count(0)
    gross.fill('83')
    expect(dialog).to_contain_text('+3')
    confirm(page)
    expect(page.locator('#content')).to_contain_text('+3')


def check_quantity_progress(page):
    metric = page.locator('#campaign-progress')
    expect(page.get_by_label('分组', exact=True)).to_have_count(0)
    metric.select_option('小转大进度')
    page.get_by_role('button', name='批量粘贴').click()
    page.locator('[name=rows]').fill('D1\t8\nD2\t0')
    confirm(page)
    metric.select_option('直接大定进度')
    action(page, 'report-add').click()
    page.get_by_label('日序').fill('1')
    page.get_by_label('数量', exact=True).fill('0')
    confirm(page)
    expect(metric.locator('option[value="大定进度"]')).to_have_count(0)


def check_reporting(page):
    tab(page, 'reporting')
    expect(page.locator('[data-view=dailyOrders]')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('[data-view=dailyOrders]')).to_have_text('日常填报')
    expect(page.locator('[data-view=stageProgress]')).to_have_count(0)
    check_result_review(page)
    view(page, 'dailyOrders')
    expect(page.locator('#campaign-progress option')).to_have_text([
        '每日小订', '小订期退订进度', '小转大进度', '直接大定进度', '首销期退订进度',
    ])
    page.get_by_role('button', name='批量粘贴').click()
    page.locator('[name=rows]').fill('2026-11-01\t0\n2026-11-02\t15')
    confirm(page)
    expect(page.locator('.report-matrix tbody tr')).to_have_count(1)
    row = report_row(page, '历史车型')
    expect(report_cell(row, 'D1')).to_have_text('0')
    report_cell(row, 'D2').click()
    page.get_by_label('小订量').fill('16')
    confirm(page)
    page.locator('#undo').click()
    expect(report_cell(row, 'D2')).to_have_text('15')
    page.locator('#redo').click()
    expect(report_cell(row, 'D2')).to_have_text('16')
    check_quantity_progress(page)
    page.locator('#save').click()
    ready(page)
    page.reload()
    ready(page)
    expect(page.locator('#owner')).to_have_value('本机验收')
    tab(page, 'reporting')
    view(page, 'businessTotals')
    action(page, 'totals').click()
    expect(page.get_by_label('大定量', exact=True)).to_have_value('83')
    page.locator('#cancel').click()
    page.locator('#publish').click()
    confirm(page)
    page.locator('#export').click()
    ready(page)
    expect(page.locator('[name=sheets]')).to_have_count(8)
    complete = download(page)
    assert len(complete) == 8
    headers, row = complete['车型汇总']
    result = dict(zip(headers, row))
    assert (result['小订待转单'], result['小订待转单占比'], result['净大定率']) == (91, .59, .46)
    curve = complete['小转大进度']
    assert curve[-1][curve[0].index('D1')] == .18
    history = complete['小订进度']
    assert [r[history[0].index('D1')] for r in history if r[0] == '历史车型'] == [63, .37]
    page.locator('#export').click()
    ready(page)
    for box in page.locator('[name=sheets]').all():
        box.set_checked(box.input_value() == '小订by天')
    page.get_by_label('开始日期', exact=True).fill('2026-11-02')
    page.get_by_label('截止日期', exact=True).fill('2026-11-02')
    page.locator('#dialog summary').click()
    page.get_by_role('checkbox', name='纯电版').check()
    sheets = download(page)
    assert list(sheets) == ['小订by天']
    entries = sheets['小订by天']
    assert any(16 in row for row in entries)
    assert any('2026-11-02' in str(cell) for row in entries for cell in row)
    assert all('2026-11-01' not in str(cell) for row in entries for cell in row)


def check_rule_context(page):
    tab(page, 'schedule')
    view(page, 'launchSchedule')
    action(page, 'add-stage').click()
    page.get_by_label('代际名', exact=True).fill('验收新车型')
    page.get_by_label('留资日期', exact=True).fill('2027-02-01')
    confirm(page)
    page.locator('#save').click()
    ready(page)
    tab(page, 'generation')
    view(page, 'gen_names')
    page.locator('[data-rule-action=edit]').click()
    page.locator('[name=target]').fill('保留未保存的脚本编辑')
    confirm(page)
    legacy_preview(page)
    page.locator('#cancel').click()
    expect(page.locator('#content')).to_contain_text('保留未保存的脚本编辑')
    legacy_preview(page)
    confirm(page)
    expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#content')).to_contain_text('保留未保存的脚本编辑')
    tab(page, 'schedule')
    expect(page.locator('#content')).to_contain_text('替换车型')
    page.locator('#data-tools').click()
    page.get_by_role('button', name='撤销旧表导入').click()
    confirm(page)
    expect(page.locator('#content')).to_contain_text('历史车型')
    expect(page.locator('#content')).to_contain_text('验收新车型')
    view(page, 'vehicleIdentity')
    action(page, 'identity').first.click()
    page.get_by_label('产品档位').fill('未保存的调整')
    confirm(page)
    tab(page, 'generation')
    page.locator('#data-tools').click()
    page.get_by_role('button', name='导入小订首销旧表').click()
    expect(page.locator('#error-notice')).to_contain_text('保存草稿')
    tab(page, 'schedule')
    expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#content')).to_contain_text('未保存的调整')
    page.set_viewport_size({'width': 390, 'height': 844})
    tab(page, 'reporting')
    expect(page.locator('[data-view=dailyOrders]')).to_be_visible()
    view(page, 'businessTotals')
    expect(page.locator('.campaign-totals .review-mode.manual')).to_have_count(2)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    action(page, 'totals').first.click()
    expect(page.get_by_label('大定量', exact=True)).to_have_value('83')
    page.locator('#cancel').click()
    tab(page, 'schedule')
    view(page, 'vehicleIdentity')
    scroll = page.locator('#content .scroll')
    assert scroll.evaluate('el => el.scrollWidth <= el.clientWidth')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    first = page.locator('#content tbody tr').first
    expect(first.locator('.identity-name')).to_have_text('历史车型')
    expect(first.locator('.identity-details dt:visible')).to_have_text(['二级代际名', '产品档位', '能源类型'])
    expect(first.locator('.identity-details dd:visible')).to_have_text(['纯电版', '未保存的调整', '纯电'])
    edit = first.get_by_role('button', name='编辑')
    bounds = edit.bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    edit.focus()
    edit.press('Enter')
    expect(page.get_by_label('代际名', exact=True)).to_have_value('历史车型')
    expect(page.get_by_label('产品档位')).to_have_value('未保存的调整')
    page.locator('#cancel').click()


def check(browser):
    with TemporaryDirectory() as directory, isolated_server(directory) as (url, store, *_):
        CampaignStore(store.root).import_legacy(0, legacy_file('历史车型'), '测试迁入')
        page = browser.new_page(viewport={'width': 1360, 'height': 880})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        ready(page)
        check_configuration(page)
        check_reporting(page)
        check_rule_context(page)
        assert not errors, errors
        page.close()


def seed_stage_matrix(campaign):
    # The same displayed D1 belongs to a different calendar date in each secondary generation.
    edits = []
    for row, (model, secondary, start, gross) in enumerate([
        ('矩阵车型', '标准', '2026-11-01', 41), ('矩阵车型', '焕新', '2026-11-05', 82),
        ('待定车型', '', '', None),
    ], start=2):
        for col, value in [(1, model), (2, secondary), (9, start), (19, gross)]:
            if value not in ('', None):
                edits.append(dict(sheet='车型汇总', row=row, col=col, value=value))
    for model, secondary, date, value in [
        ('矩阵车型', '标准', '2026-11-01', 0), ('矩阵车型', '焕新', '2026-11-05', 25),
        ('待定车型', '', '2026-11-02', 17),
    ]:
        edits.append(dict(sheet='小订by天', model=model, secondary=secondary, date=date, value=value))
    edits.append(dict(sheet='车型汇总', row=2, col=10, value='2026-11-28'))
    edits.extend([dict(sheet='车型汇总', row=3, col=16, value=160),
                  dict(sheet='车型汇总', row=3, col=26, value=40)])
    edits.append(dict(sheet='小订期退订进度', model='矩阵车型', secondary='标准', section=1, day=1, value=63))
    edits.append(dict(sheet='小订期退订进度', model='仅有历史进度', secondary='', section=1, day=3, value=57))
    snapshot = campaign.save(0, edits, '测试')
    daily = next(sheet for sheet in snapshot['sheets'] if sheet['name'] == '小订by天')
    # Old spreadsheets can have two physical rows for the same model, stage and date.
    header = daily['rows'][daily['headerRows'][-1] - 1]
    row_number = len(daily['rows']) + 1
    duplicate = [dict(sheet='小订by天', row=row_number, col=header.index(name) + 1, value=value)
                 for name, value in [('代际名', '待定车型'), ('2026-11-02', 31)]]
    campaign.save(snapshot['revision'], duplicate, '测试')


def check_stage_matrix(browser):
    with TemporaryDirectory() as directory, isolated_server(directory) as (url, store, *_):
        campaign = CampaignStore(store.root)
        seed_stage_matrix(campaign)
        page = browser.new_page()
        page.goto(url)
        ready(page)
        fill_owner(page)
        tab(page, 'reporting')
        rows = page.locator('.report-matrix tbody tr[data-model]')
        expect(rows).to_have_count(2)
        row = report_row(page, '矩阵车型')
        stage = row.locator('select[data-report-stage]')
        stage.select_option('标准')
        metric = page.locator('#campaign-progress')
        expect(metric).to_have_value('小订by天')
        expect(page.locator('#report-axis')).to_have_value('day')
        expect(report_cell(row, 'D1')).to_have_text('0')
        expect(report_cell(row, 'D2')).to_have_text('—')
        matrix = page.locator('.report-matrix')
        matrix.evaluate('el => el.scrollLeft = el.scrollWidth')
        late_day = report_cell(row, 'D28')
        expect(late_day).to_have_text('—')
        late_day.click()
        expect(page.get_by_label('日期', exact=True)).to_have_value('2026-11-28')
        page.get_by_label('小订量').fill('9')
        before_apply = matrix.evaluate('el => el.scrollLeft')
        confirm(page)
        expect(late_day).to_have_text('9')
        assert abs(matrix.evaluate('el => el.scrollLeft') - before_apply) <= 1
        search = page.locator('#search')
        search.fill('2026-11-05')
        expect(rows).to_have_count(1)
        match = report_cell(row, '2026-11-05')
        expect(match).to_have_text('25')
        expect(match).to_have_attribute('data-secondary', '焕新')
        search.fill('2026-11-01')
        expect(report_cell(row, '2026-11-01')).to_have_text('0')
        search.fill('2026-11')
        expect(rows).to_have_count(2)
        expect(report_cell(report_row(page, '待定车型'), '2026-11-02')).to_have_text(['17', '31'])
        search.fill('')
        expect(page.locator('#report-axis')).to_have_value('day')
        expect(late_day).to_have_text('9')
        assert abs(matrix.evaluate('el => el.scrollLeft') - before_apply) <= 1
        matrix.evaluate('el => el.scrollLeft = 0')
        expect(report_cell(row, 'D1')).to_have_text('0')
        historical = report_row(page, '仅有历史进度')
        expect(historical).to_have_count(0)
        metric.select_option('小订期退订进度')
        expect(report_cell(row, 'D1')).to_have_text('63')
        expect(report_cell(historical, 'D3')).to_have_text('57')
        expect(historical.locator('[data-campaign-action=report-add]')).to_have_count(0)
        metric.select_option('小订by天')
        stage.select_option('焕新')
        expect(rows).to_have_count(2)
        expect(report_cell(row, 'D1')).to_have_text('25')
        expect(report_cell(row, 'D1')).to_have_attribute('data-axis', '2026-11-05')
        report_cell(row, 'D1').click()
        expect(page.get_by_label('日期', exact=True)).to_have_value('2026-11-05')
        page.get_by_label('小订量').fill('27')
        confirm(page)
        view(page, 'businessTotals')
        totals = page.locator('#content tbody tr[data-model="矩阵车型"]')
        expect(totals).to_have_count(1)
        expect(totals.locator('select[data-report-stage]')).to_have_value('焕新')
        totals.locator('[data-campaign-action=totals]').click()
        expect(page.get_by_label('大定量', exact=True)).to_have_value('82')
        page.locator('.campaign-rates summary').click()
        expect(page.get_by_label('净大定率', exact=True)).to_have_text('25%')
        page.locator('#cancel').click()
        view(page, 'dailyOrders')
        stage.select_option('标准')
        expect(report_cell(row, 'D1')).to_have_text('0')
        missing_date = report_row(page, '待定车型')
        missing_date.locator('[data-report-calendar]').click()
        expect(page.locator('#report-axis')).to_have_value('date')
        entries = report_cell(missing_date, '2026-11-02')
        expect(entries).to_have_text(['17', '31'])
        entries.last.click()
        expect(page.get_by_label('小订量')).to_have_value('31')
        page.get_by_label('小订量').fill('19')
        confirm(page)
        page.locator('#save').click()
        ready(page)
        expect(page.locator('#save')).to_be_disabled()
        sheets = {sheet['name']: sheet['rows'] for sheet in campaign.get()['sheets']}
        assert sheets['车型汇总'][2][26] == .25
        observed = {}
        for record in sheets['小订by天']:
            if record[0] == '代际名':
                header = record
            else:
                fields = dict(zip(header, record))
                observed.setdefault((record[0], fields.get('二级代际名') or ''), []).append(fields)
        assert [r['2026-11-05'] for r in observed['矩阵车型', '焕新']] == [27]
        assert [r['2026-11-01'] for r in observed['矩阵车型', '标准']] == [0]
        assert [r['2026-11-28'] for r in observed['矩阵车型', '标准']] == [9]
        assert [r['2026-11-02'] for r in observed['待定车型', '']] == [17, 19]
        assert len(observed) == 3
        metric.select_option('小订期退订进度')
        stage.select_option('标准')
        expect(report_cell(row, 'D1')).to_have_text('63')
        page.close()


def check_readonly(browser):
    with TemporaryDirectory() as directory, isolated_server(directory, readonly=True) as (url, store, *_):
        CampaignStore(store.root).import_legacy(0, legacy_file('只读车型'), '测试')
        page = browser.new_page()
        page.goto(url)
        ready(page)
        expect(action(page, 'add-stage')).to_have_count(0)
        page.locator('#data-tools').click()
        expect(page.locator('#campaign-import')).to_be_hidden()
        expect(page.locator('#campaign-undo-import')).to_be_hidden()
        page.locator('#data-tools').click()
        tab(page, 'reporting')
        page.locator('#campaign-progress').select_option('小转大进度')
        row = report_row(page, '只读车型')
        expect(row.get_by_text('6', exact=True)).to_be_visible()
        expect(action(page, 'report-cell')).to_have_count(0)
        expect(action(page, 'report-add')).to_have_count(0)
        expect(action(page, 'batch')).to_have_count(0)
        action(page, 'report-detail').click()
        expect(page.locator('#dialog')).to_be_visible()
        expect(page.locator('#dialog input[type=number]')).to_have_count(0)
        page.close()


def check_review_backfill(browser):
    with TemporaryDirectory() as directory, isolated_server(directory) as (url, store, *_):
        campaign = CampaignStore(store.root)
        model = '逐日回填车型'
        fields = {1: model, 9: '2026-11-01', 10: '2026-11-02', 11: '2026-11-03', 12: '2026-11-04'}
        campaign.save(0, [{'sheet': '车型汇总', 'row': 2, 'col': col, 'value': value}
                          for col, value in fields.items()], '测试')
        page = browser.new_page()
        page.goto(url)
        ready(page)
        fill_owner(page)
        tab(page, 'reporting')
        metric = page.locator('#campaign-progress')
        for sheet_name, values in [('小订by天', '2026-11-01\t12\n2026-11-02\t0'),
                                   ('小转大进度', 'D1\t2\nD2\t1'), ('直接大定进度', 'D1\t0')]:
            metric.select_option(sheet_name)
            page.get_by_role('button', name='批量粘贴').click()
            page.locator('[name=rows]').fill(values)
            confirm(page)
        view(page, 'businessTotals')
        action(page, 'totals').click()
        for label, expected in [('总小订', '12'), ('小订转大定量', '3'), ('直接大定量', ''),
                                ('大定量', ''), ('小订后退定', ''), ('首销期净大定', ''), ('首销期锁单', '')]:
            expect(page.get_by_label(label, exact=True)).to_have_value(expected)
        page.locator('#cancel').click()
        view(page, 'dailyOrders')
        metric.select_option('直接大定进度')
        action(page, 'report-add').click()
        page.get_by_label('日序').fill('2')
        page.get_by_label('数量', exact=True).fill('4')
        confirm(page)
        page.locator('#publish').click()
        confirm(page)
        view(page, 'businessTotals')
        action(page, 'totals').click()
        expect(page.get_by_label('直接大定量', exact=True)).to_have_value('4')
        expect(page.get_by_label('大定量', exact=True)).to_have_value('7')
        page.locator('.campaign-rates summary').click()
        expect(page.get_by_label('小订转化率', exact=True)).to_have_text('25%')
        page.locator('#cancel').click()
        published = campaign.published()['sheets']
        assert len(published) == 7 and published[0]['rows'][1][18:20] == [7, 4]
        campaign.import_legacy(campaign.get()['revision'], legacy_file(model), '迁入旧表', confirm_replace=True)
        page.reload()
        ready(page)
        tab(page, 'reporting')
        page.locator('#export').click()
        expect(page.locator('[name=sheets]')).to_have_count(7)
        source = page.locator('[name=source]')
        daily = page.get_by_role('checkbox', name='小订by天', exact=True)
        daily.uncheck()
        source.select_option('draft')
        expect(page.get_by_role('checkbox', name='小订进度', exact=True)).to_be_checked()
        expect(daily).not_to_be_checked()
        source.select_option('published')
        expect(page.get_by_role('checkbox', name='小订进度', exact=True)).to_have_count(0)
        assert len(download(page)) == 6
        page.close()


def check_running_previous_backend(browser):
    with TemporaryDirectory() as directory, isolated_server(directory) as (url, store, *_):
        CampaignStore(store.root).import_legacy(0, legacy_file('已导入车型'), '测试迁入')
        page = browser.new_page()
        page.route('**/api/session', lambda route: route.fulfill(
            status=404, content_type='application/json', body='{"error":"unknown endpoint"}'))
        exports = []
        page.on('request', lambda request: exports.append(request.url) if request.url.endswith('/campaign/export') else None)
        page.goto(url)
        ready(page)
        expect(page.locator('#content')).to_contain_text('已导入车型')
        page.locator('#export').click()
        expect(page.locator('#error-notice')).to_contain_text('重启')
        assert not exports, '旧后端会忽略配置导出范围'
        tab(page, 'reporting')
        view(page, 'businessTotals')
        expect(action(page, 'totals')).to_be_visible()
        page.close()


if __name__ == '__main__':
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        check(browser)
        check_stage_matrix(browser)
        check_review_backfill(browser)
        check_readonly(browser)
        check_running_previous_backend(browser)
        browser.close()
    print('Campaign configuration, reporting, rule-draft isolation and scoped Excel exports passed.')
