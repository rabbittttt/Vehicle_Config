"""Isolated browser acceptance for the bulk editing/import workflow."""
import copy
import io
import sys
import tempfile
import threading
from pathlib import Path

from openpyxl import Workbook, load_workbook
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from catalog import Store, empty
from server import make_server


def workbook(sheet, rows, filename):
    book = Workbook()
    tab = book.active
    tab.title = sheet
    for row in rows:
        tab.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return {"name": filename, "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": buffer.getvalue()}


def seed():
    data = empty()
    for ident, series in [('m1', '验收车型甲'), ('m2', '验收车型乙')]:
        data['models'].append(dict(id=ident, brand='验收品牌', series=series, generation='一代', subgeneration='', year='2026', energy='纯电', tier='', status='规划'))
    for ident, model, name in [('v1', 'm1', '标准版'), ('v2', 'm1', '长续航版'), ('v3', 'm2', '标准版')]:
        data['versions'].append(dict(id=ident, model_id=model, name=name, power='', battery='', status='规划'))
    for ident, model, name in [('o1', 'm1', '珍珠白'), ('o2', 'm1', '曜石黑'), ('o3', 'm2', '珍珠白')]:
        data['options'].append(dict(id=ident, model_id=model, category='外观', name=name, status='规划'))
    data['matrix'].append(dict(id='mx1', version_id='v1', option_id='o1', state='标配'))
    return data


def main():
    out = ROOT / 'artifacts'
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        store = Store(temporary)
        store.save(seed(), 0, '浏览器验收')
        server = make_server(store, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        release_save = threading.Event()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge')
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/vehicle-config/')
                expect(page.locator('#status')).to_contain_text('已保存')
                current = lambda: page.evaluate('() => structuredClone(data)')
                ready = lambda: expect(page.locator('body')).to_have_attribute('aria-busy', 'false')
                confirm = lambda: page.locator('#confirm').click()

                # Searching configuration rows must retain each relevant version column.
                page.locator('[data-tab="matrix"]').click()
                page.locator('#search').fill('珍珠白')
                expect(page.locator('#count')).to_have_text('2 项配置 · 3 个版本')
                expect(page.locator('.matrix-table thead th')).to_have_count(5)
                page.locator('#search').fill('')
                page.locator('[data-select="o1"]').check()
                page.locator('[data-select="o3"]').check()
                page.locator('#batch-matrix').click()
                expect(page.locator('#only-unset')).to_be_checked()
                expect(page.locator('[name="batch-version"]')).to_have_count(3)
                confirm(); ready()
                matrix = current()['matrix']
                assert {(r['version_id'], r['option_id'], r['state']) for r in matrix} == {('v1', 'o1', '标配'), ('v2', 'o1', '选配'), ('v3', 'o3', '选配')}
                page.locator('#undo').click()
                assert current()['matrix'] == seed()['matrix']
                expect(page.locator('#save')).to_be_disabled()
                page.locator('#redo').click()
                assert len(current()['matrix']) == 3

                # Save waits on a controlled backend latch; all inputs remain disabled.
                original_save = store.save
                def delayed_save(*args, **kwargs):
                    if not release_save.wait(timeout=10):
                        raise RuntimeError('Browser save latch was not released')
                    return original_save(*args, **kwargs)
                store.save = delayed_save
                page.locator('#save').click()
                expect(page.locator('body')).to_have_attribute('aria-busy', 'true')
                assert page.evaluate("() => [...document.querySelectorAll('button,input,select,textarea')].every(e=>e.disabled)")
                release_save.set()
                ready(); store.save = original_save
                expect(page.locator('#undo')).to_be_disabled()
                expect(page.locator('#redo')).to_be_disabled()
                assert store.get()['draft']['data']['matrix'] == current()['matrix']

                # Batch add captures FormData before busy disables form fields.
                page.locator('[data-view="versions"]').click()
                page.locator('[data-action="batch-add:versions"]').click()
                page.locator('[name="model_id"]').select_option('m1')
                page.locator('[name="names"]').fill('舒享版\n旗舰版')
                confirm(); ready()
                assert len(current()['versions']) == 5
                page.locator('[data-view="options"]').click()
                page.locator('[data-action="batch-add:options"]').click()
                page.locator('[name="model_id"]').select_option('m1')
                page.locator('[name="category"]').select_option('内饰')
                page.locator('[name="names"]').fill('米色内饰\n黑色内饰')
                confirm(); ready()
                assert len(current()['options']) == 5
                page.locator('#select-all').check()
                page.locator('#batch-status').select_option('在售')
                page.locator('#apply-status').click(); confirm(); ready()
                assert all(row['status'] == '在售' for row in current()['options'])
                page.locator('#save').click(); ready()

                with page.expect_download() as event:
                    page.locator('#template').click()
                target = Path(temporary) / 'template.xlsx'
                event.value.save_as(str(target)); ready()
                template = load_workbook(target)
                assert template['车型档案'].max_row == 1
                assert template['车型档案']['B1'].value == '品牌'
                template.close()

                # Two workbooks resolve a new model and its version in any file order.
                model_file = workbook('车型档案', [['品牌', '车系', '代际', '年款', '能源类型'], ['导入品牌', '导入车型', '一代', 2027, '纯电']], '车型清单.xlsx')
                version_file = workbook('版本', [['车型', '名称'], ['导入车型', '新增版本']], '版本清单.xlsx')
                before = copy.deepcopy(store.get())
                page.locator('#file').set_input_files([version_file, model_file])
                expect(page.locator('#dialog-title')).to_have_text('导入预览'); ready()
                expect(page.locator('#dialog-body')).to_contain_text('车型清单.xlsx')
                expect(page.locator('#dialog-body')).to_contain_text('版本清单.xlsx')
                assert store.get() == before
                confirm(); ready()
                assert len(current()['models']) == 3
                assert store.get() == before
                page.locator('#save').click(); ready()
                assert len(store.get()['draft']['data']['models']) == 3

                # Preview merges into the editor. Cancel and undo must preserve pre-existing edits.
                page.locator('[data-tab="matrix"]').click()
                page.locator('[data-v="v1"][data-o="o1"]').select_option('不可选')
                dirty_update = workbook('车型档案', [['编号', '产品档位'], ['m1', '批量档位']], '批量档位.xlsx')
                saved_before = store.get()
                edited_before = current()
                page.locator('#file').set_input_files(dirty_update)
                expect(page.locator('#dialog-title')).to_have_text('导入预览'); ready()
                page.locator('#cancel').click()
                assert current() == edited_before and store.get() == saved_before
                page.locator('#file').set_input_files(dirty_update)
                expect(page.locator('#dialog-title')).to_have_text('导入预览')
                confirm(); ready()
                assert current()['models'][0]['tier'] == '批量档位'
                assert next(row for row in current()['matrix'] if row['id'] == 'mx1')['state'] == '不可选'
                assert store.get() == saved_before
                page.locator('#undo').click()
                assert current() == edited_before
                page.locator('#redo').click()
                page.locator('#save').click(); ready()

                # Invalid rows expose precise locations and never offer an apply action.
                bad_file = workbook('版本', [['车型', '名称'], ['不存在的车型', '无效版本']], '错误清单.xlsx')
                before = copy.deepcopy(store.get())
                page.locator('#file').set_input_files(bad_file)
                expect(page.locator('#dialog-title')).to_have_text('导入预览'); ready()
                expect(page.locator('#dialog-body')).to_contain_text('第 2 行')
                expect(page.locator('#dialog-body')).to_contain_text('版本')
                expect(page.locator('#confirm')).to_be_hidden()
                assert store.get() == before
                page.locator('#close').click()

                # Another writer saving after preview must block apply without replacement.
                update_file = workbook('车型档案', [['编号', '产品档位'], ['m1', '导入档位']], '更新清单.xlsx')
                page.locator('#file').set_input_files(update_file)
                expect(page.locator('#dialog-title')).to_have_text('导入预览'); ready()
                editor_before = current()
                remote = store.get()['draft']
                remote['data']['models'][0]['tier'] = '另一用户档位'
                store.save(remote['data'], remote['revision'], '另一验收用户')
                confirm(); ready()
                expect(page.locator('#dialog-error')).to_contain_text('草稿已被其他人修改')
                assert current() == editor_before
                assert store.get()['draft']['data']['models'][0]['tier'] == '另一用户档位'
                page.locator('#close').click(); page.locator('#reload').click(); ready()

                # With one available source, export starts immediately.
                with page.expect_download() as event:
                    page.locator('#export').click()
                assert '当前编辑' in event.value.suggested_filename
                ready()
                page.locator('[data-tab="matrix"]').click()
                page.locator('#model').select_option('m1')
                page.screenshot(path=str(out / 'bulk-desktop.png'), full_page=True)
                page.set_viewport_size({'width':390, 'height':844})
                assert page.evaluate('() => document.documentElement.scrollWidth <= window.innerWidth')
                assert page.evaluate("() => {const b=document.querySelector('#save'),r=b.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('#save')===b;}"), 'Sticky matrix cells cover the save footer'
                page.screenshot(path=str(out / 'bulk-mobile.png'), full_page=True)
                assert not errors, errors
                browser.close()
                print('Bulk browser checks passed: matrix search, scoped bulk fill, undo/redo, request lock, batch create/status, template, multi-file import, row errors, concurrent preview conflict, export, desktop/mobile.')
        finally:
            release_save.set()
            server.shutdown(); server.server_close(); thread.join()
            store.db.close()


if __name__ == '__main__':
    main()
