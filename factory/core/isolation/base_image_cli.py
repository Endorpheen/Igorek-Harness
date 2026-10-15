"""CLI front-end for the guest base image manifest (build/verify subcommands).

Kept separate from :mod:`factory.core.isolation.base_image` so that importing
the verification logic never builds an argument parser. Used by
``factory/scripts/build_isolation_image.sh``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .base_image import (
    ImageBuildInfo,
    ManifestError,
    build_manifest,
    manifest_path_for,
    resolve_tool_version,
    verify_image,
    write_manifest,
)

__all__ = ["main"]


def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify_image(
        args.image,
        manifest_path=args.manifest,
        expected_tool_version=args.expected_tool_version,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    if result.ok:
        print(
            "PASS: base image verified: "
            f"sha256={result.sha256} tool_version={result.tool_version} "
            f"built_at={result.built_at} image={result.image_path} "
            f"userns_policy={result.userns_policy} "
            f"apparmor_profile={result.apparmor_profile}"
        )
        return 0
    print(f"FAIL: {result.code}: {result.reason}")
    return 1


def _cmd_manifest(args: argparse.Namespace) -> int:
    image = Path(args.image)
    if not image.is_file():
        print(f"FAIL: missing_image: base image missing: {image}")
        return 1
    target = manifest_path_for(image)
    if target.exists() and not args.force:
        print(f"FAIL: manifest_exists: {target} (use --force to overwrite)")
        return 1
    info = ImageBuildInfo(
        tool_version=args.tool_version,
        repo_commit=args.repo_commit or "",
        distro=args.distro,
        kernel=args.kernel,
        builder=args.builder,
        guest_user=args.guest_user,
        userns_policy=args.userns_policy,
        apparmor_profile=args.apparmor_profile,
    )
    try:
        manifest = build_manifest(image, info)
        written = write_manifest(manifest, image)
    except ManifestError as exc:
        print(f"FAIL: {exc.code}: {exc.detail}")
        return 1
    print(
        f"MANIFEST-WRITTEN: {written} sha256={manifest.sha256} "
        f"tool_version={manifest.tool_version} "
        f"handshake={manifest.handshake_version}"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="base_image",
        description="Build/verify the manifest of the KVM guest base image.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser(
        "verify", help="verify image+manifest; exit 0 only when fully valid"
    )
    verify.add_argument("--image", required=True, help="path to base.qcow2")
    verify.add_argument("--manifest", default=None, help="override manifest path")
    verify.add_argument(
        "--expected-tool-version",
        default=None,
        help="refuse the image when its tool version differs (boot handshake)",
    )
    verify.add_argument("--json", action="store_true", help="also print JSON result")
    verify.set_defaults(func=_cmd_verify)

    manifest = sub.add_parser(
        "manifest", help="hash an existing image and write its manifest"
    )
    manifest.add_argument("--image", required=True, help="path to base.qcow2")
    manifest.add_argument(
        "--tool-version", default=resolve_tool_version(), help="host tool version"
    )
    defaults = ImageBuildInfo(tool_version="unused")
    manifest.add_argument("--repo-commit", default="")
    manifest.add_argument("--distro", default=defaults.distro)
    manifest.add_argument("--kernel", default=defaults.kernel)
    manifest.add_argument("--builder", default=defaults.builder)
    manifest.add_argument("--guest-user", default=defaults.guest_user)
    manifest.add_argument("--userns-policy", default=defaults.userns_policy)
    manifest.add_argument(
        "--apparmor-profile", default=defaults.apparmor_profile
    )
    manifest.add_argument(
        "--force", action="store_true", help="overwrite an existing manifest"
    )
    manifest.set_defaults(func=_cmd_manifest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
