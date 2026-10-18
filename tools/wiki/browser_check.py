"""Exercise rendered diagrams, language routes, search and mobile navigation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def preview(site):
    with tempfile.TemporaryDirectory(prefix='igorek-wiki-preview-') as temporary:
        (Path(temporary) / 'Igorek-Harness').symlink_to(site.resolve(), target_is_directory=True)
        handler = functools.partial(QuietHandler, directory=temporary)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/Igorek-Harness/'
        finally:
            server.shutdown(); server.server_close(); worker.join()


def check_controls(browser, page, base, output, results):
    def search(term):
        field = page.locator('input[data-md-component="search-query"]')
        field.click()
        placeholder = page.locator('#__config').evaluate("e => JSON.parse(e.textContent).translations['search.result.placeholder']")
        page.get_by_text(placeholder, exact=True).wait_for()
        field.press_sequentially(term)
        field.press('End')
        page.locator('.md-search-result__item').first.wait_for()
    page.goto(base, wait_until='networkidle')
    page.screenshot(path=str(output / 'desktop-light.png'), full_page=True)
    search('OpenCode')
    results['english_search'] = True
    page.keyboard.press('Escape')
    page.locator('label[for="__palette_1"]').first.click()
    page.wait_for_function("document.body.dataset.mdColorScheme === 'slate'")
    page.screenshot(path=str(output / 'desktop-dark.png'), full_page=True)
    results['theme_switch'] = True
    page.goto(base + 'ru/', wait_until='networkidle')
    search('хранилище')
    results['russian_search'] = True
    page.keyboard.press('Escape')
    chapter = '5-OpenCode-Delegation-Subsystem/5-OpenCode-Delegation-Subsystem/'
    page.goto(base + chapter, wait_until='networkidle')
    page.get_by_role('button', name='Select language', exact=True).click()
    page.locator('a[hreflang="ru"]').first.click()
    page.wait_for_url('**/ru/' + chapter)
    results['actual_language_navigation'] = True
    mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, device_scale_factor=1)
    phone = mobile.new_page(); phone.goto(base + 'ru/', wait_until='networkidle')
    assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Mobile page overflows horizontally'
    phone.locator('label.md-header__button[for="__drawer"]').click()
    phone.locator('.md-sidebar--primary').wait_for(state='visible')
    phone.screenshot(path=str(output / 'mobile-navigation.png'))
    results['mobile_navigation'] = True
    phone.locator('label.md-overlay').click(position={'x': 380, 'y': 420})
    assert not phone.locator('#__drawer').is_checked(), 'Mobile navigation did not close'
    phone.screenshot(path=str(output / 'mobile-home.png'), full_page=True)
    phone.goto(base + 'ru/' + chapter, wait_until='networkidle')
    assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Mobile chapter overflows horizontally'
    results['mobile_chapter'] = True


def run(site, output, base=None):
    output.mkdir(parents=True, exist_ok=True)
    results = {'pages': 0, 'diagrams': 0, 'language_switches': 0, 'errors': []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
        page = context.new_page()
        console_errors = []
        page.on('pageerror', lambda error: console_errors.append(str(error)))
        client = context.new_cdp_session(page)
        client.send('DOM.enable')
        def rendered_diagrams():
            root = client.send('DOM.getDocument', {'depth': -1, 'pierce': True})['root']
            def descendants(node):
                yield node
                for child in node.get('children', []) + node.get('shadowRoots', []):
                    yield from descendants(child)
            diagrams = []
            for node in descendants(root):
                attrs = dict(zip(node.get('attributes', [])[::2], node.get('attributes', [])[1::2]))
                if 'mermaid' in attrs.get('class', '').split():
                    children = list(descendants(node))
                    text = ' '.join(c.get('nodeValue', '') for c in children)
                    diagrams.append((any(c.get('nodeName', '').lower() == 'svg' for c in children), text))
            return diagrams
        for source in sorted(site.rglob('index.html')):
            relative = source.relative_to(site).as_posix().removesuffix('index.html')
            url = base + relative
            console_errors.clear()
            page.goto(url, wait_until='networkidle')
            count = page.locator('.mermaid').count()
            if count:
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        state = rendered_diagrams()
                        if len(state) == count and all(svg for svg, text in state):
                            break
                        if console_errors or time.monotonic() > deadline:
                            raise AssertionError('; '.join(console_errors) or 'Diagram did not render')
                        page.wait_for_timeout(200)
                    for svg, text in state:
                        if 'Syntax error' in text or 'Parse error' in text:
                            raise AssertionError(text[:160])
                    results['diagrams'] += count
                except Exception as error:
                    results['errors'].append(relative + ': diagrams: ' + str(error)[:240])
                    print('DIAGRAM ERROR', results['errors'][-1], flush=True)
            alternatives = page.locator('a[hreflang]')
            if alternatives.count() < 2:
                results['errors'].append(relative + ': missing language alternatives')
            else:
                for alternative in alternatives.all():
                    href = alternative.get_attribute('href')
                    destination = urlsplit(href).path if href.startswith('http') else href
                    if relative and not relative.startswith('ru/') and alternative.get_attribute('hreflang') == 'ru' and relative.rstrip('/') not in destination:
                        results['errors'].append(relative + ': language switch loses chapter')
                results['language_switches'] += 1
            results['pages'] += 1
            print('PAGE', results['pages'], relative or '/', 'diagrams', count, flush=True)
        check_controls(browser, page, base, output, results)
        browser.close()
    (output / 'browser-check.json').write_text(json.dumps(results, indent=2, ensure_ascii=False) + '\n')
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT / 'build/wiki/site')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/wiki/browser')
    parser.add_argument('--url', help='Verify the approved public URL using the same page routes')
    args = parser.parse_args()
    if args.url:
        result = run(args.site.resolve(), args.output, args.url.rstrip('/') + '/')
    else:
        with preview(args.site) as address:
            result = run(args.site.resolve(), args.output, address)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['errors']))
