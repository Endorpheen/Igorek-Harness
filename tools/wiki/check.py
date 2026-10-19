"""Check imported completeness, public content boundaries and built local links."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
WIKI = ROOT / 'docs/wiki'
BASE = 'https://endorpheen.github.io/Igorek-Harness/'
PRIVATE = re.compile(r'/home/(?!example(?:/|\b)|user(?:/|\b)|agent(?:/|\b)|runner(?:/|\b)|opencode(?:/|\b))[a-z][a-z0-9_-]+/|app\.devin\.ai/org/|192\.168\.\d+\.\d+|(?:sk-proj-|github_pat_)[A-Za-z0-9_]{15,}')


def check(site):
    errors, counts = [], {}
    manifest = json.loads((WIKI / 'import-manifest.json').read_text())
    for language, value in manifest['languages'].items():
        pages, diagrams = 0, 0
        for name, record in value['files'].items():
            path = WIKI / 'content' / language / name
            if not path.is_file():
                errors.append('Missing imported file: ' + language + '/' + name)
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                errors.append('Imported original changed without provenance: ' + language + '/' + name)
            if path.suffix == '.md':
                pages += 1
                diagrams += len(re.findall(rb'^```mermaid\s*$', data, re.M))
        counts[language] = {'imported_pages': pages, 'original_mermaid': diagrams}
    en = {p.relative_to(WIKI / 'content/en').as_posix() for p in (WIKI / 'content/en').rglob('*.md')}
    ru = {p.relative_to(WIKI / 'content/ru').as_posix() for p in (WIKI / 'content/ru').rglob('*.md')}
    if en != ru:
        errors.append('Language page parity differs: ' + str(sorted(en ^ ru)))
    for root in [WIKI / 'content', WIKI / 'updates', site]:
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix not in {'.md', '.html', '.json', '.css', '.svg', '.js'}:
                continue
            text = path.read_text(errors='replace')
            if PRIVATE.search(text):
                errors.append('Private marker detected (value omitted): ' + str(path.relative_to(root)))
    html = {p.resolve(): BeautifulSoup(p.read_text(), 'html.parser') for p in site.rglob('*.html')}
    checked = 0
    source_links = 0
    for path, soup in html.items():
        page_url = BASE + path.relative_to(site.resolve()).as_posix()
        for code in soup.find_all('code'):
            if not code.find_parent(['pre', 'a']) and re.search(r'[.\w/-]+:[0-9]+(?:-[0-9]+)?', code.get_text()):
                errors.append(str(path.relative_to(site)) + ': source citation rendered as literal code: ' + code.get_text()[:120])
        source_links += sum(bool(re.match(r'https://github\.com/Endorpheen/Igorek-Harness/blob/[^/]+/.+#L\d+', a.get('href', ''))) for a in soup.find_all('a'))
        for tag, attribute in [('a', 'href'), ('img', 'src'), ('script', 'src'), ('link', 'href')]:
            for item in soup.find_all(tag):
                target = item.get(attribute)
                if target is None:
                    continue
                if target == '' and tag == 'a':
                    errors.append(str(path.relative_to(site)) + ': empty link ' + item.get_text()[:80])
                    continue
                parsed = urlsplit(urljoin(page_url, target))
                if parsed.scheme == 'javascript':
                    errors.append('Active JavaScript link in built page')
                    continue
                if parsed.netloc != urlsplit(BASE).netloc:
                    continue
                if not parsed.path.startswith('/Igorek-Harness/'):
                    errors.append('Link escapes the Pages project path: ' + parsed.path)
                    continue
                destination = site / unquote(parsed.path.removeprefix('/Igorek-Harness/'))
                if destination.is_dir():
                    destination /= 'index.html'
                destination = destination.resolve()
                checked += 1
                if not destination.is_relative_to(site.resolve()) or not destination.is_file():
                    errors.append(str(path.relative_to(site)) + ': missing local target ' + target)
                elif parsed.fragment and destination in html and not html[destination].find(id=unquote(parsed.fragment)):
                    errors.append(str(path.relative_to(site)) + ': missing anchor ' + target)
    indexes = list(site.rglob('search_index.json'))
    if not indexes or not any('OpenCode' in p.read_text() for p in indexes):
        errors.append('Search index missing technical content')
    result = {'languages': counts, 'html_pages': len(html), 'local_links_checked': checked, 'source_links': source_links,
              'search_indexes': len(indexes), 'errors': errors}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT / 'build/wiki/site')
    parser.add_argument('--report', type=Path, default=ROOT / 'build/wiki/check.json')
    args = parser.parse_args()
    result = check(args.site.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({**result, 'errors': result['errors'][:20]}, indent=2, ensure_ascii=False))
    raise SystemExit(bool(result['errors']))
