"""Exercise in-place editing, safe dismissal and context-aware actions with synthetic data."""
import tempfile

from playwright.sync_api import expect, sync_playwright

from browser_campaign import check_draft_status_everywhere
from browser_rules_check import add_vehicle, fill_owner, isolated_server, ready, switch_view
from campaign_store import CampaignStore


def check_header_actions(page, tab):
    actions = page.locator('header .top-actions :is(#template, #import, #export, #data-tools):visible')
    expected = ['data-tools'] if tab == 'history' else ['template', 'import', 'export', 'data-tools']
    expect(actions).to_have_count(len(expected))
    assert actions.evaluate_all('buttons => buttons.map(button => button.id)') == expected
    boxes = [button.bounding_box() for button in actions.all()]
    center_y = boxes[0]['y'] + boxes[0]['height'] / 2
    assert all(box['y'] <= center_y <= box['y'] + box['height'] for box in boxes), tab
    assert all(left['x'] + left['width'] <= right['x'] + 1 for left, right in zip(boxes, boxes[1:])), tab
    assert boxes[0]['x'] >= 0 and boxes[-1]['x'] + boxes[-1]['width'] <= page.viewport_size['width'] + 1, tab
    if page.viewport_size['width'] > 700:
        nav = page.locator('header nav').bounding_box()
        assert nav['y'] <= center_y <= nav['y'] + nav['height'], tab
        assert nav['x'] + nav['width'] <= boxes[0]['x'] + 1, tab


def test_editor(browser):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory) as (url, store, rules, _):
        page = browser.new_page(viewport={"width": 1120, "height": 820})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        ready(page)
        expect(page.locator("#publish")).to_be_disabled()
        expect(page.locator("#status")).to_have_text("配置草稿 · 无待发布变更")
        add = page.locator('[data-campaign-action="add-stage"]')
        add.click()
        name = page.get_by_label("代际名", exact=True)
        expect(name).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#dialog")).to_be_hidden()
        expect(add).to_be_focused()
        add.click()
        name.fill("尚在填写的车型")
        name.press("Escape")
        expect(page.get_by_role("button", name="继续编辑")).to_be_focused()
        page.get_by_role("button", name="继续编辑").click()
        expect(name).to_have_value("尚在填写的车型")
        expect(name).to_be_focused()
        page.locator("#close").click()
        page.get_by_role("button", name="放弃输入").click()
        expect(add).to_be_focused()
        expect(page.locator("#save")).to_be_disabled()

        add_vehicle(page)
        expect(page.locator("#content")).to_contain_text("未保存车型甲")
        page.keyboard.press("Control+z")
        expect(page.locator("#content")).not_to_contain_text("未保存车型甲")
        page.keyboard.press("Control+Shift+z")
        expect(page.locator("#content")).to_contain_text("未保存车型甲")
        check_draft_status_everywhere(page, "配置草稿 · 有未保存修改")
        expect(page.locator("#export")).to_have_accessible_name("导出车型与日程（Excel）")
        expect(page.locator("#template")).to_be_enabled()
        expect(page.locator("#import")).to_be_visible()
        expect(page.locator("#import")).to_have_accessible_name("导入")
        expect(page.locator("#import")).to_have_attribute("title", "导入车型与日程（Excel）")
        switch_view(page, "reporting", "dailyOrders")
        expect(page.locator('[data-campaign-action=batch]')).to_be_visible()
        expect(page.locator('.report-matrix tr[data-model="未保存车型甲"] [data-campaign-action=report-add]')).to_be_visible()
        expect(page.locator("#export")).to_have_accessible_name("导出小订与首销（Excel）")
        excel_captions = page.locator("#template span, #import span, #export span")

        switch_view(page, "generation", "gen_names")
        expect(excel_captions).to_have_text(["模板", "导入", "导出"])
        expect(page.locator("#template")).to_have_accessible_name("下载脚本规则模板（Excel）")
        expect(page.locator("#import")).to_have_accessible_name("导入")
        expect(page.locator("#import")).to_have_attribute("title", "导入脚本规则（Excel）")
        expect(page.locator("#export")).to_have_accessible_name("导出脚本规则（Excel）")
        expect(page.locator("#publish")).to_be_enabled()
        page.locator("#data-tools").click()
        page.locator("#reload-rules").click()
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
        expect(page.locator("#status")).to_have_text("配置草稿 · 有未保存修改")

        switch_view(page, "options", "battery_rules")
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


def test_global_tools(browser):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory) as (url, store, rules, source):
        original = rules.get()["draft"]
        source.write_text(source.read_text(encoding="utf-8").replace("80度", "90度"), encoding="utf-8")
        script = source.read_bytes()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url)
        ready(page)
        fill_owner(page)
        tools = page.locator("#data-tools")
        panel = page.locator("#data-tools-panel")
        reload_rules = panel.locator("#reload-rules")
        # Start with an unsaved, filtered schedule. The rule editor has not been opened.
        add_vehicle(page)
        page.locator("#search").fill("未保存车型甲")
        invalid_script = script.replace(b', 3000, False', b', "TBD", False')
        assert invalid_script != script
        source.write_bytes(invalid_script)
        requests = []
        page.on("request", lambda req: requests.append((req.method, req.url.split("/api/")[-1]))
                if "/api/" in req.url else None)
        tools.click()
        reload_rules.click()
        dialog = page.locator("#dialog")
        expect(dialog).to_be_visible()
        for detail in ("脚本检查未通过", "本次未载入", "OPTION_CONFIG 第", "车型甲 2026款",
                       "舒适/功能选配", "天窗", "TBD", str(source.resolve())):
            expect(dialog).to_contain_text(detail)
        assert requests == [("GET", "rules/state")]
        expect(page.locator("#confirm")).to_be_hidden()
        page.locator("#cancel").click()
        expect(page.locator("#save")).to_be_enabled()
        assert rules.get()["draft"] == original
        assert source.read_bytes() == invalid_script

        # Retry the same action once the file on disk has a valid price.
        source.write_bytes(script)
        tools.click()
        expect(reload_rules).to_have_text("从脚本重新载入")
        reload_rules.click()
        expect(dialog).to_be_visible()
        for label in ("shared_config.py", "代际显示名称", "代际合并", "电池包规则", "选配名称合并", "选配金"):
            expect(dialog).to_contain_text(label)
        page.locator("#cancel").click()
        expect(dialog).to_be_hidden()
        expect(tools).to_be_focused()
        assert rules.get()["draft"] == original

        tools.click()
        reload_rules.click()
        page.get_by_role("button", name="替换规则并载入").click()
        expect(dialog).to_be_hidden()
        ready(page)
        expect(page.locator("[data-tab=schedule]")).to_have_attribute("aria-current", "page")
        expect(page.locator("#search")).to_have_value("未保存车型甲")
        expect(page.locator("#content")).to_contain_text("未保存车型甲")
        expect(page.locator("#save")).to_be_enabled()
        expect(page.locator("#undo")).to_be_enabled()
        assert len(CampaignStore(store.root).get()["sheets"][0]["rows"]) == 1
        assert rules.get()["draft"]["data"]["battery_rules"][0]["capacity"] == "90度"
        assert script == source.read_bytes()

        # An in-browser rule edit also needs confirmation when the script itself is unchanged.
        switch_view(page, "generation", "gen_names")
        page.locator('[data-rule-action="edit"]').click()
        page.locator('[name="target"]').fill("尚未保存的规则")
        page.locator("#confirm").click()
        ready(page)
        page.locator("[data-tab=schedule]").click()
        tools.click()
        reload_rules.click()
        expect(dialog).to_be_visible()
        page.locator("#cancel").click()
        switch_view(page, "generation", "gen_names")
        expect(page.locator("#content")).to_contain_text("尚未保存的规则")
        expect(page.locator("#save")).to_be_enabled()

        for tab in ("schedule", "generation", "options", "reporting", "history"):
            page.locator(f"[data-tab={tab}]").click()
            check_header_actions(page, tab)
            tools.focus()
            tools.press("Enter")
            expect(tools).to_have_attribute("aria-expanded", "true")
            expect(reload_rules).to_be_visible()
            expect(panel.get_by_role("button", name="运行路径")).to_be_visible()
            page.keyboard.press("Tab")
            assert panel.evaluate("el => el.contains(document.activeElement)")
            page.keyboard.press("Escape")
            expect(panel).to_be_hidden()
            expect(tools).to_be_focused()

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("[data-tab=reporting]").click()
        check_header_actions(page, "reporting")
        tools.click()
        expect(reload_rules).to_be_in_viewport(ratio=1)
        expect(panel.get_by_role("button", name="运行路径")).to_be_in_viewport(ratio=1)
        page.locator("h1").click()
        expect(panel).to_be_hidden()
        page.close()


def test_readonly(browser):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory, readonly=True) as (url, store, _, _):
        CampaignStore(store.root).save(0, [dict(sheet="车型汇总", row=2, col=1, value="已发布车型")], "验收", publish=True)
        page = browser.new_page()
        page.goto(url)
        ready(page)
        expect(page.locator("#status")).to_contain_text("只读")
        page.locator("#data-tools").click()
        expect(page.locator("#reload-rules")).to_be_hidden()
        page.get_by_role("button", name="运行路径").click()
        expect(page.locator("#dialog")).to_contain_text("shared_config.py")
        page.locator("#cancel").click()
        expect(page.locator("#content")).to_contain_text("已发布车型")
        expect(page.locator('[data-campaign-action="add-stage"]')).to_have_count(0)
        page.get_by_role("button", name="查看", exact=True).click()
        expect(page.locator("#dialog output").first).to_have_text("已发布车型")
        expect(page.locator("#confirm")).to_be_hidden()
        page.locator("#cancel").click()
        switch_view(page, "options", "option_prices")
        expect(page.locator("#content")).to_contain_text("天窗")
        expect(page.locator("[data-rule-action]")).to_have_count(0)
        expect(page.locator("#export")).to_be_enabled()
        page.close()


def test_publication_retry(browser, *, lose_campaign_response=False):
    with tempfile.TemporaryDirectory() as directory, isolated_server(directory) as (url, store, rules, source):
        campaign = CampaignStore(store.root)
        if lose_campaign_response:
            draft = rules.get()["draft"]
            draft["data"]["gen_names"][0]["target"] = "待发布代际"
            rules.save(draft["data"], draft["revision"], "测试维护人")

        page = browser.new_page()
        publications = []
        lost_response_status = []

        def publish_request(route):
            group = route.request.url.split("/api/")[1]
            publications.append(group)
            if lose_campaign_response and publications == ["campaign/publish"]:
                # Forward the real unsaved edits and discard only the response to the browser.
                response = route.fetch()
                lost_response_status.append(response.status)
                response.dispose()
                route.abort("failed")
            elif not lose_campaign_response and len(publications) == 2:
                route.fulfill(status=503, json={"error": "规则服务暂时不可用"})
            else:
                route.continue_()

        page.route("**/api/*/publish", publish_request)
        page.goto(url)
        ready(page)
        fill_owner(page)
        if lose_campaign_response:
            # The home page must include a saved rule draft before opening any rule view.
            expect(page.locator("#status")).to_be_visible()
            expect(page.locator("#status")).to_have_text("配置草稿 · 待发布")
        else:
            switch_view(page, "generation", "gen_names")
            page.locator('[data-rule-action="edit"]').click()
            page.locator('[name="target"]').fill("待发布代际")
            page.locator("#confirm").click()
            ready(page)
            check_draft_status_everywhere(page, "配置草稿 · 有未保存修改")
            switch_view(page, "generation", "gen_names")
            page.locator("#save").click()
            ready(page)
            check_draft_status_everywhere(page, "配置草稿 · 待发布")
        add_vehicle(page)
        switch_view(page, "history", "campaign_history")
        expect(page.locator("#status")).to_have_text("配置草稿 · 有未保存修改")
        publish = page.get_by_role("button", name="发布", exact=True)
        confirm = page.get_by_role("button", name="确认发布", exact=True)
        dialog = page.locator("#dialog")
        publish.click()
        for group in ("车型与日程、小订首销填报", "代际规则、电池与选配"):
            expect(dialog).to_contain_text(group)
        page.locator("#cancel").click()
        expect(dialog).to_be_hidden()
        assert publications == []
        assert campaign.published()["revision"] == 0
        assert rules.get()["published"]["data"]["gen_names"][0]["target"] != "待发布代际"

        original_source = source.read_bytes()
        source.write_bytes(original_source + b"\n# changed outside the workbench\n")
        publish.click()
        expect(dialog).to_contain_text("源文件已被其他程序修改")
        expect(confirm).to_be_hidden()
        page.locator("#cancel").click()
        assert publications == []
        assert campaign.published()["revision"] == 0
        source.write_bytes(original_source)

        publish.click()
        owner = dialog.get_by_label("本次维护人")
        expect(owner).to_have_count(1)
        owner.fill("统一发布维护人")
        confirm.click()
        if lose_campaign_response:
            expect(dialog).to_contain_text("代际规则、电池与选配尚未执行")
            assert lost_response_status == [200]
            first_attempt = ["campaign/publish"]
        else:
            expect(dialog).to_contain_text("规则服务暂时不可用")
            expect(dialog).to_contain_text("已发布")
            first_attempt = ["campaign/publish", "rules/publish"]
        assert publications == first_attempt
        assert campaign.published()["revision"] == 1
        assert campaign.published()["sheets"][0]["rows"][1][0] == "未保存车型甲"
        assert rules.get()["published"]["data"]["gen_names"][0]["target"] != "待发布代际"

        dialog.get_by_role("button", name="重新核对", exact=True).click()
        expect(dialog.get_by_role("heading", level=3)).to_have_text(["代际规则、电池与选配"])
        confirm.click()
        expect(dialog).to_be_hidden()
        expect(publish).to_be_disabled()
        check_draft_status_everywhere(page, "配置草稿 · 无待发布变更")
        assert publications == first_attempt + ["rules/publish"]
        assert campaign.published()["revision"] == 1
        assert rules.get()["published"]["data"]["gen_names"][0]["target"] == "待发布代际"
        assert campaign.history_page()["history"][0]["actor"] == "统一发布维护人"
        assert rules.history_page()["history"][0]["actor"] == "统一发布维护人"
        page.close()


if __name__ == "__main__":
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge")
        test_editor(browser)
        test_global_tools(browser)
        test_publication_retry(browser)
        test_publication_retry(browser, lose_campaign_response=True)
        test_readonly(browser)
        browser.close()
    print("Workflow UX passed: editor workflows, global rule reload scope, keyboard and mobile access, and readonly tools.")
