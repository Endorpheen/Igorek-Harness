"""Export an approved committed source tree without private Git history."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import tomllib

from build_wheel import REPO, git_output
from check_publication import check


def export(output: Path) -> Path:
    if git_output("status", "--porcelain"):
        raise ValueError("Commit the reviewed source tree before exporting")
    problems = check()
    if problems:
        raise ValueError(f"Publication checks have {len(problems)} unresolved problem(s)")
    commit = git_output("rev-parse", "HEAD").decode().strip()
    timestamp = int(git_output("show", "-s", "--format=%ct", "HEAD").decode().strip())
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    prefix = f"igorek-{version}-source"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    if archive.exists():
        raise FileExistsError("Choose a new output directory; archives are not overwritten")
    entries = git_output("ls-tree", "-r", "-z", "HEAD").split(b"\0")
    inventory = {}
    try:
        with archive.open("xb") as destination:
            with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=timestamp) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as tar:
                    for entry in entries:
                        if not entry:
                            continue
                        metadata, encoded_name = entry.split(b"\t", 1)
                        mode, kind, oid = metadata.split()
                        name = encoded_name.decode()
                        path = PurePosixPath(name)
                        if kind != b"blob" or mode not in {b"100644", b"100755"}:
                            raise ValueError(f"Unsupported source entry: {name}")
                        if path.is_absolute() or ".." in path.parts:
                            raise ValueError("Source entry escapes archive root")
                        data = git_output("cat-file", "blob", oid.decode())
                        info = tarfile.TarInfo(f"{prefix}/{name}")
                        info.size = len(data)
                        info.mode = 0o755 if mode == b"100755" else 0o644
                        info.mtime = timestamp
                        tar.addfile(info, io.BytesIO(data))
                        inventory[name] = hashlib.sha256(data).hexdigest()
    except Exception:
        archive.unlink(missing_ok=True)
        raise
    receipt = {"source_commit": commit, "version": version, "history_included": False,
               "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "files": inventory}
    archive.with_suffix(".manifest.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Source-only archive: {archive}; {len(inventory)} files; SHA256 {receipt['sha256']}")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args().output)
