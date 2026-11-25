"""Exercise in-place editing, safe dismissal and context-aware actions with synthetic data."""
import tempfile

from playwright.sync_api import expect, sync_playwright

from browser_bulk_check import seed
from browser_rules_check import isolated_server, ready, switch_view


def test_editor(browser):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory) as (url, store, rules, _):
        page = browser.new_page(viewport={"width": 1120, "height": 820})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        ready(page)
        expect(page.locator("#publish")).to_be_disabled()
        page.locator("[data-tab=matrix]").click()
        page.get_by_role("button", name="新增车型档案").click()
        expect(page.locator("[name=brand]")).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()

        data = seed()
        for i in range(20):
            data["versions"].append(dict(data["versions"][0], id=f"extra-{i}", name=f"版本 {i}"))
        store.save(data, 0, "验收", publish=True)
        page.reload()
        ready(page)
        expect(page.locator("#apply-status")).to_be_hidden()
        expect(page.locator("#select-all")).to_be_visible()
        first = page.locator('[data-select="m1"]')
        first.focus()
        first.press("Space")
        expect(first).to_be_focused()
        expect(page.locator("#selection-count")).to_have_text("已选 1 项")
        expect(page.locator("#apply-status")).to_be_disabled()
        page.locator("#batch-status").select_option("在售")
        expect(page.locator("#apply-status")).to_be_enabled()
        first.uncheck()
        expect(page.locator("#batch-status")).to_be_hidden()

        page.locator("[data-tab=matrix]").click()
        cell = page.locator('[data-v="extra-18"][data-o="o1"]')
        cell.scroll_into_view_if_needed()
        cell.focus()
        scroll = page.locator(".matrix-scroll")
        position = scroll.evaluate("node => node.scrollLeft")
        assert position > 0
        cell.select_option("选配")
        expect(cell).to_be_focused()
        expect(page.locator("#publish")).to_be_enabled()
        assert scroll.evaluate("node => node.scrollLeft") == position
        cell.select_option("未配置")
        expect(page.locator("#save")).to_be_disabled()
        expect(page.locator("#publish")).to_be_disabled()
        cell.press("Control+z")
        expect(cell).to_have_value("选配")
        expect(cell).to_be_focused()
        assert scroll.evaluate("node => node.scrollLeft") == position
        cell.press("Control+Shift+z")
        expect(cell).to_have_value("未配置")
        expect(page.locator("#save")).to_be_disabled()

        page.locator('[data-select="o1"]').check()
        page.locator("#batch-matrix").click()
        page.locator("#batch-state").select_option("未配置")
        expect(page.locator("#confirm")).to_be_disabled()
        page.locator("#cancel").click()

        page.locator("[data-view=versions]").click()
        batch = page.locator('[data-action="batch-add:versions"]')
        batch.click()
        expect(page.locator("[name=model_id]")).to_have_value("")
        expect(page.locator("[name=model_id]")).to_be_focused()
        names = page.locator("[name=names]")
        names.press_sequentially("draft")
        names.press("Control+z")
        expect(names).to_have_value("")
        names.fill("未完成的版本")
        names.press("Escape")
        expect(page.get_by_role("button", name="继续编辑")).to_be_focused()
        page.get_by_role("button", name="继续编辑").click()
        expect(names).to_have_value("未完成的版本")
        expect(names).to_be_focused()
        page.locator("#close").click()
        page.get_by_role("button", name="放弃输入").click()
        expect(batch).to_be_focused()
        expect(page.locator("#save")).to_be_disabled()
        page.locator("#model").select_option("m2")
        batch.click()
        expect(page.locator("[name=model_id]")).to_have_value("m2")
        names.fill("第二辆车的版本")
        page.locator("#confirm").click()
        ready(page)
        expect(batch).to_be_focused()
        expect(page.locator("#content")).to_contain_text("第二辆车的版本")

        switch_view(page, "aliases", "gen_names")
        expect(page.locator("#publish")).to_be_disabled()
        page.locator('[data-rule-action="sync"]').click()
        ready(page)
        expect(page.locator("#dialog")).to_be_hidden()
        expect(page.locator("#notice")).to_contain_text("已与脚本一致")
        page.locator('[data-rule-action="edit"]').click()
        target = page.locator('[name="target"]')
        original = target.input_value()
        target.fill("规则临时修改")
        page.locator("#cancel").click()
        page.get_by_role("button", name="继续编辑").click()
        target.fill(original)
        page.locator("#close").click()
        expect(page.locator("#dialog")).to_be_hidden()
        expect(page.locator("#save")).to_be_disabled()
        expect(page.locator("#status")).to_contain_text("车型配置有未保存修改")

        switch_view(page, "matrix", "battery_rules")
        page.set_viewport_size({"width": 420, "height": 780})
        down = page.locator('[data-rule-action="down"]').first
        rule_id = down.get_attribute("data-rule-id")
        up = page.locator(f'[data-rule-id="{rule_id}"][data-rule-action="up"]')
        rule_scroll = page.locator("#content .scroll")
        down.scroll_into_view_if_needed()
        down.focus()
        rule_position = rule_scroll.evaluate("node => node.scrollLeft")
        assert rule_position > 0
        down.press("Enter")
        expect(up).to_be_focused()
        assert rule_scroll.evaluate("node => node.scrollLeft") == rule_position
        up.press("Enter")
        expect(down).to_be_focused()
        expect(page.locator("#save")).to_be_disabled()
        page.set_viewport_size({"width": 1120, "height": 820})
        page.locator("#search").fill("长续航")
        expect(page.locator("#content tbody tr")).to_have_count(1)
        expect(page.locator('[data-rule-action="up"],[data-rule-action="down"]')).to_have_count(0)
        page.locator("#search").fill("")
        extra = rules.get()["draft"]
        for i in range(40):
            extra["data"]["battery_rules"].append(dict(id=f"battery-{i}", generation=[f"代际{i}"], version=[], sku=[], capacity="80度"))
        rules.save(extra["data"], extra["revision"], "验收")
        page.locator("#reload").click()
        ready(page)
        boundary = page.locator('[data-rule-id="battery-37"][data-rule-action="down"]')
        boundary.press("Enter")
        expect(page.locator("#pagination")).to_contain_text("第 2 / 2 页")
        expect(boundary).to_be_focused()
        page.locator('[data-rule-id="battery-37"][data-rule-action="up"]').press("Enter")
        expect(page.locator("#pagination")).to_contain_text("第 1 / 2 页")
        assert not errors, errors
        page.close()


def test_readonly(browser):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory, readonly=True) as (url, store, _, _):
        store.save(seed(), 0, "验收", publish=True)
        page = browser.new_page()
        page.goto(url)
        ready(page)
        expect(page.locator("#status")).to_contain_text("只读")
        expect(page.locator("#bulkbar")).to_be_hidden()
        expect(page.locator("[data-edit], [data-select], [data-action]")).to_have_count(0)
        switch_view(page, "matrix", "option_prices")
        expect(page.locator("#content")).to_contain_text("天窗")
        expect(page.locator("[data-rule-action]")).to_have_count(0)
        expect(page.locator("#export")).to_be_enabled()
        page.close()


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        test_editor(browser)
        test_readonly(browser)
        browser.close()
    print("Workflow UX passed: empty states, bulk selection, matrix focus and scroll, discard protection, model scope, rules and readonly.")
