"""Isolated browser acceptance for Python-backed business mapping rules.

All input scripts, catalog data, and rules history belong to TemporaryDirectory.
This check must never discover or update the workspace's real shared_config.py.
"""
import ast
import json
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def main():
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url,store,rules,source), sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(url+'rules.html');ready(page)
        page.get_by_role('button',name='代际显示名称',exact=True).click()
        page.get_by_role('button',name='编辑',exact=True).click()
        page.locator('[name=target]').fill('车型甲标准名')
        page.locator('#confirm').click();ready(page)
        page.locator('#save').click();ready(page)
        assert assignments(source)['GEN_DISPLAY_NAME_MAP']['车型甲旧名']=='车型甲 2026款'
        page.get_by_role('button',name='电池包规则',exact=True).click()
        page.get_by_role('button',name='下移',exact=True).first.click()
        page.get_by_role('button',name='选配金',exact=True).click()
        page.get_by_role('button',name='编辑',exact=True).click();page.locator('[name=price]').fill('4500');page.locator('#confirm').click();ready(page)
        page.locator('#publish').click();expect(page.locator('.script-diff')).to_contain_text('4500');ready(page)
        page.locator('#confirm').click();ready(page)
        values=assignments(source)
        assert values['GEN_DISPLAY_NAME_MAP']['车型甲旧名']=='车型甲标准名'
        assert values['BATTERY_RULES'][0][3]=='60度'
        assert values['OPTION_CONFIG']['车型甲 2026款']['舒适/功能选配'][0][1]==4500
        assert values['VERSION_NAME_MAP']=={'标准旧名':'标准版'}
        source.write_text(source.read_text(encoding='utf-8')+'\n# external\n',encoding='utf-8')
        page.get_by_role('button',name='编辑',exact=True).click();page.locator('[name=price]').fill('5000');page.locator('#confirm').click();ready(page)
        page.locator('#publish').click();ready(page);expect(page.locator('#body')).to_contain_text('源文件已被其他程序修改');expect(page.locator('#confirm')).to_be_hidden()
        assert not errors,errors
        page.locator('#cancel').click();page.set_viewport_size({'width':390,'height':844})
        Path('artifacts').mkdir(exist_ok=True);page.screenshot(path='artifacts/rules-mobile.png',full_page=True)
        browser.close()
    print('Rules browser checks passed')


if __name__ == '__main__':
    main()
