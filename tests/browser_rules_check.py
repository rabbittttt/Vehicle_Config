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


def switch_view(page, tab, view=None):
    page.locator(f'[data-tab="{tab}"]').click()
    if view:
        page.locator(f'[data-view="{view}"]').click()
    ready(page)
    expect(page.locator(f'[data-tab="{tab}"]')).to_have_attribute('aria-current', 'page')
    if view:
        expect(page.locator(f'[data-view="{view}"]')).to_have_attribute('aria-pressed', 'true')
    else:
        expect(page.locator('#view-tabs')).to_be_hidden()


def add_vehicle(page):
    page.locator('[data-campaign-action="add-stage"]').click()
    page.get_by_label('代际名', exact=True).fill('未保存车型甲')
    page.locator('#confirm').click()
    expect(page.locator('#dialog')).to_be_hidden()
    ready(page)


def expect_rule_match(content, view, value):
    if view == 'option_prices':
        expect(content.locator('.price-name').first).to_have_value(value)
    else:
        expect(content).to_contain_text(value)


def check_grouped_price_entry(browser):
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url, _, rules, source):
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.emulate_media(reduced_motion='reduce')
        page.goto(url)
        ready(page)
        fill_owner(page)
        switch_view(page, 'options', 'option_prices')
        model, year = page.locator('#model'), page.locator('#year')
        model.select_option('车型甲 2026款')
        year.select_option('2026')
        content = page.locator('#content')
        groups = content.locator('.price-group')
        original = rules.get()['draft']['data']['option_prices'][0]
        expect(groups).to_have_count(1)
        expect(groups.locator('.price-group-toggle')).to_contain_text('舒适/功能选配')
        expect(groups.locator('tbody input[value="车型甲 2026款"], tbody input[value="舒适/功能选配"]')).to_have_count(0)

        groups.get_by_role('button', name='参数', exact=True).click()
        form = page.locator('#rule-form')
        expect(form.locator('[name=default]')).to_have_value('false')
        expect(form.locator('[name=versions]')).to_have_value('长续航')
        expect(form.locator('[name=match_versions]')).to_have_value('长续航版')
        expect(form.locator('[name=seat]')).to_have_value('五座')
        expect(form.locator('[name=price_mode]')).to_have_value('fixed')
        page.locator('#cancel').click()

        # Only the category and model context are shared. Changing an amount preserves all restrictions.
        groups.locator('.price-amount').fill('3200')
        groups.locator('.price-amount').press('Tab')
        groups.locator('.price-add').click()
        new_row = groups.locator('.price-new')
        name = new_row.locator('.price-name')
        amount = new_row.locator('.price-amount')
        for label, value in [('座椅通风', '1800'), ('免费踏板', '0')]:
            expect(name).to_be_focused()
            name.fill(label)
            name.press('Tab')
            expect(amount).to_be_focused()
            amount.fill(value)
            amount.press('Enter')
        expect(name).to_be_focused()
        expect(name).to_have_value('')
        page.locator('#save').click()
        expect(page.locator('#save')).to_be_disabled()
        saved = rules.get()['draft']['data']['option_prices']
        assert saved[0] == dict(original, price=3200)
        assert [(r['generation'], r['category'], r['name'], r['price']) for r in saved[1:]] == [
            ('车型甲 2026款', '舒适/功能选配', '座椅通风', 1800),
            ('车型甲 2026款', '舒适/功能选配', '免费踏板', 0),
        ]

        page.locator('[data-price-category]').click()
        page.locator('#dialog [name=category]').fill('外观选配')
        page.locator('#confirm').click()
        expect(name).to_be_focused()
        expect(groups).to_have_count(2)
        out = ROOT / 'artifacts'
        out.mkdir(exist_ok=True)
        for width, height in [(1440, 900), (390, 844), (320, 740)]:
            page.set_viewport_size({'width': width, 'height': height})
            expect(groups.locator('.price-amount').first).to_be_visible()
            expect(groups.locator('.price-add').last).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), width
            page.screenshot(path=str(out / f'price-entry-{width}.png'), full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 900})

        # A second vehicle may have the same category name and no prices yet.
        model.select_option('车型乙 2026款')
        expect(groups).to_have_count(0)
        page.locator('[data-price-category]').click()
        expect(page.locator('#dialog [name=generation]:visible')).to_have_count(0)
        page.locator('#dialog [name=category]').fill('舒适/功能选配')
        page.locator('#confirm').click()
        expect(name).to_be_focused()
        name.fill('座椅通风')
        page.locator('#save').click()
        expect(page.locator('#error-notice')).to_contain_text('金额')
        assert rules.get()['draft']['data']['option_prices'] == saved
        expect(name).to_have_value('座椅通风')
        amount.fill('1200')
        page.locator('#save').click()
        expect(page.locator('#save')).to_be_disabled()
        rows = rules.get()['draft']['data']['option_prices']
        assert len(rows) == 4
        assert rows[:3] == saved
        assert (rows[-1]['generation'], rows[-1]['category'], rows[-1]['price']) == ('车型乙 2026款', '舒适/功能选配', 1200)
        assert assignments(source)['OPTION_CONFIG']['车型甲 2026款']['舒适/功能选配'][0][1] == 3000

        page.reload()
        ready(page)
        switch_view(page, 'options', 'option_prices')
        model.select_option('车型乙 2026款')
        expect(groups).to_have_count(1)
        expect(groups.locator('.price-name')).to_have_value('座椅通风')
        expect(groups.locator('.price-amount')).to_have_value('1200')
        groups.locator('.price-add').click()
        name.fill('轮毂')
        new_row.get_by_role('button', name='参数', exact=True).click()
        form = page.locator('#rule-form')
        expect(form.locator('[name=name]')).to_have_value('轮毂')
        expect(form.locator('[name=price_mode]')).to_have_value('conditional')
        form.locator('[name=price_condition]').fill('长续航')
        form.locator('[name=price_amount]').fill('2100')
        page.locator('#confirm').click()
        expect(page.locator('#dialog')).to_be_hidden()
        expect(groups.get_by_role('button', name='编辑条件价')).to_contain_text('长续航 2100 元')
        page.locator('#save').click()
        expect(page.locator('#save')).to_be_disabled()
        last = rules.get()['draft']['data']['option_prices'][-1]
        assert (last['generation'], last['category'], last['price']) == ('车型乙 2026款', '舒适/功能选配', {'长续航': 2100})
        page.close()


def check_conditional_prices(browser):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / 'shared_config.py'
        source.write_text(SOURCE.replace('"天窗", 3000, False', '"天窗", {"条件乙": 21, "条件甲": 12}, True'), encoding='utf-8')
        with isolated_server(tmp, source=source) as (url, store, rules, _):
            CampaignStore(store.root).save(0, [dict(sheet='车型汇总', row=2, col=1, value='车型甲 2026款')], '测试', publish=True)
            page = browser.new_page()
            page.goto(url)
            ready(page)
            fill_owner(page)
            page.locator('#model').select_option('车型甲 2026款')
            content = page.locator('#content')
            for group, view, match in [('generation', 'gen_names', '车型甲旧名'), ('generation', 'merge_groups', '车型甲&乙'),
                                       ('options', 'battery_rules', '80度'), ('options', 'option_prices', '天窗')]:
                switch_view(page, group, view)
                expect_rule_match(content, view, match)
            page.locator('#search').fill('条件甲')
            expect(content).to_contain_text('条件乙 21 元 / 条件甲 12 元')
            edit = content.get_by_role('button', name='编辑条件价', exact=True)
            edit.click()
            form = page.locator('#rule-form')
            mode = form.locator('[name=price_mode]')
            amounts = form.locator('[name=price_amount]')
            keys = form.locator('[name=price_condition]')
            expect(form.locator('[name=default]')).to_have_value('true')
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
            expect(page.locator('#dialog-body')).not_to_contain_text('车型与日程、小订首销填报')
            page.get_by_role('button', name='确认发布', exact=True).click()
            expect(page.locator('#dialog')).to_be_hidden()
            ready(page)
            expected = [('条件乙', 21), ('条件甲', 12), ('default', 0), ('条件丙', 9)]
            published = rules.get()['published']['data']['option_prices'][0]
            assert list(published['price'].items()) == expected
            assert published['default'] is True
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
        campaign.save(0, [dict(sheet='车型汇总', row=2, col=1, value='已保存车型乙 2027款'),
                          dict(sheet='车型汇总', row=2, col=2, value='焕新 2028款')], '首发维护人', publish=True)
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
        title = header.get_by_role('heading', level=1)
        expect(title).to_have_text('车型配置')
        primary_nav = header.locator('nav [data-tab]')
        nav_labels = ['车型与日程', '小订首销填报', '代际规则', '电池与选配', '变更记录']
        expect(primary_nav).to_have_text(nav_labels)
        expect(header.get_by_role('link', name='订单配置规则')).to_have_count(0)
        title_box = title.bounding_box()
        title_mid_y = title_box['y'] + title_box['height'] / 2
        nav_top = header.locator('nav').bounding_box()['y']
        search = header.get_by_role('searchbox')
        clear_search = header.get_by_role('button', name='清空搜索', exact=True)
        model = header.locator('#catalog-filters #model')
        year = header.locator('#catalog-filters #year')
        expect(page.locator('#schedule-filters, #clear-schedule-filters')).to_have_count(0)
        expect(search).to_be_visible()
        search_x = search.bounding_box()['x']
        expect(model).to_be_visible()
        model_x = model.bounding_box()['x']
        expect(year).to_be_visible()
        year_x = year.bounding_box()['x']
        assert search_x > page.viewport_size['width'] / 2
        for tab in ['schedule', 'generation', 'options', 'reporting', 'history']:
            header.locator(f'[data-tab="{tab}"]').click()
            ready(page)
            expect(search).to_be_visible()
            expect(model).to_be_visible()
            assert abs(model.bounding_box()['x'] - model_x) <= 1, tab
            expect(year).to_be_visible()
            assert abs(year.bounding_box()['x'] - year_x) <= 1, tab
            expect(clear_search).to_be_visible()
            expect(clear_search).to_be_disabled()
            box = search.bounding_box()
            assert abs(box['x'] - search_x) <= 1, tab
            assert box['y'] <= title_mid_y <= box['y'] + box['height'], tab
            assert box['y'] + box['height'] <= nav_top + 1, tab
        header.locator('[data-tab=schedule]').click()
        ready(page)

        add_vehicle(page)
        expect(content).to_contain_text('未保存车型甲')
        year.select_option('2028')
        expect(content).not_to_contain_text('未保存车型甲')
        expect(content).to_contain_text('已保存车型乙')
        for report in ['dailyOrders', 'businessTotals']:
            switch_view(page, 'reporting', report)
            expect(year).to_have_value('2028')
            expect(content.locator('tbody tr')).to_have_count(1)
            expect(content).to_contain_text('已保存车型乙')
            expect(content).not_to_contain_text('未保存车型甲')
        for group, view, match in [('generation', 'gen_names', '车型甲旧名'), ('generation', 'merge_groups', '车型甲&乙'),
                                   ('options', 'battery_rules', '80度'), ('options', 'option_prices', '天窗')]:
            switch_view(page, group, view)
            expect(year).to_have_value('2028')
            expect(content.locator('tbody tr')).to_have_count(0)
            year.select_option('2026')
            expect_rule_match(content, view, match)
            year.select_option('2028')
        switch_view(page, 'options', 'option_names')
        expect(content).to_contain_text('旧选配名称')
        switch_view(page, 'history')
        expect(year).to_have_value('2028')
        for actor, source_name in [('首发维护人', '日程与填报'), ('后续操作人', '脚本规则')]:
            search.fill(actor)
            row = content.get_by_role('row').filter(has_text=actor).first
            expect(row.get_by_role('cell', name=source_name)).to_be_visible()
        search.fill('')
        header.locator('[data-tab=schedule]').click()
        ready(page)
        expect(year).to_have_value('2028')
        year.select_option('2027')
        expect(content).not_to_contain_text('未保存车型甲')
        expect(clear_search).to_be_disabled()
        model.select_option('已保存车型乙 2027款')
        expect(content).not_to_contain_text('未保存车型甲')
        search.fill('未保存车型甲')
        expect(content).not_to_contain_text('已保存车型乙')
        clear_search.click()
        expect(model).to_have_value('已保存车型乙 2027款')
        expect(year).to_have_value('2027')
        expect(content).to_contain_text('已保存车型乙')
        expect(content).not_to_contain_text('未保存车型甲')
        search.fill('未保存车型甲')
        year.select_option(label='全部年款')
        expect(year).to_have_value('')
        expect(model).to_have_value('已保存车型乙 2027款')
        expect(search).to_have_value('未保存车型甲')
        expect(content.locator('tbody tr')).to_have_count(0)
        for report in ['dailyOrders', 'businessTotals']:
            switch_view(page, 'reporting', report)
            expect(model).to_have_value('已保存车型乙 2027款')
            expect(year).to_have_value('')
            expect(content.locator('tbody tr')).to_have_count(1)
            expect(content).to_contain_text('已保存车型乙')
            expect(content).not_to_contain_text('未保存车型甲')
        for group, scoped_view in [('generation', 'gen_names'), ('generation', 'merge_groups'),
                                   ('options', 'battery_rules'), ('options', 'option_prices')]:
            switch_view(page, group, scoped_view)
            expect(model).to_have_value('已保存车型乙 2027款')
            expect(content.locator('tbody tr')).to_have_count(0)
        switch_view(page, 'options', 'option_names')
        expect(content).to_contain_text('旧选配名称')
        model.select_option('未保存车型甲')
        switch_view(page, 'history')
        expect(model).to_have_value('未保存车型甲')
        expect(content).to_contain_text('后续操作人')
        model.select_option('已保存车型乙 2027款')
        header.locator('[data-tab=schedule]').click()
        expect(search).to_have_value('未保存车型甲')
        expect(model).to_have_value('已保存车型乙 2027款')
        model.select_option('')
        expect(content).to_contain_text('未保存车型甲')
        expect(content).not_to_contain_text('已保存车型乙')

        switch_view(page, 'generation', 'gen_names')
        expect(page).to_have_url(url)
        expect(page.locator('h1')).to_have_text('车型配置')
        expect(primary_nav).to_have_text(nav_labels)
        expect(year).to_be_visible()
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
        content.locator('.price-amount').fill('4500')
        content.locator('.price-amount').press('Tab')
        ready(page)
        page.get_by_role('button', name='发布', exact=True).click()
        for group in ('车型与日程、小订首销填报', '代际规则、电池与选配'):
            expect(page.locator('#dialog-body')).to_contain_text(group)
        expect(page.locator('.script-diff')).to_contain_text('4500')
        ready(page)
        page.get_by_role('button', name='确认发布', exact=True).click()
        expect(page.locator('#dialog')).to_be_hidden()
        ready(page)
        expect(page.locator('#publish')).to_be_disabled()
        assert campaign.published()['sheets'][0]['rows'][2][0] == '未保存车型甲'
        values = assignments(source)
        assert values['GEN_DISPLAY_NAME_MAP']['车型甲旧名'] == '车型甲标准名'
        assert values['BATTERY_RULES'][0][3] == '60度'
        assert values['OPTION_CONFIG']['车型甲 2026款']['舒适/功能选配'][0][1] == 4500
        assert values['VERSION_NAME_MAP'] == {'标准旧名': '标准版'}

        switch_view(page, 'history')
        expect(content.get_by_role('button', name='查看差异').first).to_be_visible()
        page.get_by_role('button', name='下一页').click()
        expect(content).to_contain_text('早期操作人')
        search.fill('首发维护人')
        expect(page.locator('#count')).to_have_text('1 条记录')
        campaign_record = content.get_by_role('row').filter(has_text='首发维护人')
        expect(campaign_record).to_contain_text('日程与填报')
        expect(campaign_record.get_by_role('button')).to_have_count(0)
        search.fill('早期操作人')
        expect(page.locator('#count')).to_have_text('1 条记录')
        historical_rule = content.get_by_role('row').filter(has_text='早期操作人')
        historical_rule.get_by_role('button', name='查看差异').click()
        expect(page.locator('#dialog')).to_be_visible()
        page.locator('#dialog').get_by_role('button', name='恢复到编辑区').click()
        expect(page.locator('#dialog')).to_be_hidden()
        expect(header.locator('[data-tab=generation]')).to_have_attribute('aria-current', 'page')
        expect(page.locator('#save')).to_be_enabled()
        assert assignments(source) == values
        page.locator('#undo').click()
        expect(page.locator('#save')).to_be_disabled()
        switch_view(page, 'options', 'option_prices')
        source.write_text(source.read_text(encoding='utf-8') + '\n# external\n', encoding='utf-8')
        content.locator('.price-amount').fill('5000')
        content.locator('.price-amount').press('Tab')
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
        expect(model).to_be_hidden()
        expect(year).to_be_hidden()
        header.get_by_role('button', name='筛选', exact=True).click()
        expect(header.locator('#search')).to_be_visible()
        expect(model).to_be_in_viewport(ratio=1)
        expect(page.locator('[data-view="option_prices"]')).to_be_in_viewport(ratio=0.9)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile overflow'
        out = ROOT / 'artifacts'
        out.mkdir(exist_ok=True)
        page.screenshot(path=str(out / 'rules-mobile.png'), full_page=True)
        header.locator('[data-tab=schedule]').click()
        clear_search.click()
        expect(year).to_be_in_viewport(ratio=1)
        year.select_option('2027')
        expect(content).to_contain_text('已保存车型乙')
        header.get_by_role('button', name='筛选 · 1', exact=True).click()
        expect(search).to_be_hidden()
        expect(model).to_be_hidden()
        expect(year).to_be_hidden()
        header.get_by_role('button', name='筛选 · 1', exact=True).click()
        search.fill('已保存车型乙')
        model.select_option('已保存车型乙 2027款')
        header.get_by_role('button', name='筛选 · 3', exact=True).click()
        expect(search).to_be_hidden()
        header.get_by_role('button', name='筛选 · 3', exact=True).click()
        expect(year).to_have_value('2027')
        year.select_option(label='全部年款')
        expect(model).to_have_value('已保存车型乙 2027款')
        expect(search).to_have_value('已保存车型乙')
        expect(content).to_contain_text('已保存车型乙')
        expect(content).not_to_contain_text('未保存车型甲')
        header.locator('[data-tab=generation]').click()
        expect(page.locator('#view-tabs [aria-pressed="true"]')).to_have_text('代际显示名称')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(header.locator('[data-tab="generation"]')).to_have_attribute('aria-current', 'page')
        assert not errors, errors
        check_grouped_price_entry(browser)
        check_conditional_prices(browser)
        browser.close()
    print('Rules browser checks passed: shared navigation, header filters, isolated drafts, publishing, history, and external change conflicts.')


if __name__ == '__main__':
    main()
