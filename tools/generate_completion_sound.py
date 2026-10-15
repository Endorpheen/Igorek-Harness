"""Synthesize the original task-completion chime without external audio samples."""
from __future__ import annotations

import argparse
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def generate(output: Path) -> None:
    rate = 48_000
    duration = 0.8
    notes = ((0.0, 659.255), (0.16, 880.0))
    samples = bytearray()
    for index in range(round(rate * duration)):
        time = index / rate
        value = 0.0
        for start, frequency in notes:
            age = time - start
            if not 0 <= age < 0.6:
                continue
            attack = min(age / 0.012, 1.0)
            release = min((0.6 - age) / 0.12, 1.0)
            envelope = attack * release * math.exp(-7.0 * age)
            phase = 2 * math.pi * frequency * age
            value += 0.23 * envelope * (math.sin(phase) + 0.12 * math.sin(2 * phase))
        samples.extend(struct.pack('<h', round(value * 32767)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='igorek-chime-') as temporary:
        source = Path(temporary) / 'chime.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(samples)
        subprocess.run([
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-i', str(source), '-map_metadata', '-1', '-c:a', 'libmp3lame',
            '-b:a', '96k', str(output),
        ], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    generate(parser.parse_args().output)
