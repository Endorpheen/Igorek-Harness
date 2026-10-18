"""Provide navigation and repair export link targets without changing prose."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote

SITE = Path(__file__).resolve().parent
REPOSITORY = 'https://github.com/Endorpheen/Igorek-Harness'


def mermaid_source(source):
    source = re.sub(r'(?<=[\[({])""', '"', source)
    source = re.sub(r'""(?=[\])}])', '"', source)
    source = re.sub(r'^(\s*)Sources:', r'\1%% Sources:', source, flags=re.M)
    source = re.sub(r'^\s*sub:\s*(.+)$', r'    %% Diagram mapping: \1', source, flags=re.M)
    source = re.sub(r'^\s*classDef default [^\n]+\n?', '', source, flags=re.M)
    source = re.sub(r'^\s*style \w+ fill:none\s*$', '', source, flags=re.M)
    source = re.sub(r'\["([^"\n]*)"\s*/\s*"([^"\n]*)"\]', r'["\1 / \2"]', source)
    source = re.sub(r'\b([A-Za-z_][\w]*)[ \t]+\["', r'\1["', source)
    source = re.sub(r'\|([^|\n]+)\|', lambda m: m.group(0) if m.group(1).startswith('"') else '|"' + m.group(1).replace('"', '&quot;') + '"|', source)
    groups = len(re.findall(r'^\s*subgraph\b', source, re.M))
    endings = len(re.findall(r'^\s*end\s*$', source, re.M))
    pseudo = list(re.finditer(r'^(\s*)(sub\w+)\s*\["([^"\n]+)"\]\s*$', source, re.M))
    if endings > groups and endings == groups + len(pseudo):
        source = re.sub(r'^(\s*)(sub\w+)\s*\["([^"\n]+)"\]\s*$', r'\1subgraph \2["\3"]', source, flags=re.M)
    title = re.search(r'^title\s+"([^\n]+)"\s*\n', source, re.M)
    if title:
        source = source[:title.start()] + source[title.end():]
        source = '---\ntitle: ' + json.dumps(title.group(1), ensure_ascii=False) + '\n---\n' + source
    settings = {name: {'useMaxWidth': False} for name in ['flowchart', 'sequence', 'class', 'state', 'er']}
    directive = '%%{init: ' + json.dumps(settings, separators=(',', ':')) + '}%%\n'
    if source.startswith('---\n'):
        end = source.index('\n---\n', 4) + len('\n---\n')
        source = source[:end] + directive + source[end:]
    else:
        source = directive + source
    return source


def heading(path):
    content = SITE / 'content'
    if path.is_relative_to(content):
        update = SITE / 'updates' / path.relative_to(content)
        edition = json.loads((SITE / 'edition.json').read_text())
        if update.is_file() and path.relative_to(content).parts[1:] and path.relative_to(content).as_posix().split('/', 1)[-1] in edition['historical_pages']:
            path = update
    text = path.read_text(encoding='utf-8-sig')
    match = re.search(r'^# (.+)$', text, re.M)
    return match.group(1).strip() if match else ('Repository note' if path.parts[-2] == 'en' else 'О проекте')


def on_config(config):
    root = SITE / 'content/en'
    numbered = {}
    translations = {'Home': 'Главная', 'Chapter overview': 'Обзор раздела', 'Repository note': 'О проекте'}
    for path in root.rglob('*.md'):
        relative = path.relative_to(root)
        other = SITE / 'content/ru' / relative
        if other.exists():
            translations[heading(path)] = heading(other)
        match = re.match(r'([0-9]+(?:\.[0-9]+)*)-', path.name)
        if match:
            numbered[match.group(1)] = (heading(path), relative.as_posix())
    def item(key):
        title, path = numbered[key]
        children = sorted((k for k in numbered if k.rpartition('.')[0] == key), key=lambda k: tuple(map(int, k.split('.'))))
        return {title: [{'Chapter overview': path}, *[item(k) for k in children]]} if children else {title: path}
    config.nav = [{'Home': 'index.md'}, *[item(k) for k in sorted((k for k in numbered if '.' not in k), key=int)], {'Repository note': 'repo-note.md'}]
    for language in config.plugins['i18n'].config.languages:
        if language.locale == 'ru':
            language.nav_translations = translations
    return config


def on_page_markdown(markdown, page, config, files):
    markdown = re.sub(r'<details>(\s*<summary>)', r'<details markdown="1">\1', markdown)
    manifest = json.loads((SITE / 'import-manifest.json').read_text())
    default_commit = manifest['languages']['en'].get('export_commit', 'main')
    actual = Path(page.file.abs_src_path)
    root = SITE / 'content'
    relative = actual.relative_to(root)
    language = relative.parts[0] if relative.parts[0] in {'en', 'ru'} else 'en'
    base = SITE / 'content' / language
    page_record = manifest['languages'][language]['files'].get('/'.join(relative.parts[1:]), {})
    default_commit = page_record.get('source_commit') or manifest['languages'][language].get('export_commit', default_commit)
    update = SITE / 'updates' / relative
    if update.is_file():
        edition = json.loads((SITE / 'edition.json').read_text())
        if '/'.join(relative.parts[1:]) in edition['historical_pages']:
            label = 'Historical implementation · 0.2.10' if language == 'en' else 'Историческая реализация · 0.2.10'
            warning = ('The following original export is preserved in full as historical reference. Its copy/transfer and cancellation flows are not the current assignment path.' if language == 'en' else 'Исходный экспорт ниже сохранён полностью как историческая справка. Описанные копирование, перенос и отмена через инструмент не являются текущим механизмом поручений.')
            markdown = update.read_text() + '\n\n---\n\n## ' + label + '\n\n!!! warning "0.2.10"\n    ' + warning + '\n\n' + re.sub(r'^# ', '## ', markdown, count=1)
        else:
            title, separator, rest = markdown.partition('\n')
            markdown = title + '\n\n' + update.read_text() + '\n\n' + rest if separator else markdown
    def source_link(path, start=None, end=None):
        target = REPOSITORY + ('/tree/' if path.endswith('/') else '/blob/') + default_commit + '/' + quote(path, safe='/')
        return target + (f'#L{start}' + (f'-L{end}' if end else '') if start else '')
    def link(match):
        label, target = match.group(1), match.group(2)
        if not target:
            citation = re.fullmatch(r'(?:non-cited:\s*)?`?([.\w/-]+)(?::([\d,\s-]+))?`?', label)
            if citation:
                path, ranges = citation.groups()
                if '/' in path or path in {'AGENTS.md', 'README.md', 'README.ru.md', 'pyproject.toml', 'uv.lock', '.gitignore', '.gitleaks.toml', '.gitattributes'}:
                    numbers = list(map(int, re.findall(r'\d+', ranges or '')))
                    return '[' + label + '](' + source_link(path, min(numbers) if numbers else None, max(numbers) if len(numbers) > 1 else None) + ')'
            return match.group(0)
        if re.fullmatch(r'#[0-9]+(?:\.[0-9]+)*', target):
            destination = next(base.rglob(target[1:] + '-*.md'), None)
            if destination:
                return '[' + label + '](' + Path(os.path.relpath(destination, actual.parent)).as_posix() + ')'
        if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:|#|/', target):
            return match.group(0)
        path, _, fragment = target.partition('#')
        wiki = (actual.parent / path).resolve()
        if wiki.is_relative_to(base) and wiki.exists() and wiki != base / 'README.md':
            return match.group(0)
        repo_path = PurePosixPath(path).as_posix()
        if repo_path.startswith(('factory/', 'desktop/', 'android/', 'tests/', 'tools/', 'docs/', '.github/')) or repo_path in {'AGENTS.md', 'README.md', 'README.ru.md', 'pyproject.toml', 'uv.lock', '.gitignore', '.gitleaks.toml', '.gitattributes'}:
            return '[' + label + '](' + source_link(repo_path) + ('#' + fragment if fragment else '') + ')'
        return match.group(0)
    parts = re.split(r'(^```[^\n]*\n.*?^```\s*$)', markdown, flags=re.M | re.S)
    result = []
    for part in parts:
        if part.startswith('```mermaid'):
            first, _, body = part.partition('\n')
            body, _, closing = body.rpartition('```')
            part = first + '\n' + mermaid_source(body) + '\n```' + closing
        elif not part.startswith('```'):
            part = re.sub(r'(?<!!)\[([^\]]+)\]\(([^)]*)\)', link, part)
        result.append(part)
    return ''.join(result)
