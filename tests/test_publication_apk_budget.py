"""Repeated binary XML offsets cannot multiply APK parser memory use."""
import struct
import tracemalloc
import zipfile

import pytest

from factory.core.isolation.apk_delivery import apk_metadata


def test_repeated_string_offsets_share_one_bounded_decoding(tmp_path):
    count = 512
    value = 'x' * 16384
    encoded = struct.pack('<H', len(value)) + value.encode('utf-16le') + b'\0\0'
    start = 28 + 4 * count
    pool = struct.pack('<HHIIIIII', 1, 28, start + len(encoded), count, 0, 0, start, 0)
    pool += b'\0' * (count * 4) + encoded
    xml = struct.pack('<HHI', 3, 8, 8 + len(pool)) + pool
    path = tmp_path / 'fixture.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('AndroidManifest.xml', xml)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match='apk_manifest_invalid'):
            apk_metadata(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024


@pytest.mark.parametrize('offset', [100000, 0xffffffff])
def test_string_offsets_outside_their_chunk_are_rejected(tmp_path, offset):
    pool = struct.pack('<HHIIIIII', 1, 28, 36, 1, 0, 0, 32, 0)
    pool += struct.pack('<I', offset) + b'\0' * 4
    xml = struct.pack('<HHI', 3, 8, 8 + len(pool)) + pool
    path = tmp_path / 'fixture.apk'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('AndroidManifest.xml', xml)
    with pytest.raises(ValueError, match='apk_manifest_invalid'):
        apk_metadata(path)
