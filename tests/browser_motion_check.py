"""Measure repeated edits in a paginated matrix and verify the rendering contracts."""
import json
import statistics
import tempfile

from playwright.sync_api import expect, sync_playwright

from browser_bulk_check import seed
from browser_rules_check import ROOT, isolated_server, ready


def matrix_data():
    data = seed()
    data['models'] = data['models'][:1]
    data['versions'] = [dict(data['versions'][0], id=f'v{n}', name=f'版本 {n:02d}') for n in range(12)]
    data['options'] = [dict(data['options'][0], id=f'o{n}', name=f'外观项目 {n:03d}') for n in range(800)]
    data['matrix'] = []
    return data


# Use real change listeners, native scrolling and the browser frame clock. No network writes.
SAMPLE = """async () => {
    const tick=()=>new Promise(resolve=>requestAnimationFrame(resolve));
    const intervals=[], idle=[], edits=[];
    const cell=document.querySelector('[data-v="v10"][data-o="o5"]');
    const scroll=document.querySelector('.matrix-scroll');
    cell.focus();
    scroll.scrollLeft=scroll.scrollWidth;
    const left=scroll.scrollLeft;
    for(let n=0;n<16;n++)await tick();
    let last=await tick();
    for(let n=0;n<48;n++){const frame=await tick();idle.push(frame-last);last=frame;}
    for(let n=0;n<96;n++){
        const time=await tick();
        intervals.push(time-last);
        last=time;
        if(n%2===0){
            const start=performance.now();
            cell.value=n%4===0?'标配':'选配';
            cell.dispatchEvent(new Event('change',{bubbles:true}));
            edits.push(performance.now()-start);
        }
    }
    return {intervals, idle, edits, focus:document.activeElement===cell, scroll:scroll.scrollLeft===left,
            cells:document.querySelectorAll('.matrix-table select').length};
}"""


def percentile(values, p):
    return round(statistics.quantiles(values, n=100, method='inclusive')[p-1], 2)


def main():
    output = ROOT / 'artifacts' / 'motion'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url, store, *_), sync_playwright() as pw:
        store.save(matrix_data(), 0, '测量')
        browser = pw.chromium.launch(channel='msedge')
        results = []
        for width, height, theme, motion in [(1440, 900, 'light', 'no-preference'), (390, 844, 'dark', 'reduce')]:
            page = browser.new_page(viewport={'width': width, 'height': height}, color_scheme=theme, reduced_motion=motion)
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.goto(url)
            ready(page)
            if width > 700:
                page.get_by_role('button', name='版本与选配', exact=True).click()
            else:
                page.get_by_label('配置管理模块').select_option('matrix')
            expect(page.locator('.matrix-table select')).to_have_count(96)
            expect(page.locator('#content tbody tr')).to_have_count(8)
            page.evaluate('document.fonts.ready')
            sample = page.evaluate(SAMPLE)
            assert sample['focus'] and sample['scroll'], 'Editing moved focus or the horizontal scroll position'
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors, errors
            results.append(dict(browser=browser.version, viewport=f'{width}x{height}', theme=theme, motion=motion,
                                controls=sample['cells'], frames=len(sample['intervals']), edits=len(sample['edits']),
                                frameMedianMs=round(statistics.median(sample['intervals']), 2),
                                frameP95Ms=percentile(sample['intervals'], 95), idleP95Ms=percentile(sample['idle'],95),
                                frameMaxMs=round(max(sample['intervals']), 2),
                                framesOver33Ms=sum(value>33.4 for value in sample['intervals']),
                                editP95Ms=percentile(sample['edits'], 95)))
            # Measure reversible modal motion on the same large live table.
            modal = page.evaluate("""async () => {
                const frames=[], dialog=document.querySelector('dialog');
                const tick=()=>new Promise(requestAnimationFrame);
                const trigger=document.querySelector('[data-action="add:options"]');
                let last=await tick();
                for(let n=0;n<72;n++){
                    const frame=await tick();
                    frames.push(frame-last);
                    last=frame;
                    if(n%12===0)trigger.click();
                    if(n%12===4)document.querySelector('#cancel').click();
                }
                return {frames,open:dialog.open, transition:getComputedStyle(dialog).transitionDuration};
            }""")
            assert not modal['open']
            if motion == 'reduce':
                assert all(t.strip() == '0s' for t in modal['transition'].split(','))
            results[-1].update(modalFrames=len(modal['frames']), modalFrameP95Ms=percentile(modal['frames'], 95),
                               modalFrameMaxMs=round(max(modal['frames']), 2))
            page.locator('#undo').click()
            ready(page)
            page.evaluate("() => {scrollTo(0,0); document.querySelector('.matrix-scroll').scrollLeft=0;}")
            page.screenshot(path=str(output / f'matrix-{width}-{theme}.png'))
            page.close()
        browser.close()
    report = json.dumps(results, ensure_ascii=False, indent=2)
    (output / 'timings.json').write_text(report, encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
