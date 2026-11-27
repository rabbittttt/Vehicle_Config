"""Measure report-matrix search, batch updates and dialogs on desktop and mobile."""
import json
import statistics
import tempfile
from datetime import date, timedelta

from playwright.sync_api import expect, sync_playwright

from browser_rules_check import ROOT, isolated_server, ready
from campaign_store import CampaignStore
from stage_workbook import empty_workbook, export_workbook


MODELS = 32
DAYS = 28
CALENDAR_DAYS = 785
LAST_DATE = date(2026, 11, 28)
FIRST_MODEL = '测量车型 00'


def seed_orders(store):
    book = empty_workbook()
    summary, daily = book['sheets'][:2]
    first_date = LAST_DATE - timedelta(days=CALENDAR_DAYS - 1)
    dates = [(first_date + timedelta(days=n)).isoformat() for n in range(CALENDAR_DAYS)]
    daily['rows'] = [['代际名', *dates, '合计', '二级代际名']]
    for index in range(MODELS):
        model = f'测量车型 {index:02d}'
        # Stagger model launches across a multi-year calendar.
        start = (MODELS - 1 - index) * (CALENDAR_DAYS - DAYS) // (MODELS - 1)
        summary['rows'].append([model, *([None] * 7), dates[start], dates[start + DAYS - 1]])
        quantities = [None] * CALENDAR_DAYS
        quantities[start:start + DAYS] = range(index + 1, index + 1 + DAYS)
        daily['rows'].append([model, *quantities])
    CampaignStore(store.root).import_legacy(0, export_workbook(book), '合成数据')


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
    matrix = page.locator('.report-matrix')
    matrix.evaluate('el => el.scrollLeft = el.scrollWidth')
    last = matrix.locator('[data-campaign-action=report-cell][data-key=D28]')
    expect(last).to_have_text(str(DAYS))
    last.scroll_into_view_if_needed()
    before = matrix.evaluate('el => el.scrollLeft')
    assert before > 0
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
    expect(last).to_have_text(str(700 + DAYS))
    assert abs(matrix.evaluate('el => el.scrollLeft') - before) <= 1, 'Batch apply moved the timeline'
    return round(elapsed, 2)


def check_calendar_scroll(page):
    axis = page.locator('#report-axis')
    axis.select_option('date')
    matrix = page.locator('.report-matrix')
    expect(page.locator('#actions')).not_to_contain_text('范围')
    first_date = (LAST_DATE - timedelta(days=CALENDAR_DAYS - 1)).isoformat()
    last_model = matrix.locator(f'tr[data-model="测量车型 {MODELS - 1:02d}"]')
    first_model = matrix.locator(f'tr[data-model="{FIRST_MODEL}"]')
    heading_x = first_model.locator('th').bounding_box()['x']
    matrix.evaluate('el => el.scrollLeft = 0')
    expect(last_model.locator(f'[data-key="{first_date}"]')).to_have_text(str(MODELS))
    span, visible = matrix.evaluate('el => [el.scrollWidth, el.clientWidth]')
    assert span > visible * 10, (span, visible)
    # Sample a continuous sideways scroll, then drag the thumb to the far edge.
    frames = matrix.evaluate("""async el => {
        const tick = () => new Promise(requestAnimationFrame);
        const frames = [];
        let last = await tick();
        for (let i = 1; i <= 60; ++i) {
            el.scrollLeft += 48;
            const now = await tick();
            frames.push(now - last);
            last = now;
        }
        return frames;
    }""")
    matrix.evaluate('el => el.scrollLeft = el.scrollWidth')
    far_cell = first_model.locator(f'[data-key="{LAST_DATE.isoformat()}"]')
    expect(far_cell).to_have_text(str(DAYS))
    assert abs(first_model.locator('th').bounding_box()['x'] - heading_x) <= 1
    far_cell.click()
    expect(page.get_by_label('日期', exact=True)).to_have_value(LAST_DATE.isoformat())
    page.locator('#cancel').click()
    far_cell.press('Home')
    first_cell = first_model.locator(f'[data-key="{first_date}"]')
    expect(first_cell).to_be_focused()
    first_cell.press('End')
    expect(far_cell).to_be_focused()
    axis.select_option('day')
    return percentile(frames, 95)


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
            scroll_p95 = check_calendar_scroll(page)
            sample = page.evaluate(SAMPLE, dict(model=FIRST_MODEL, total=MODELS))
            assert sample['focused'], 'Rerender interrupted typing'
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            results.append(dict(browser=browser.version, viewport=f'{width}x{height}', theme=theme, motion=motion,
                                calendarColumns=CALENDAR_DAYS, horizontalScrollFrameP95Ms=scroll_p95,
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
