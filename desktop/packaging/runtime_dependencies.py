"""Copy the locked HTTP client shared by all offline desktop packages."""
import json
from pathlib import Path
import shutil


RUNTIME_DEPENDENCIES = {"undici": "7.30.1"}


def stage_runtime_dependencies(desktop: Path, app: Path):
    declared = json.loads((desktop / "package.json").read_text()).get("dependencies")
    lock = json.loads((desktop / "package-lock.json").read_text())
    if declared != RUNTIME_DEPENDENCIES or lock["packages"][""].get("dependencies") != declared:
        raise ValueError("Runtime dependencies must match the reviewed lock")
    copies = []
    for name, version in declared.items():
        source = desktop / "node_modules" / name
        if source.is_symlink() or not source.is_dir():
            raise ValueError("Runtime dependency must be an installed regular directory")
        metadata_path = source / "package.json"
        if metadata_path.is_symlink():
            raise ValueError("Runtime dependency metadata must be a regular file")
        metadata = json.loads(metadata_path.read_text())
        if (metadata.get("name") != name or metadata.get("version") != version
                or lock["packages"]["node_modules/" + name]["version"] != version
                or metadata.get("dependencies") or metadata.get("optionalDependencies")
                or metadata.get("main") != "index.js"):
            raise ValueError("Runtime dependency is stale or has an unreviewed dependency graph")
        for entry in ("package.json", "index.js", "LICENSE", "lib"):
            path = source / entry
            if not path.exists() or path.is_symlink():
                raise ValueError("Runtime dependency entry is missing or linked")
            paths = [path, *path.rglob("*")] if path.is_dir() else [path]
            for item in paths:
                if item.is_symlink() or not (item.is_dir() or item.is_file()):
                    raise ValueError("Runtime dependency contains a non-regular entry")
                if item.is_file():
                    copies.append((item, app / "node_modules" / name / item.relative_to(source)))
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return dict(declared)
