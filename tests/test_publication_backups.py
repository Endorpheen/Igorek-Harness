"""Backup archives are private and cannot dereference external workspace roots."""
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('backup_archive', ROOT / 'factory/scripts/backup_archive.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def database(path):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE fixture(value TEXT)')
        connection.execute("INSERT INTO fixture VALUES ('ordinary fixture')")


def test_backup_creation_ignores_public_umask_and_keeps_archive_private(tmp_path):
    deploy = tmp_path / 'deploy'
    (deploy / 'factory/database').mkdir(parents=True)
    database(deploy / 'factory/database/factory.db')
    (deploy / '.env.lan').write_text('FIXTURE_VALUE=synthetic\n')
    commands = tmp_path / 'bin'
    commands.mkdir()
    sqlite = commands / 'sqlite3'
    sqlite.write_text('''#!/usr/bin/env python3
import shlex,shutil,sys
shutil.copyfile(sys.argv[1], shlex.split(sys.argv[3])[1])
''')
    sqlite.chmod(0o755)
    env = {'PATH': str(commands) + os.pathsep + os.environ['PATH']}
    process = subprocess.run(['bash', str(ROOT / 'factory/scripts/backup_create.sh'), str(deploy)],
                             env=env, text=True, capture_output=True, umask=0o022)
    assert process.returncode == 0, process.stderr
    archive = next((deploy / 'backups').glob('*.tar.gz'))
    assert archive.name.startswith('agentfactory-backup-')
    assert archive.stat().st_mode & 0o777 == 0o600
    assert (deploy / 'backups').stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('link', [True, False])
def test_backup_workspace_root_must_be_a_real_directory(tmp_path, link):
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        db = tarfile.TarInfo('factory/database/factory.db')
        output.addfile(db, io.BytesIO(b''))
        workspace = tarfile.TarInfo('factory/workspace')
        if link:
            workspace.type = tarfile.SYMTYPE
            workspace.linkname = str(tmp_path / 'private')
        output.addfile(workspace, io.BytesIO(b''))
    with pytest.raises(ValueError, match='workspace root'):
        module.extract(archive, tmp_path / 'extracted')
    assert not (tmp_path / 'extracted').exists()


def test_ordinary_archive_preserves_internal_links_and_database_contents(tmp_path):
    source = tmp_path / 'source'
    (source / 'factory/workspace').mkdir(parents=True)
    (source / 'factory/database').mkdir()
    database(source / 'factory/database/factory.db')
    (source / 'factory/workspace/note.txt').write_text('ordinary fixture')
    (source / 'factory/workspace/alias.txt').symlink_to('note.txt')
    archive = tmp_path / 'backup.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        output.add(source / 'factory', arcname='factory')
    destination = tmp_path / 'extracted'
    module.extract(archive, destination)
    assert (destination / 'factory/workspace/alias.txt').is_symlink()
    restored = tmp_path / 'restored.db'
    module.restore_database(destination / 'factory/database/factory.db', restored)
    with sqlite3.connect(restored) as connection:
        assert connection.execute('SELECT value FROM fixture').fetchone() == ('ordinary fixture',)


def test_archive_member_cannot_traverse_another_members_symlink(tmp_path):
    archive = tmp_path / 'bad.tar.gz'
    outside = tmp_path / 'outside'
    outside.mkdir()
    with tarfile.open(archive, 'w:gz') as output:
        db = tarfile.TarInfo('factory/database/factory.db')
        output.addfile(db, io.BytesIO(b''))
        root = tarfile.TarInfo('factory/workspace')
        root.type = tarfile.DIRTYPE
        output.addfile(root)
        link = tarfile.TarInfo('factory/workspace/escape')
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        output.addfile(link)
        file = tarfile.TarInfo('factory/workspace/escape/written.txt')
        file.size = 7
        output.addfile(file, io.BytesIO(b'fixture'))
    with pytest.raises(ValueError, match='non-directory'):
        module.extract(archive, tmp_path / 'extracted')
    assert list(outside.iterdir()) == []
