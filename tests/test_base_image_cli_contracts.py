"""Manifest CLI uses owned byte fixtures and never starts a VM."""

import json
from unittest.mock import Mock

import pytest

from factory.core.isolation import base_image_cli as cli
from factory.core.isolation.base_image import ManifestError, manifest_path_for


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "owned-base.qcow2"
    path.write_bytes(b"owned byte fixture, not a bootable image")
    return path


def manifest(image, *extra):
    return cli.main(
        [
            "manifest",
            "--image",
            str(image),
            "--tool-version",
            "fixture-1",
            "--repo-commit",
            "fixture-commit",
            *extra,
        ]
    )


def test_manifest_then_verify_records_actual_digest_without_boot(image, capsys):
    assert manifest(image) == 0
    data = json.loads(manifest_path_for(image).read_text())
    assert data["repo_commit"] == "fixture-commit"
    assert (
        cli.main(
            [
                "verify",
                "--image",
                str(image),
                "--expected-tool-version",
                "fixture-1",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "MANIFEST-WRITTEN:" in output and "PASS: base image verified:" in output
    assert data["sha256"] in output and '"ok": true' in output


def test_existing_manifest_requires_explicit_force(image, capsys):
    assert manifest(image) == 0
    original = manifest_path_for(image).read_bytes()
    assert manifest(image) == 1
    assert manifest_path_for(image).read_bytes() == original
    assert "manifest_exists" in capsys.readouterr().out
    assert manifest(image, "--force") == 0


@pytest.mark.parametrize("kind", ["missing", "tampered", "version"])
def test_verify_reports_real_owned_file_failure(image, kind, capsys):
    if kind != "missing":
        assert manifest(image) == 0
    if kind == "tampered":
        image.write_bytes(b"tampered owned image")
    result = cli.main(
        [
            "verify",
            "--image",
            str(image),
            "--expected-tool-version",
            "different" if kind == "version" else "fixture-1",
        ]
    )
    assert result == 1 and "FAIL:" in capsys.readouterr().out


def test_missing_image_never_creates_manifest(tmp_path, capsys):
    path = tmp_path / "absent.qcow2"
    assert manifest(path) == 1
    assert (
        not manifest_path_for(path).exists()
        and "missing_image" in capsys.readouterr().out
    )


def test_typed_build_failure_has_cli_error_and_no_false_success(
    image, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "build_manifest",
        Mock(side_effect=ManifestError("fixture_refusal", "owned failure")),
    )
    assert manifest(image) == 1
    output = capsys.readouterr().out
    assert (
        "FAIL: fixture_refusal: owned failure" in output
        and "MANIFEST-WRITTEN" not in output
    )
    assert not manifest_path_for(image).exists()


@pytest.mark.parametrize(
    "args", [[], ["unknown"], ["verify"], ["manifest"], ["verify", "--image"]]
)
def test_bad_cli_shape_exits_before_any_file_operation(args, monkeypatch):
    build = Mock()
    verify = Mock()
    monkeypatch.setattr(cli, "build_manifest", build)
    monkeypatch.setattr(cli, "verify_image", verify)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    build.assert_not_called()
    verify.assert_not_called()
