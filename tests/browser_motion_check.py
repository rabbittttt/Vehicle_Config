"""Measure report-matrix search, batch updates and dialogs on desktop and mobile."""
import json
import statistics
import tempfile

from playwright.sync_api import expect, sync_playwright

from browser_rules_check import ROOT, isolated_server, ready
from campaign_store import CampaignStore


MODELS = 32
DAYS = 28
FIRST_MODEL = '测量车型 00'


def seed_orders(store):
    edits = []
    for index in range(MODELS):
        model = f'测量车型 {index:02d}'
        edits.append(dict(sheet='车型汇总', row=index + 2, col=1, value=model))
        edits.append(dict(sheet='车型汇总', row=index + 2, col=9, value='2026-11-01'))
        for day in range(1, DAYS + 1):
            edits.append(dict(sheet='小订by天', model=model, date=f'2026-11-{day:02d}', value=day + index))
    CampaignStore(store.root).save(0, edits, '合成数据')


# Measure from input through the visible result and next frame, including deferred rendering.
SAMPLE = """async ({model, total}) => {
    const tick = () => new Promise(requestAnimationFrame);
    const search = document.querySelector('#search');
    const idle = [], intervals = [], searches = [];
    search.focus();
    let last = await tick();
    for (let i = 0; i < 36; i++) {
        const now = await tick();
        idle.push(now - last);
        last = now;
    }
    for (let i = 0; i < 36; i++) {
        const filtered = i % 2 === 0, start = performance.now();
        search.value = filtered ? model : '';
        search.dispatchEvent(new Event('input', {bubbles:true}));
        const expected = filtered ? 1 : total;
        let rendered = false;
        for (let n = 0; n < 12; n++) {
            const now = await tick();
            intervals.push(now - last);
            last = now;
            if (document.querySelectorAll('.report-matrix tbody tr[data-model]').length === expected) {
                rendered = true;
                break;
            }
        }
        if (!rendered) throw new Error('搜索没有显示匹配记录数 ' + expected);
        const painted = await tick();
        intervals.push(painted - last);
        last = painted;
        searches.push(performance.now() - start);
    }
    return {idle, intervals, searches, focused:document.activeElement === search};
}"""


def batch_timing(page):
    page.locator('#search').fill(FIRST_MODEL)
    expect(page.locator('.report-matrix tbody tr[data-model]')).to_have_count(1)
    page.locator('#report-window').select_option('20')
    page.locator('[data-campaign-action="batch"]').click()
    page.get_by_label('车型与阶段').select_option(label=FIRST_MODEL)
    values = [f'2026-11-{day:02d}\t{700 + day}' for day in range(1, DAYS + 1)]
    page.locator('[name=rows]').fill('\n'.join(values))
    button = page.locator('#confirm')
    # Time the actual app update and its paint, independently of Playwright polling.
    button.evaluate("""(button, expected) => button.addEventListener('click', () => {
        const started = performance.now();
        window.batchTiming = new Promise(resolve => {
            function waitForResult() {
                const quantity = document.querySelector('[data-campaign-action=report-cell][data-key=D28]');
                if (!document.querySelector('#dialog').open && quantity?.textContent === expected) {
                    requestAnimationFrame(() => resolve(performance.now() - started));
                } else if (performance.now() - started > 10000) {
                    resolve(null);
                } else {
                    requestAnimationFrame(waitForResult);
                }
            }
            requestAnimationFrame(waitForResult);
        });
    }, {once:true, capture:true})""", str(700 + DAYS))
    button.click()
    elapsed = page.evaluate('window.batchTiming')
    assert elapsed is not None, 'Batch result did not appear'
    expect(page.locator('#dialog')).to_be_hidden()
    expect(page.locator('[data-campaign-action=report-cell][data-key=D28]')).to_have_text(str(700 + DAYS))
    return round(elapsed, 2)


def percentile(values, p):
    return round(statistics.quantiles(values, n=100, method='inclusive')[p-1], 2)


def main():
    output = ROOT / 'artifacts' / 'motion'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url, store, *_), sync_playwright() as pw:
        seed_orders(store)
        browser = pw.chromium.launch(channel='msedge')
        results = []
        for width, height, theme, motion in [(1440, 900, 'light', 'no-preference'), (390, 844, 'dark', 'reduce')]:
            page = browser.new_page(viewport={'width': width, 'height': height}, color_scheme=theme, reduced_motion=motion)
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(url)
            ready(page)
            if width > 700:
                page.locator('[data-tab=reporting]').click()
            else:
                page.get_by_label('配置管理模块').select_option('reporting')
                page.get_by_role('button', name='筛选', exact=True).click()
            expect(page.locator('.report-matrix tbody tr[data-model]')).to_have_count(MODELS)
            expect(page.locator('#report-axis')).to_have_value('day')
            page.evaluate('document.fonts.ready')
            if width <= 700:
                matrix = page.locator('.report-matrix')
                scroll = matrix.evaluate("""el => {
                    const heading = el.querySelector('tbody th');
                    const start = heading.getBoundingClientRect().left;
                    el.scrollLeft = el.scrollWidth;
                    const result = {moved:el.scrollLeft, shift:Math.abs(start-heading.getBoundingClientRect().left)};
                    el.scrollLeft = 0;
                    return result;
                }""")
                assert scroll['moved'] > 0 and scroll['shift'] <= 1, scroll
            sample = page.evaluate(SAMPLE, dict(model=FIRST_MODEL, total=MODELS))
            assert sample['focused'], 'Rerender interrupted typing'
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            results.append(dict(browser=browser.version, viewport=f'{width}x{height}', theme=theme, motion=motion,
                                dailyEntries=MODELS * DAYS, frames=len(sample['intervals']), searches=len(sample['searches']),
                                frameMedianMs=round(statistics.median(sample['intervals']), 2),
                                frameP95Ms=percentile(sample['intervals'], 95), idleP95Ms=percentile(sample['idle'],95),
                                frameMaxMs=round(max(sample['intervals']), 2),
                                framesOver33Ms=sum(value>33.4 for value in sample['intervals']),
                                searchToFrameP95Ms=percentile(sample['searches'], 95)))
            # Measure reversible modal motion on the same large live table.
            modal = page.evaluate("""async () => {
                const frames=[], dialog=document.querySelector('dialog');
                const tick=()=>new Promise(requestAnimationFrame);
                const trigger=document.querySelector('[data-campaign-action="report-add"]');
                let last=await tick();
                for(let n=0;n<72;n++){
                    const frame=await tick();
                    frames.push(frame-last);
                    last=frame;
                    if(n%12===0){
                        trigger.click();
                        if(!dialog.open)throw new Error('填小订未打开');
                    }
                    if(n%12===4)document.querySelector('#cancel').click();
                }
                return {frames,open:dialog.open, transition:getComputedStyle(dialog).transitionDuration};
            }""")
            assert not modal['open']
            if motion == 'reduce':
                assert all(t.strip() == '0s' for t in modal['transition'].split(','))
            results[-1].update(modalFrames=len(modal['frames']), modalFrameP95Ms=percentile(modal['frames'], 95),
                               modalFrameMaxMs=round(max(modal['frames']), 2))
            results[-1].update(batchRows=DAYS, batchPreviewMs=batch_timing(page))
            page.locator('#undo').click()
            ready(page)
            expect(page.locator('[data-campaign-action=report-cell][data-key=D28]')).to_have_text(str(DAYS))
            assert not errors, errors
            page.screenshot(path=str(output / f'daily-{width}-{theme}.png'))
            page.close()
        browser.close()
    report = json.dumps(results, ensure_ascii=False, indent=2)
    (output / 'timings.json').write_text(report, encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
