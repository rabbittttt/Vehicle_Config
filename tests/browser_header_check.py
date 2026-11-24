"""Compare local Edge header images. Updating references requires an explicit flag."""
import argparse
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from browser_rules_check import ROOT, isolated_server, ready


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--approve',action='store_true')
    args=parser.parse_args()
    output=ROOT/'artifacts'/'visual-baseline'
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, isolated_server(tmp) as (url,*_), sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge')
        for width in (1440,390):
            page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
            page.goto(url)
            ready(page)
            page.evaluate('document.fonts.ready')
            actual=page.locator('header').screenshot(animations='disabled')
            baseline=output/f'header-{width}.png'
            if args.approve:
                baseline.write_bytes(actual)
            elif not baseline.exists() or baseline.read_bytes()!=actual:
                result=output/f'header-{width}-actual.png'
                result.write_bytes(actual)
                raise AssertionError(f'Header baseline missing or changed: {baseline}; current image: {result}')
            page.close()
        browser.close()
    print('Header baselines approved.' if args.approve else 'Desktop and narrow header images match.')


if __name__=='__main__':main()
