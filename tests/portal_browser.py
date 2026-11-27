"""Verify the registered workbench iframe with isolated schedule and rule data."""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check(browser, url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.goto(url + '/#/app/vehicle-config')
    frame = page.frame_locator('iframe')
    expect(frame.locator('h1')).to_have_text('车型配置')
    expect(frame.locator('[data-view=vehicleIdentity]')).to_have_attribute('aria-pressed', 'true')

    frame.locator('[data-campaign-action=add-stage]').click()
    expect(frame.locator('#dialog form input')).to_have_count(4)
    expect(frame.get_by_label('小订开始日期', exact=True)).to_have_count(0)
    frame.get_by_label('代际名', exact=True).fill('代理验收 2026款')
    frame.get_by_label('能源类型', exact=True).fill('纯电')
    frame.locator('#confirm').click()
    frame.locator('#save').click()
    expect(frame.locator('#save')).to_be_disabled()
    page.reload()
    expect(frame.locator('#content')).to_contain_text('代理验收 2026款')
    expect(frame.locator('#content')).to_contain_text('纯电')
    expect(frame.locator('#content')).not_to_contain_text('总小订')

    search = frame.get_by_role('searchbox')
    search.fill('不存在的车型')
    expect(frame.locator('.empty')).to_be_visible()
    search.fill('代理验收')
    expect(frame.locator('#content tbody tr')).to_have_count(1)
    frame.locator('[data-view=launchSchedule]').click()
    frame.locator('[data-campaign-action=stage]').click()
    frame.get_by_label('小订开始日期', exact=True).fill('2026-11-01')
    frame.get_by_label('小订结束日期', exact=True).fill('2026-11-03')
    frame.locator('#confirm').click()
    frame.locator('#save').click()
    expect(frame.locator('#save')).to_be_disabled()
    frame.locator('[data-campaign-action=stage]').click()
    expect(frame.get_by_label('小订天数', exact=True)).to_have_text('3')
    expect(frame.get_by_label('小订开始日期', exact=True)).to_have_value('2026-11-01')
    frame.locator('#cancel').click()

    frame.locator('[data-tab=generation]').click()
    frame.locator('[data-view=gen_names]').click()
    expect(frame.locator('#content')).to_contain_text('standard')
    expect(frame.locator('h1')).to_have_text('车型配置')
    frame.locator('[data-tab=reporting]').click()
    expect(frame.locator('[data-view=dailyOrders]')).to_have_text('日常填报')
    expect(frame.locator('[data-view=stageProgress]')).to_have_count(0)
    expect(frame.locator('.report-matrix tbody tr[data-model="代理验收 2026款"]')).to_have_count(1)
    expect(frame.locator('#campaign-progress')).to_have_value('小订by天')
    frame.locator('[data-view=businessTotals]').click()
    expect(frame.locator('#content')).to_contain_text('总小订')
    expect(frame.locator('#content')).to_contain_text('代理验收 2026款')
    frame.locator('[data-tab=schedule]').click()
    frame.locator('[data-view=vehicleIdentity]').click()
    expect(frame.locator('#content')).not_to_contain_text('总小订')

    artifacts = Path('artifacts')
    page.screenshot(path=str(artifacts / 'portal-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    frame.locator('header nav [data-tab="reporting"]').click()
    expect(frame.locator('[data-view=dailyOrders]')).to_be_visible()
    frame.locator('header nav [data-tab="schedule"]').click()
    expect(frame.locator('#content')).to_contain_text('代理验收 2026款')
    page.screenshot(path=str(artifacts / 'portal-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert frame.locator('html').evaluate('node => node.scrollWidth <= window.innerWidth')
    assert not errors, errors
    page.close()


def check_account_change(browser, url):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.route('**/account-test', lambda route: route.fulfill(content_type='text/html',
               body='<iframe style="position:fixed;inset:0;width:100%;height:100%;border:0" src="/#/app/vehicle-config"></iframe>'))
    page.goto(url + '/account-test')
    portal = page.frame_locator('iframe')
    app = portal.frame_locator('iframe[data-app="vehicle-config"]')
    expect(app.locator('h1')).to_have_text('车型配置')

    def switch_account(name):
        with page.expect_response(lambda r: r.url.endswith('/vehicle-config/api/session') and r.status == 200) as reply:
            page.evaluate("""account => document.querySelector('iframe').contentWindow.postMessage(
                {account, roleCode: 2, roleName: '维护人'}, location.origin)""", name)
        assert reply.value.json()['user'] == name
        expect(app.locator('body')).to_have_attribute('aria-busy', 'false')

    switch_account('业务甲')
    app.locator('[data-campaign-action=add-stage]').click()
    app.get_by_label('代际名', exact=True).fill('上个账号的草稿')
    app.locator('#confirm').click()
    expect(app.locator('#save')).to_be_enabled()
    switch_account('业务乙')
    expect(app.locator('#save')).to_be_disabled()
    expect(app.locator('#content')).not_to_contain_text('上个账号的草稿')
    app.locator('#publish').click()
    app.locator('#confirm').click()
    expect(app.locator('#dialog')).to_be_hidden()
    app.locator('[data-tab=history]').click()
    expect(app.locator('#content tbody tr').first.locator('td').nth(2)).to_have_text('业务乙')
    page.close()


if __name__ == '__main__':
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        check(browser, sys.argv[1])
        check_account_change(browser, sys.argv[1])
        browser.close()
    print('Portal iframe: schedule save/reload, A–N ownership, rules, reporting and narrow navigation passed.')
