"""Exercise a no-preorder launch through the normal campaign editor."""
from io import BytesIO
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

from browser_campaign import action, confirm, report_row, tab, view
from browser_rules_check import fill_owner, isolated_server, ready
from campaign_store import CampaignStore
from stage_workbook import DAILY, SUMMARY, empty_workbook, export_workbook


def legacy_workbook():
    book = load_workbook(BytesIO(export_workbook(empty_workbook())))
    summary = book[SUMMARY]
    for row, name in enumerate(('直接首销', '含历史记录', '待定车型'), 2):
        summary.cell(row, 1, name)
        if name != '待定车型':
            summary.cell(row, 9, 'NA')
            summary.cell(row, 10, 'NA')
            summary.cell(row, 11, '2026-02-01')
            summary.cell(row, 12, '2026-02-02')
    daily = book[DAILY]
    daily.delete_rows(1)
    daily.append(['代际名', '2026-01-01', '合计', '二级代际名'])
    daily.append(['含历史记录', 7, 7])
    raw = BytesIO()
    book.save(raw)
    book.close()
    return raw.getvalue()


def check(browser):
    with TemporaryDirectory() as tmp, isolated_server(tmp) as (url, catalog, *_):
        store = CampaignStore(catalog.root)
        store.import_legacy(0, legacy_workbook(), '浏览器测试')
        page = browser.new_page()
        page.goto(url)
        ready(page)
        fill_owner(page)
        schedule_row = page.locator('#content tbody tr').filter(has_text='直接首销')
        expect(schedule_row.locator('[data-label="小订期"]')).to_have_text('无小订')
        expect(schedule_row.locator('[data-label="首销期"]')).to_contain_text('2026-02-01')
        schedule_row.get_by_role('button', name='编辑').click()
        toggle = page.get_by_role('checkbox', name='无小订')
        expect(toggle).to_be_checked()
        expect(page.get_by_label('小订开始日期', exact=True)).to_be_hidden()
        page.get_by_label('产品档位').fill('中型')
        confirm(page)
        expect(schedule_row).to_contain_text('无小订')

        tab(page, 'reporting')
        view(page, 'dailyOrders')
        expect(report_row(page, '直接首销')).to_have_count(0)
        historic = report_row(page, '含历史记录')
        expect(historic.get_by_role('button', name='填报')).to_have_count(0)
        historic.get_by_role('button', name='明细').click()
        expect(page.locator('#dialog-body')).to_contain_text('7')
        expect(page.locator('#dialog input[type=number]')).to_have_count(0)
        page.locator('#cancel').click()
        expect(report_row(page, '待定车型')).to_be_visible()
        action(page, 'batch').click()
        expect(page.locator('[name=stage] option')).not_to_contain_text(['直接首销'])
        page.locator('#cancel').click()
        for target in ('conversions','smallOrderCancellations'):
            view(page, target)
            expect(report_row(page, '直接首销')).to_have_count(0)
        view(page, 'directOrders')
        report_row(page, '直接首销').get_by_role('button', name='填报').click()
        page.get_by_label('数量', exact=True).fill('12')
        confirm(page)
        report_row(page, '直接首销').get_by_role('button', name='明细').click()
        expect(page.get_by_label('D1', exact=True)).to_have_value('12')
        page.locator('#cancel').click()
        view(page, 'businessTotals')
        report_row(page, '直接首销').get_by_role('button', name='核对').click()
        expect(page.get_by_label('总小订', exact=True)).to_have_count(0)
        expect(page.get_by_label('首销期净大定', exact=True)).to_be_visible()
        page.locator('#cancel').click()

        tab(page, 'schedule')
        schedule_row.get_by_role('button', name='编辑').click()
        toggle.uncheck()
        page.get_by_label('小订开始日期', exact=True).fill('2026-01-01')
        page.get_by_label('小订结束日期', exact=True).fill('2026-01-02')
        confirm(page)
        expect(schedule_row.locator('[data-label="小订期"]')).to_contain_text('2026-01-01')
        schedule_row.get_by_role('button', name='编辑').click()
        toggle.check()
        confirm(page)
        expect(schedule_row.locator('[data-label="小订期"]')).to_have_text('无小订')
        page.locator('#save').click()
        ready(page)
        assert store.get()['sheets'][0]['rows'][1][8:12] == ['NA','NA','2026-02-01','2026-02-02']
        page.close()


if __name__ == '__main__':
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge')
        check(browser)
        browser.close()
    print('No-preorder campaign browser checks passed.')
