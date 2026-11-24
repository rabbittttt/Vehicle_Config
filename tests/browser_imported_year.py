"""Exercise year duplication and edits using the independent IDs from an imported workbook."""
import copy
import tempfile
from playwright.sync_api import expect, sync_playwright
from browser_rules_check import isolated_server, ready
from excel_io import excel
from test_catalog import fixture


def main():
    data = fixture()
    data['versions'][0]['id'] = data['options'][0]['id'] = '01'
    data['options'][0]['category'] = '舒适/功能'
    data['aliases']=[dict(id='alias1',source='ORDER',name='Straße',target_type='models',target_id='m1')]
    data['phases'] = [dict(id='01', model_id='m1', name='首销', sale_date='2026-03-01')]
    data['matrix'] = [dict(id='01', version_id='01', option_id='01', state='标配')]
    data['models'].append(dict(data['models'][0], id='m1:旧', year='2025'))
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url,store,_,_), sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        page = browser.new_page()
        page.goto(url)
        ready(page)
        page.locator('#file').set_input_files(dict(name='旧车型.xlsx', mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buffer=excel(data)))
        page.get_by_role('button',name='应用到编辑区').click()
        ready(page)
        page.locator('#publish').click()
        page.get_by_role('button',name='确认发布').click()
        ready(page)
        original = copy.deepcopy(store.get()['published']['data'])
        page.get_by_role('row').filter(has_text='2025').get_by_role('button',name='编辑').click()
        expect(page.locator('[name=year]')).to_have_value('2025')
        page.locator('#cancel').click()
        page.locator('[data-tab=matrix]').click()
        page.locator('#category-filter').select_option('舒适/功能')
        expect(page.locator('#content select[data-v]')).to_have_value('标配')
        page.locator('[data-view=options]').click()
        page.get_by_role('button',name='编辑',exact=True).click()
        expect(page.locator('[name=category]')).to_have_value('舒适/功能')
        page.locator('#cancel').click()
        page.locator('[data-tab=models]').click()
        page.get_by_role('row').filter(has_text='2026').get_by_role('button',name='复制年款').click()
        page.locator('[name=year]').fill('2027')
        page.locator('#confirm').click()
        page.locator('#save').click()
        expect(page.locator('#notice')).to_have_text('草稿已保存')
        page.reload()
        ready(page)
        page.locator('#publish').click()
        page.get_by_role('button',name='确认发布').click()
        ready(page)
        current = store.get()['published']
        assert current['revision'] == 2
        result = current['data']
        for table,rows in original.items():
            assert all(row in result[table] for row in rows)
        new_model = next(r['id'] for r in result['models'] if r['year']=='2027')
        version,option,phase = [next(r for r in result[t] if r['model_id']==new_model) for t in ('versions','options','phases')]
        assert phase['sale_date'] == phase['presale_date'] == ''
        assert len(result['matrix']) == 2
        assert any((r['version_id'],r['option_id'],r['state'])==(version['id'],option['id'],'标配') for r in result['matrix'])
        page.locator('[data-tab=aliases]').click()
        page.locator('.panel-head [data-action="add:aliases"]').click()
        page.locator('[name=source]').fill('order')
        page.locator('[name=name]').fill('STRASSE')
        page.locator('[name=target_id]').select_option('m1')
        page.locator('#confirm').click()
        expect(page.locator('#dialog-error')).to_contain_text('名称已存在')
        page.locator('#cancel').click()
        page.locator('[data-tab=history]').click()
        page.get_by_role('button',name='运行路径').click()
        expect(page.locator('#dialog-body')).to_contain_text(str(store.root.resolve()))
        expect(page.locator('#dialog-body')).to_contain_text('shared_config.py')
        page.locator('#cancel').click()
        browser.close()
    print('Imported IDs, category edits, copy, save, refresh and publication passed.')


if __name__=='__main__':main()
