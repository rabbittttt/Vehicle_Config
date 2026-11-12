import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1];out=root/'artifacts';out.mkdir(exist_ok=True)
sys.path.insert(0,str(root))
import tempfile, threading, atexit
from catalog import Store
from server import make_server
tmp=tempfile.TemporaryDirectory();store=Store(tmp.name);server=make_server(store,port=0)
threading.Thread(target=server.serve_forever,daemon=True).start()
atexit.register(lambda: (server.shutdown(),server.server_close(),store.db.close(),tmp.cleanup()))
base='http://127.0.0.1:'+str(server.server_port)
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge');page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base+'/vehicle-config/');page.wait_for_function("() => document.querySelector('#status').textContent.includes('已保存')")
    page.locator('.panel-head [data-action="add:models"]').click()
    for field,value in {'brand':'验收品牌','series':'验收车型','generation':'第一代','year':'2026','energy':'纯电','tier':'中大型'}.items():page.locator(f'[name="{field}"]').fill(value)
    page.locator('#confirm').click();page.locator('[data-tab="matrix"]').click()
    for name in ['标准版','长续航版','旗舰版']:
        page.locator('[data-action="add:versions"]').click();page.locator('[name="name"]').fill(name);page.locator('#confirm').click()
    for category,name in [('外观','珍珠白'),('外观','曜石黑'),('内饰','米色内饰'),('内饰','黑色内饰'),('轮毂','20 英寸轮毂'),('轮毂','21 英寸轮毂')]:
        page.locator('[data-action="add:options"]').click();page.locator('[name="name"]').fill(name);page.locator('[name="category"]').select_option(category);page.locator('#confirm').click()
    cells=page.locator('#content select')
    for i in range(cells.count()):cells.nth(i).select_option(['标配','选配','不可选','未配置'][i%4])
    page.locator('#save').click();page.wait_for_function("() => document.querySelector('#status').textContent.includes('已保存')")
    page.locator('#publish').click();page.get_by_role('button',name='确认发布',exact=True).click();page.wait_for_function("() => document.querySelector('#published').textContent==='已发布 v1'")
    page.screenshot(path=str(out/'desktop-matrix.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'mobile-matrix.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile overflow'
    page.set_viewport_size({'width':1440,'height':1000})
    page.locator('[data-tab="models"]').click();page.get_by_role('button',name='复制年款').click();page.locator('[name="year"]').fill('2027');page.locator('#confirm').click();page.locator('#save').click();page.wait_for_function("() => document.querySelector('#status').textContent.includes('已保存')")
    page.reload();page.wait_for_function("() => document.querySelector('#status').textContent.includes('已保存')");assert page.locator('#content tbody tr').count()==2
    with page.expect_download() as event:page.locator('#export').click()
    download=event.value;download.save_as(str(out/'roundtrip.xlsx'))
    page.locator('#file').set_input_files(str(out/'roundtrip.xlsx'));page.get_by_role('button',name='应用到编辑区').wait_for();page.locator('#confirm').click();page.locator('#save').click();page.wait_for_function("() => document.querySelector('#status').textContent.includes('已保存')")
    page.locator('[data-tab="history"]').click();page.locator('[data-history]').first.click();page.get_by_role('button',name='恢复到草稿').click();assert '未保存' in page.locator('#status').inner_text()
    assert not errors,errors
    browser.close();print('Browser checks passed: matrix, publish, copy, reload, XLSX import/export, history restore, desktop/mobile. Test data only.')
