"""Build an isolated, relocatable Linux Python bundle from the lockfile."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


def build(output: Path, *, kvm_bindings=False):
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This prototype bundle targets Linux x86_64 only")
    if output.exists():
        raise RuntimeError("Output already exists; choose a new output directory")
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv is required on the build machine")
    repo = Path(__file__).resolve().parents[2]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".runtime-build-", dir=output.parent) as temp:
        stage = Path(temp)
        bundle = stage / "bundle"
        bundle.mkdir()
        python_root = bundle / "python"

        def command(*args):
            subprocess.run([uv, *map(str, args)], cwd=repo, check=True,
                           stdout=subprocess.DEVNULL if args[0] in {"export", "build"} else None)

        python_version = (repo / ".python-version").read_text().strip()
        command("python", "install", python_version, "--install-dir", python_root, "--no-bin", "--no-config")
        interpreters = sorted({path.resolve() for path in python_root.glob("*/bin/python3.14")})
        if len(interpreters) != 1:
            raise RuntimeError("Expected exactly one bundled Python")
        python = interpreters[0]
        requirements = stage / "requirements.txt"
        command("export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements.txt", "-o", requirements)
        command("pip", "install", "--python", python, "--target", bundle / "packages", "--link-mode", "copy", "--require-hashes", "-r", requirements)
        import sys
        subprocess.run([sys.executable, str(repo / "tools/build_wheel.py"),
                        "--output", str(stage / "wheels")], cwd=repo, check=True)
        wheels = list((stage / "wheels").glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected one project wheel")
        command("pip", "install", "--python", python, "--target", bundle / "packages", "--link-mode", "copy", "--no-deps", wheels[0])
        for metadata in (bundle / 'packages').glob('igorek_agent_factory-*.dist-info'):
            for name in ('direct_url.json', 'uv_cache.json'):
                (metadata / name).unlink(missing_ok=True)
        provenance = json.loads(wheels[0].with_suffix('.provenance.json').read_text())
        shutil.copy2(wheels[0].with_suffix('.provenance.json'), bundle / 'source-provenance.json')
        import importlib.util
        package_spec = importlib.util.spec_from_file_location("desktop_package_sources", Path(__file__).with_name("package-linux.py"))
        package_sources = importlib.util.module_from_spec(package_spec)
        package_spec.loader.exec_module(package_sources)
        for name in package_sources.BACKEND_FILES:
            shutil.copy2(repo / "desktop" / name, bundle / name)
        metadata = tomllib.loads((repo / "pyproject.toml").read_text())["project"]
        manifest = {"schema_version": 1, "python": str(python.relative_to(bundle)), "entry": "backend.py",
                    "factory_version": metadata["version"], "python_version": python_version,
                    "source_commit": provenance['source_commit'],
                    "source_modified": provenance['working_tree_modified'],
                    "lock_sha256": hashlib.sha256((repo / "uv.lock").read_bytes()).hexdigest(),
                    "wheel_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest()}
        manifest['backend_sources'] = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                                       for name in package_sources.BACKEND_FILES}
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for alias in python_root.iterdir():
            if alias.is_symlink():
                target = alias.resolve()
                if target.parent == python_root:
                    alias.unlink()
                    alias.symlink_to(target.name)
        if kvm_bindings:
            import importlib.util
            spec = importlib.util.spec_from_file_location('libvirt_bundle', repo / 'desktop/packaging/bundle-libvirt.py')
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            helper.add_bindings(bundle)
            spec = importlib.util.spec_from_file_location('socket_bundle', repo / 'desktop/packaging/bundle-socket.py')
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)
            helper.add_socket(bundle)
        bundle.rename(output)
    print(f"Bundle ready: {output}", flush=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "runtime")
    parser.add_argument('--with-kvm-bindings', action='store_true')
    args = parser.parse_args()
    build(args.output.absolute(), kvm_bindings=args.with_kvm_bindings)
