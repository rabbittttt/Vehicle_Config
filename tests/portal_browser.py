import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge');page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(sys.argv[1]+'/#/app/vehicle-config');frame=page.frame_locator('iframe');expect(frame.locator('h1')).to_have_text('车型配置');expect(frame.locator('#status')).to_contain_text('已保存')
    frame.locator('.panel-head [data-action="add:models"]').click()
    for field,value in {'brand':'代理验收','series':'测试车型','generation':'一代','year':'2026','energy':'纯电'}.items():frame.locator('[name="'+field+'"]').fill(value)
    frame.locator('#confirm').click();frame.locator('#save').click();expect(frame.locator('#notice')).to_have_text('草稿已保存');page.reload();expect(frame.locator('#content')).to_contain_text('代理验收')
    frame.locator('#search').fill('不存在');expect(frame.locator('.empty')).to_be_visible();frame.locator('#search').fill('代理验收');expect(frame.locator('#content tbody tr')).to_have_count(1)
    page.screenshot(path=str(Path('artifacts/portal-desktop.png')),full_page=True);page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(Path('artifacts/portal-mobile.png')),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth');assert not errors,errors
    browser.close();print('Portal actual launcher, health, iframe, save, reload, search and responsive layout passed with isolated data.')
