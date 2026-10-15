"""Validate backup entries before extracting and restore a regular database."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sqlite3
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from factory.core.artifact_files import atomic_write_file, open_regular_file


def archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or '\0' in name:
        raise ValueError('Backup contains an unsafe archive path')
    return path


def extract(archive: Path, destination: Path) -> None:
    workspace = PurePosixPath('factory/workspace')
    database = PurePosixPath('factory/database/factory.db')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        inventory = {}
        for member in members:
            name = archive_path(member.name)
            if name in inventory or not (member.isdir() or member.isreg() or member.issym() or member.islnk()):
                raise ValueError('Backup contains duplicate or special entries')
            inventory[name] = member
        if database not in inventory or not inventory[database].isreg():
            raise ValueError('Backup database must be a regular file')
        if workspace in inventory and not inventory[workspace].isdir():
            raise ValueError('Backup workspace root must be a directory')
        for name, member in inventory.items():
            for parent in name.parents:
                if parent in inventory and not inventory[parent].isdir():
                    raise ValueError('Backup entry traverses a non-directory member')
            if member.islnk():
                target = archive_path(member.linkname)
                if target not in inventory or not inventory[target].isreg():
                    raise ValueError('Backup hardlink must reference a regular archive member')
                if name.is_relative_to(workspace) and not target.is_relative_to(workspace):
                    raise ValueError('Workspace hardlink points outside the archived workspace')
                member.linkname = str(target)
            member.name = str(name)
            member.mode &= 0o1777
        # Every member path and ancestor has been checked. Symlinks remain data;
        # no archive member may be extracted through one of them.
        source.extractall(destination, members=members, filter='fully_trusted')
    validate_database(destination / str(database))


def validate_database(source: Path) -> None:
    with open_regular_file(source.absolute()) as stream:
        if stream.read(16) != b'SQLite format 3\0':
            raise ValueError('Backup database is not SQLite')
    with sqlite3.connect(source.absolute().as_uri() + '?mode=ro', uri=True) as connection:
        if connection.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ValueError('Backup database integrity check failed')


def restore_database(source: Path, destination: Path) -> None:
    validate_database(source)
    # The validated source is in the private extraction directory. The output
    # is replaced atomically, without following a final or parent symlink.
    with open_regular_file(source.absolute()) as stream:
        atomic_write_file(destination.expanduser().absolute(), stream, mode=0o600,
                          preserve_mode=False, preserve_owner=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['extract', 'database'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        (extract if args.action == 'extract' else restore_database)(args.source, args.destination)
    except (OSError, ValueError, tarfile.TarError, sqlite3.Error) as error:
        print(f'[FAIL] {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
