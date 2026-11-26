"""Exercise new web-owned schedules, daily orders, progress and selected exports."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright

from browser_rules_check import add_vehicle, isolated_server, ready
from catalog import empty
from stage_workbook import apply_edits, empty_workbook, export_workbook


def confirm(page):
    page.locator('#confirm').click()
    ready(page)
    expect(page.locator('#dialog')).to_be_hidden()


def legacy_preview(page):
    workbook = apply_edits(empty_workbook(), [
        {'sheet':'车型汇总','row':2,'col':1,'value':'新导入车型'},
        {'sheet':'小订by天','model':'新导入车型','date':'2026-11-03','value':3},
    ])
    page.locator('#data-tools').click()
    with page.expect_file_chooser() as chooser:
        page.get_by_role('button', name='导入小订首销旧表').click()
    chooser.value.set_files({
        'name':'验收旧表.xlsx',
        'mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'buffer':export_workbook(workbook),
    })
    expect(page.get_by_role('heading', name='旧表导入预览')).to_be_visible()
    expect(page.locator('#dialog')).to_contain_text('现有草稿行数')
    expect(page.locator('#dialog-body details')).to_have_count(2)
    for detail in page.locator('#dialog-body summary').all():
        detail.click()
    expect(page.locator('#dialog-body li')).to_have_text(['新导入车型','测试 2026款 · 纯电版'])
    expect(page.locator('#confirm')).to_have_text('替换草稿')


def check(browser):
    with TemporaryDirectory() as directory, isolated_server(directory) as (url, store, *_):
        catalog = empty()
        catalog['models'].append({'id':'m','brand':'测试','series':'甲','generation':'测试 2026款',
                                  'subgeneration':'纯电版','energy':'纯电','tier':'紧凑型',
                                  'year':'2026','status':'在售'})
        catalog['phases'].append({'id':'p','model_id':'m','name':'标准发布',
                                  'presale_date':'2026-11-01','sale_date':'2026-11-04'})
        store.save(catalog, 0, '验收', publish=True)
        page = browser.new_page(viewport={'width': 1360, 'height': 880})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url)
        ready(page)
        page.get_by_role('button', name='小订与首销', exact=True).click()
        ready(page)
        page.get_by_role('button', name='新增发布日程').click()
        page.get_by_label('产品档位').fill('自定档位')
        page.get_by_label('代际名', exact=True).fill('测试 2026款')
        expect(page.get_by_label('二级代际名')).to_have_value('纯电版')
        expect(page.get_by_label('小订开始日期')).to_have_value('2026-11-01')
        expect(page.get_by_label('开始大定日期')).to_have_value('2026-11-04')
        expect(page.get_by_label('产品档位')).to_have_value('自定档位')
        expect(page.get_by_label('能源类型')).to_have_value('纯电')
        page.get_by_label('小订结束日期').fill('2026-11-03')
        confirm(page)
        expect(page.locator('#content')).to_contain_text('纯电版')
        page.get_by_role('button', name='日程', exact=True).click()
        expect(page.locator('label').filter(has_text='小订天数').locator('output')).to_have_text('3')
        page.locator('#cancel').click()
        page.get_by_role('button', name='每日小订', exact=True).click()
        page.get_by_role('button', name='批量粘贴').click()
        page.locator('[name=rows]').fill('2026-11-01\t0\n2026-11-02\t15')
        confirm(page)
        expect(page.locator('#content tbody tr')).to_have_count(2)
        page.locator('#content tbody tr').first.get_by_role('button', name='编辑').click()
        page.get_by_label('小订量').fill('16')
        confirm(page)
        page.locator('#undo').click()
        ready(page)
        expect(page.locator('#content tbody tr').first).to_contain_text('15')
        page.locator('#redo').click()
        ready(page)
        expect(page.locator('#content tbody tr').first).to_contain_text('16')
        page.get_by_role('button', name='阶段进度', exact=True).click()
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
        page.locator('[data-tab=campaign]').click()
        ready(page)
        page.get_by_role('button', name='每日小订', exact=True).click()
        expect(page.locator('#content tbody tr').first).to_contain_text('16')
        page.locator('#publish').click()
        ready(page)
        confirm(page)
        page.locator('#export').click()
        ready(page)
        expect(page.locator('[name=sheets]')).to_have_count(8)
        for box in page.locator('[name=sheets]').all():
            box.set_checked(box.input_value()=='小订by天')
        page.get_by_label('开始日期', exact=True).fill('2026-11-02')
        page.get_by_label('截止日期', exact=True).fill('2026-11-02')
        page.locator('summary').click()
        page.get_by_role('checkbox', name='纯电版').check()
        with page.expect_download() as received:
            confirm(page)
        book = load_workbook(BytesIO(Path(received.value.path()).read_bytes()),read_only=True,data_only=True)
        assert book.sheetnames == ['小订by天']
        values = list(book.active.values)
        assert any(16 in row for row in values)
        assert any('2026-11-02' in str(cell) for row in values for cell in row)
        assert all('2026-11-01' not in str(cell) for row in values for cell in row)
        book.close()
        # Catalog edits survive global campaign import and undo, including cancellation.
        page.locator('[data-tab=models]').click()
        add_vehicle(page)
        legacy_preview(page)
        page.locator('#cancel').click()
        expect(page.locator('#save')).to_be_enabled()
        legacy_preview(page)
        confirm(page)
        expect(page.locator('#content')).to_contain_text('未保存车型甲')
        expect(page.locator('#save')).to_be_enabled()
        page.locator('[data-tab=campaign]').click()
        expect(page.locator('#content')).to_contain_text('新导入车型')
        expect(page.locator('#import')).to_be_hidden()
        page.locator('#data-tools').click()
        page.get_by_role('button', name='撤销旧表导入').click()
        confirm(page)
        expect(page.locator('#content')).to_contain_text('测试 2026款')
        expect(page.locator('#content')).not_to_contain_text('新导入车型')
        # An unsaved campaign change can't be overwritten from this tab or from other modules.
        page.get_by_role('button', name='日程', exact=True).click()
        page.get_by_label('产品档位').fill('保留本地编辑')
        confirm(page)
        page.locator('[data-tab=models]').click()
        page.locator('#data-tools').click()
        page.get_by_role('button', name='导入小订首销旧表').click()
        expect(page.locator('#error-notice')).to_contain_text('先在「小订与首销」保存草稿')
        page.locator('[data-tab=campaign]').click()
        expect(page.locator('#save')).to_be_enabled()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('#mobile-tab').select_option('campaign')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        page.close()


def check_readonly(browser):
    with TemporaryDirectory() as directory, isolated_server(directory, readonly=True) as (url, *_):
        page=browser.new_page()
        page.goto(url)
        ready(page)
        page.locator('#data-tools').click()
        expect(page.locator('#campaign-import')).to_be_hidden()
        expect(page.locator('#campaign-undo-import')).to_be_hidden()
        page.close()


if __name__ == '__main__':
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        check(browser)
        check_readonly(browser)
        browser.close()
    print('Campaign web workflow passed.')
