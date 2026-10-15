"""Build a wheel from a clean source inventory, excluding stale local build files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

REPO = Path(__file__).resolve().parents[1]


def git_output(*args: str, repo: Path = REPO) -> bytes:
    env = {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C.UTF-8",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.check_output(
        ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=repo, env=env,
    )


def source_files(repo: Path = REPO) -> list[Path]:
    names = git_output("ls-files", "-z", "--cached", "--others", "--exclude-standard", repo=repo).decode().split("\0")
    return sorted({Path(name) for name in names if name and (repo / name).is_file()})


def build(output: Path) -> Path:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inventory = {}
    commit = git_output("rev-parse", "HEAD").decode().strip()
    changed = bool(git_output("status", "--porcelain"))
    timestamp = git_output("show", "-s", "--format=%ct", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="igorek-wheel-") as temporary:
        stage = Path(temporary)
        for relative in source_files():
            if relative.parts[0] != "factory" and relative.parent != Path("docs/licenses") and str(relative) not in {
                "pyproject.toml", "uv.lock", "README.md", "LICENSE",
            }:
                continue
            source = REPO / relative
            if source.is_symlink() or not source.resolve().is_relative_to(REPO):
                raise ValueError(f"Source symlink rejected: {relative}")
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            inventory[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
        env = dict(os.environ, SOURCE_DATE_EPOCH=timestamp)
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(stage / "wheel"), str(stage)],
                       cwd=REPO, env=env, check=True)
        wheels = list((stage / "wheel").glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("Expected exactly one project wheel")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                if not name.startswith("factory/") or name.endswith("/"):
                    continue
                if name not in inventory:
                    raise ValueError(f"Wheel contains a file absent from clean sources: {name}")
                if hashlib.sha256(archive.read(name)).hexdigest() != inventory[name]:
                    raise ValueError(f"Wheel source mismatch: {name}")
        target = output / wheel.name
        if target.exists():
            raise FileExistsError("Choose a new output directory; an existing wheel will not be overwritten")
        shutil.copy2(wheel, target)
        provenance = {"source_commit": commit, "working_tree_modified": changed,
                      "wheel_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "sources": inventory}
        target.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Verified clean-source wheel: {target}", flush=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
