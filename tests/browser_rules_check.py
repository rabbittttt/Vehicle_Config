"""Isolated browser acceptance for Python-backed business mapping rules.

All input scripts, catalog data, and rules history belong to TemporaryDirectory.
This check must never discover or update the workspace's real shared_config.py.
"""
import ast
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campaign_store import CampaignStore
from catalog import Store
from server import make_server
from shared_rules import RuleStore


SOURCE = '''# -*- coding: utf-8 -*-
"""Synthetic browser fixture; no business data."""
import re

UNMANAGED = {"preserve": "原有内容"}

GEN_DISPLAY_NAME_MAP = {"车型甲旧名": "车型甲 2026款"}
VERSION_NAME_MAP = {"标准旧名": "标准版"}
SKU_NAME_MAP = {"旧SKU甲": "SKU甲"}
MERGE_GROUPS = {"车型甲&乙 2026款": ["车型甲 2026款", "车型乙 2026款"]}
BATTERY_RULES = [
    (["车型甲", "2026"], ["纯电", "长续航"], ["大电池"], "80度"),
    (["车型甲", "2026"], ["纯电"], [], "60度"),
]
MERGE_OPTIONS = {"旧选配名称": "天窗"}
OPTION_CONFIG = {
    "车型甲 2026款": {
        "舒适/功能选配": [
            ("天窗", 3000, False, {"versions": ["长续航"], "match_versions": ["长续航版"], "seat": "五座"}),
        ],
    },
}

# Keep this comment and function intact when publishing managed assignments.
def untouched(value):
    return re.sub(r"\\s+", " ", value).strip()
'''


def assignments(path):
    """Read literal fixture assignments without executing the Python source."""
    result = {}
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            result[node.targets[0].id] = ast.literal_eval(node.value)
    return result


@contextmanager
def isolated_server(directory, *, readonly=False, source=None):
    root = Path(directory)
    source = source or root / 'source' / 'shared_config.py'
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(SOURCE, encoding='utf-8')
    store = Store(root / 'catalog')
    rules = RuleStore(store.root, source)
    server = make_server(store, port=0, readonly=readonly, rules=rules)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/vehicle-config/', store, rules, source
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        store.db.close()


def ready(page):
    expect(page.locator('body')).to_have_attribute('aria-busy', 'false')


def fill_owner(page):
    page.locator('#data-tools').click()
    page.get_by_label('维护人', exact=True).fill('浏览器验收')
    page.locator('#data-tools').click()


def switch_view(page, tab, view):
    page.locator(f'[data-tab="{tab}"]').click()
    page.locator(f'[data-view="{view}"]').click()
    ready(page)
    expect(page.locator(f'[data-tab="{tab}"]')).to_have_attribute('aria-current', 'page')
    expect(page.locator(f'[data-view="{view}"]')).to_have_attribute('aria-pressed', 'true')


def add_vehicle(page):
    page.locator('[data-campaign-action="add-stage"]').click()
    page.get_by_label('代际名', exact=True).fill('未保存车型甲')
    page.locator('#confirm').click()
    expect(page.locator('#dialog')).to_be_hidden()
    ready(page)


def check_conditional_prices(browser):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'shared_config.py'
        source.write_text(SOURCE.replace('"天窗", 3000', '"天窗", {"条件乙": 21, "条件甲": 12}'), encoding='utf-8')
        with isolated_server(tmp, source=source) as (url, _, rules, _):
            page = browser.new_page()
            page.goto(url)
            ready(page)
            fill_owner(page)
            switch_view(page, 'options', 'option_prices')
            content = page.locator('#content')
            page.locator('#search').fill('条件甲')
            expect(content).to_contain_text('条件乙 21 元 / 条件甲 12 元')
            edit = content.get_by_role('button', name='编辑', exact=True)
            edit.click()
            form = page.locator('#rule-form')
            mode = form.locator('[name=price_mode]')
            amounts = form.locator('[name=price_amount]')
            keys = form.locator('[name=price_condition]')
            expect(mode).to_have_value('conditional')
            form.locator('[name=price_mode]').select_option('fixed')
            form.locator('[name=price]').fill('99')
            mode.select_option('conditional')
            expect(amounts.first).to_have_value('21')
            page.locator('#confirm').click()
            ready(page)
            expect(page.locator('#save')).to_be_disabled()
            edit.click()
            form.locator('[data-add-default]').click()
            amounts.last.fill('0')
            form.locator('[data-add-price]').click()
            keys.last.fill('条件乙')
            amounts.last.fill('9')
            page.locator('#confirm').click()
            expect(page.locator('#dialog-error')).to_contain_text('价格条件重复')
            keys.last.fill('条件丙')
            page.locator('#cancel').click()
            page.get_by_role('button', name='继续编辑').click()
            expect(keys.last).to_have_value('条件丙')
            page.locator('#confirm').click()
            ready(page)
            page.locator('#publish').click()
            expect(page.locator('#dialog-body .diff')).to_contain_text('默认价 0 元 / 条件丙 9 元')
            page.locator('#confirm').click()
            ready(page)
            expected = [('条件乙', 21), ('条件甲', 12), ('default', 0), ('条件丙', 9)]
            assert list(rules.get()['published']['data']['option_prices'][0]['price'].items()) == expected
            script_prices = assignments(source)['OPTION_CONFIG']['车型甲 2026款']['舒适/功能选配'][0][1]
            assert list(script_prices.items()) == expected
            edit.click()
            form.locator('[data-price-row]').last.locator('[data-remove-price]').click()
            form.locator('[data-price-row]').last.locator('[data-remove-price]').click()
            expect(form.locator('[data-add-default]')).to_be_visible()
            page.locator('#confirm').click()
            ready(page)
            expect(content).not_to_contain_text('默认价')
            page.locator('#undo').click()
            expect(content).to_contain_text('默认价 0 元')
            page.close()


def main():
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url, store, rules, source), sync_playwright() as pw:
        campaign = CampaignStore(store.root)
        campaign.save(0, [dict(sheet='车型汇总', row=2, col=1, value='已保存车型乙 2027款')], '浏览器验收')
        initial_rules=rules.get()['draft']['data']
        with rules.db:
            rules._event('草稿','早期操作人',initial_rules,[])
            for _ in range(40):
                rules._event('草稿','后续操作人',initial_rules,[])
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        content = page.locator('#content')
        header = page.locator('header')

        page.goto(url + 'rules.html')
        expect(page).to_have_url(url)
        ready(page)
        fill_owner(page)
        heading = header.locator('.heading')
        title = heading.get_by_role('heading', level=1)
        expect(title).to_have_text('车型配置')
        primary_nav = header.locator('nav [data-tab]')
        expect(primary_nav).to_have_text(['车型与日程', '代际规则', '电池与选配', '小订首销填报', '变更记录'])
        expect(header.get_by_role('link', name='订单配置规则')).to_have_count(0)
        title_box = title.bounding_box()
        title_mid_y = title_box['y'] + title_box['height'] / 2
        nav_top = header.locator('nav').bounding_box()['y']
        for selector in ['#model', '#year', '#search']:
            control = heading.locator(selector)
            expect(control).to_be_visible()
            box = control.bounding_box()
            assert box['x'] >= title_box['x'] + title_box['width'] - 1, selector
            assert box['y'] <= title_mid_y <= box['y'] + box['height'], selector
            assert box['y'] + box['height'] <= nav_top + 1, selector
        expect(header.locator('#clear-filters')).to_be_hidden()

        add_vehicle(page)
        expect(content).to_contain_text('未保存车型甲')
        header.locator('#year').select_option('2027')
        expect(content).not_to_contain_text('未保存车型甲')
        expect(content).to_contain_text('已保存车型乙')
        header.locator('#clear-filters').click()
        header.locator('#model').select_option('已保存车型乙 2027款')
        expect(content).not_to_contain_text('未保存车型甲')
        header.locator('#clear-filters').click()
        header.locator('#search').fill('未保存车型甲')
        expect(content).not_to_contain_text('已保存车型乙')

        switch_view(page, 'generation', 'gen_names')
        expect(page).to_have_url(url)
        expect(page.locator('h1')).to_have_text('车型配置')
        expect(primary_nav).to_have_text(['车型与日程', '代际规则', '电池与选配', '小订首销填报', '变更记录'])
        for selector in ['#model', '#year', '#status-filter']:
            expect(header.locator(selector)).to_be_hidden()
        header.locator('#search').fill('不存在的旧名')
        expect(content).not_to_contain_text('车型甲旧名')
        header.locator('#search').fill('车型甲旧名')
        expect(content).to_contain_text('车型甲旧名')
        content.get_by_role('button', name='编辑', exact=True).click()
        page.locator('[name=target]').fill('车型甲标准名')
        page.locator('#confirm').click()
        ready(page)
        page.locator('#undo').click()
        expect(content.get_by_text('车型甲 2026款', exact=True)).to_be_visible()
        page.locator('#redo').click()
        expect(content.get_by_text('车型甲标准名', exact=True)).to_be_visible()

        # Each draft remains in its editor, and the shared save button writes only the active draft.
        page.locator('[data-tab="schedule"]').click()
        expect(header.locator('#search')).to_have_value('未保存车型甲')
        expect(content).to_contain_text('未保存车型甲')
        expect(page.locator('#save')).to_be_enabled()
        switch_view(page, 'generation', 'gen_names')
        expect(content).to_contain_text('车型甲标准名')
        page.locator('#save').click()
        ready(page)
        assert len(campaign.get()['sheets'][0]['rows']) == 2
        assert assignments(source)['GEN_DISPLAY_NAME_MAP']['车型甲旧名'] == '车型甲 2026款'
        page.locator('[data-tab="schedule"]').click()
        page.locator('#save').click()
        ready(page)
        assert campaign.get()['sheets'][0]['rows'][2][0] == '未保存车型甲'

        switch_view(page, 'options', 'option_names')
        header.locator('#search').fill('')
        expect(content).to_contain_text('旧选配名称')
        switch_view(page, 'generation', 'merge_groups')
        header.locator('#search').fill('')
        expect(content).to_contain_text('车型甲&乙 2026款')
        switch_view(page, 'options', 'battery_rules')
        content.get_by_role('button', name='下移', exact=True).first.click()
        switch_view(page, 'options', 'option_prices')
        content.get_by_role('button', name='编辑', exact=True).click()
        page.locator('[name=price]').fill('4500')
        page.locator('#confirm').click()
        ready(page)
        expect(page.locator('#publish')).to_have_text('发布并更新脚本')
        page.locator('#publish').click()
        expect(page.locator('.script-diff')).to_contain_text('4500')
        ready(page)
        page.locator('#confirm').click()
        ready(page)
        values = assignments(source)
        assert values['GEN_DISPLAY_NAME_MAP']['车型甲旧名'] == '车型甲标准名'
        assert values['BATTERY_RULES'][0][3] == '60度'
        assert values['OPTION_CONFIG']['车型甲 2026款']['舒适/功能选配'][0][1] == 4500
        assert values['VERSION_NAME_MAP'] == {'标准旧名': '标准版'}

        switch_view(page, 'history', 'rule_history')
        expect(content.get_by_role('button', name='差异与恢复').first).to_be_visible()
        page.get_by_role('button',name='下一页').click()
        expect(content).to_contain_text('早期操作人')
        header.locator('#search').fill('早期操作人')
        expect(page.locator('#count')).to_have_text('1 条记录')
        content.get_by_role('button',name='差异与恢复').click()
        expect(page.locator('#dialog')).to_be_visible()
        page.locator('#cancel').click()
        header.locator('#search').fill('')
        switch_view(page, 'options', 'option_prices')
        source.write_text(source.read_text(encoding='utf-8') + '\n# external\n', encoding='utf-8')
        content.get_by_role('button', name='编辑', exact=True).click()
        page.locator('[name=price]').fill('5000')
        page.locator('#confirm').click()
        ready(page)
        page.locator('#publish').click()
        ready(page)
        expect(page.locator('#dialog-body')).to_contain_text('源文件已被其他程序修改')
        expect(page.locator('#confirm')).to_be_hidden()
        assert assignments(source) == values
        page.locator('#cancel').click()

        page.set_viewport_size({'width': 390, 'height': 844})
        expect(header.locator('[data-tab=options]')).to_have_attribute('aria-current', 'page')
        assert header.locator('nav').evaluate('el => el.scrollWidth > el.clientWidth')
        expect(header.locator('#search')).to_be_hidden()
        header.get_by_role('button', name='筛选', exact=True).click()
        expect(header.locator('#search')).to_be_visible()
        expect(page.locator('[data-view="option_prices"]')).to_be_in_viewport(ratio=0.9)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile overflow'
        out = ROOT / 'artifacts'
        out.mkdir(exist_ok=True)
        page.screenshot(path=str(out / 'rules-mobile.png'), full_page=True)
        header.locator('[data-tab=schedule]').click()
        header.locator('#clear-filters').click()
        expect(header.locator('#year')).to_be_in_viewport(ratio=1)
        header.locator('#year').select_option('2027')
        expect(content).to_contain_text('已保存车型乙')
        header.get_by_role('button', name='筛选 · 1', exact=True).click()
        expect(header.locator('#year')).to_be_hidden()
        header.locator('[data-tab=generation]').click()
        expect(page.locator('#view-tabs [aria-pressed="true"]')).to_have_text('代际显示名称')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(header.locator('[data-tab="generation"]')).to_have_attribute('aria-current', 'page')
        assert not errors, errors
        check_conditional_prices(browser)
        browser.close()
    print('Rules browser checks passed: shared navigation, header filters, isolated drafts, publishing, history, and external change conflicts.')


if __name__ == '__main__':
    main()
