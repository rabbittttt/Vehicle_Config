"""Browser acceptance for separated configuration, rule and daily reporting views."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

from browser_rules_check import isolated_server, ready
from campaign_store import CampaignStore
from stage_workbook import SUMMARY_FIELDS, apply_edits, empty_workbook, export_workbook


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


def legacy_file(model):
    fields = {1: model, 2: '纯电版', 3: '中型', 4: '纯电', 5: '标准发布',
              9: '2026-11-01', 10: '2026-11-02', 19: 77}
    edits = [{'sheet': '车型汇总', 'row': 2, 'col': col, 'value': value}
             for col, value in fields.items()]
    return export_workbook(apply_edits(empty_workbook(), edits))


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
    ready(page)
    page.locator('#export').click()
    ready(page)
    expect(page.locator('[name=sheets], [name=dateFrom]')).to_have_count(0)
    sheets = download(page)
    assert len(sheets) == 1
    headers, row = next(iter(sheets.values()))
    assert list(headers) == SUMMARY_FIELDS[:14]
    assert row[2] == '中大型' and row[12] == 3


def check_reporting(page):
    tab(page, 'reporting')
    expect(page.locator('[data-view=dailyOrders]')).to_have_attribute('aria-pressed', 'true')
    view(page, 'businessTotals')
    action(page, 'totals').click()
    expect(page.get_by_label('大定量', exact=True)).to_have_value('77')
    expect(page.locator('#dialog [name=f2], #dialog [name=f9]')).to_have_count(0)
    page.get_by_label('大定量', exact=True).fill('83')
    confirm(page)
    view(page, 'dailyOrders')
    page.get_by_role('button', name='批量粘贴').click()
    page.locator('[name=rows]').fill('2026-11-01\t0\n2026-11-02\t15')
    confirm(page)
    rows = page.locator('#content tbody tr')
    expect(rows).to_have_count(2)
    expect(rows.last.locator('td').nth(2)).to_have_text('0')
    rows.first.get_by_role('button', name='编辑').click()
    page.get_by_label('小订量').fill('16')
    confirm(page)
    page.locator('#undo').click()
    expect(rows.first.locator('td').nth(2)).to_have_text('15')
    page.locator('#redo').click()
    expect(rows.first.locator('td').nth(2)).to_have_text('16')
    view(page, 'stageProgress')
    page.get_by_label('指标', exact=True).select_option('小转大进度')
    page.get_by_label('分组', exact=True).select_option('2')
    page.get_by_role('button', name='填进度', exact=True).click()
    page.get_by_label('日序').fill('2')
    page.get_by_label('百分比').fill('15')
    confirm(page)
    expect(page.locator('#content')).to_contain_text('15%')
    page.locator('#save').click()
    ready(page)
    page.reload()
    ready(page)
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
    page.locator('#mobile-tab').select_option('reporting')
    expect(page.locator('[data-view=dailyOrders]')).to_be_visible()
    view(page, 'businessTotals')
    action(page, 'totals').first.click()
    expect(page.get_by_label('大定量', exact=True)).to_have_value('83')
    page.locator('#cancel').click()
    page.locator('#mobile-tab').select_option('schedule')
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


def check_readonly(browser):
    with TemporaryDirectory() as directory, isolated_server(directory, readonly=True) as (url, *_):
        page = browser.new_page()
        page.goto(url)
        ready(page)
        expect(action(page, 'add-stage')).to_have_count(0)
        page.locator('#data-tools').click()
        expect(page.locator('#campaign-import')).to_be_hidden()
        expect(page.locator('#campaign-undo-import')).to_be_hidden()
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
        check_readonly(browser)
        check_running_previous_backend(browser)
        browser.close()
    print('Campaign configuration, reporting, rule-draft isolation and scoped Excel exports passed.')
