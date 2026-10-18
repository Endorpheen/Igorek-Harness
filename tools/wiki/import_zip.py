"""Import public Wiki pages from exports without deleting missing pages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / 'docs/wiki'
ASSETS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_archive(archive: Path, language: str):
    pages, members = {}, set()
    source = {'archive_sha256': sha(archive.read_bytes())}
    with zipfile.ZipFile(archive) as stream:
        entries = stream.infolist()
        if len(entries) > 2000 or sum(e.file_size for e in entries) > 32 * 1024 * 1024:
            raise ValueError('Archive exceeds the documented import limits')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('Unsafe archive member')
            if entry.filename in members:
                raise ValueError('Duplicate archive member')
            members.add(entry.filename)
            if entry.is_dir():
                continue
            if entry.file_size > 2 * 1024 * 1024:
                raise ValueError('Archive member exceeds the import limit')
            if entry.filename == '_raw/full_wiki.json':
                raw = json.loads(stream.read(entry))
                wiki = raw.get('wiki', {}).get('wikis', {})
                metadata = next(iter(wiki.values()), {}).get('metadata', {})
                commit = metadata.get('full_commit_sha', '')
                if re.fullmatch(r'[0-9a-f]{40}', commit):
                    source['export_commit'] = commit
                generated = metadata.get('generated_at', '')
                if re.fullmatch(r'[0-9T:.+Z-]+', generated):
                    source['generated_at'] = generated
                continue
            if path.parts[0] in {'_raw', '__MACOSX'} or path.name == 'README.md':
                continue
            if path.parts[0] in {'ru_md', 'en_md'}:
                path = PurePosixPath(*path.parts[1:])
            if path.suffix.lower() not in ASSETS | {'.md'}:
                raise ValueError('Unexpected public asset type: ' + path.suffix)
            if not path.parts or str(path) in pages or path.name == 'index.md':
                raise ValueError('Duplicate or reserved public destination')
            data = stream.read(entry)
            if path.suffix == '.md':
                data.decode('utf-8-sig')
            pages[str(path)] = data
    if not any(name.endswith('.md') for name in pages):
        raise ValueError('No public Wiki pages found')
    source['language'] = language
    return pages, source


def import_archives(archives, site=SITE, accept_upstream=False):
    manifest_path = site / 'import-manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schema': 1, 'languages': {}}
    writes, conflicts, reports = [], [], []
    for language, archive in archives.items():
        if language not in {'en', 'ru'}:
            raise ValueError('Only en and ru are supported')
        files, provenance = read_archive(Path(archive), language)
        previous = manifest['languages'].get(language, {})
        records = {name: {**record, 'source_commit': record.get('source_commit', previous.get('export_commit'))}
                   for name, record in previous.get('files', {}).items()}
        for name, data in files.items():
            target = site / 'content' / language / name
            if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != site.parent):
                raise ValueError('Public destination must not traverse symlinks')
            incoming = sha(data)
            old = records.get(name, {}).get('sha256')
            current = sha(target.read_bytes()) if target.exists() else None
            if current is not None and current != incoming:
                if old == incoming:
                    continue
                if current != old and not accept_upstream:
                    conflicts.append(language + '/' + name)
                    continue
            if current != incoming:
                writes.append((target, data))
            records[name] = {'sha256': incoming, 'mermaid': len(re.findall(rb'^```mermaid\s*$', data, re.M)),
                             'source_commit': provenance.get('export_commit', previous.get('export_commit'))}
        manifest['languages'][language] = {**previous, **provenance, 'files': dict(sorted(records.items()))}
        reports.append({'language': language, 'archive_pages': sum(n.endswith('.md') for n in files),
                        'retained_files': len(records), 'mermaid': sum(r['mermaid'] for r in records.values())})
    if conflicts:
        raise ValueError('Locally maintained pages conflict; no files changed: ' + ', '.join(conflicts))
    for target, data in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    site.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--en', type=Path)
    parser.add_argument('--ru', type=Path)
    parser.add_argument('--accept-upstream', action='store_true', help='Explicitly replace conflicting local revisions with the supplied export')
    args = parser.parse_args()
    archives = {lang: getattr(args, lang) for lang in ['en', 'ru'] if getattr(args, lang)}
    if not archives:
        parser.error('Provide --en and/or --ru')
    print(json.dumps(import_archives(archives, accept_upstream=args.accept_upstream), indent=2))
