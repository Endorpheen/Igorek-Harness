#!/usr/bin/env python3
"""Structural closure of the host-escape surface (plan todo 15).

The guest (a per-session KVM VM) must have no host user D-Bus, no systemd
control socket, no ``docker.sock``, no libvirt socket and no ssh-agent socket,
and the documented ``systemd-run --user`` escape (``handoff.md:45-48``) must be
impossible. The assertions here are structural, never prompt- or wording-based:

* every named host control surface is asserted absent/refused INDIVIDUALLY
  against the generated domain XML, the workspace export set, the guest roots
  and the guest sandbox environment names - a single "no escape" aggregate
  assertion would hide which surface regressed;
* the consolidated :class:`factory.core.isolation.guest_contract.GuestContract`
  verdict for each surface must be ``closed`` and the contract audit must be
  violation-free;
* detectability: deliberately injecting ``/run``, a host socket channel, a
  forged control-path export or a D-Bus environment name into the artifacts
  MUST produce a violation and a refused :class:`IsolationError` - if any test
  mount/export ever adds a control path, these tests fail loudly.

TDD note: this file was written before ``host_escape`` existed; the first run
failed at import (the closure artifact did not exist yet) - never a vacuous
pass.
"""

from __future__ import annotations

import dataclasses
import os
import socket
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import domain as domain_module  # noqa: E402
from factory.core.isolation import guest_contract  # noqa: E402
from factory.core.isolation import host_escape  # noqa: E402
from factory.core.isolation import workspace as workspace_module  # noqa: E402
from factory.core.isolation.domain import (  # noqa: E402
    DomainSpec,
    IsolationError,
    build_domain_xml,
)
from factory.core.isolation.guest_task import DEFAULT_GUEST_ROOTS  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
    resolve_workspace_export,
)

#: Ambient environment that DOES carry live host control connections. The
#: contract must still exclude every one of them from the guest sandbox env.
HOSTILE_ENV: dict[str, str] = {
    "HOME": "/home/agent",
    "PATH": "/usr/bin:/bin",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/run/dbus/system_bus_socket",
    "SSH_AUTH_SOCK": "/run/user/1000/ssh-agent.socket",
    "XDG_RUNTIME_DIR": "/run/user/1000",
}

#: Host control roots a domain/export must never touch. ``/var/run`` is the
#: traditional alias of ``/run`` and is checked separately on purpose.
FORBIDDEN_XML_PATHS: tuple[str, ...] = ("/run/", "/var/run", "/proc/", "/sys/", "/dev/")


class _ContractCase(unittest.TestCase):
    """Given: a real spec/export pair plus the resolved host uid."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="igorek_no_host_escape_")
        self.addCleanup(tmp.cleanup)
        root = Path(os.path.realpath(tmp.name))
        self.root = root
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "project.txt").write_text("workspace\n", encoding="utf-8")
        self.base_image = root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2-base")
        self.overlay_dir = root / "overlays"
        self.home = root / "home"
        self.home.mkdir()
        self.data_root = root / "data"
        self.data_root.mkdir()
        self.uid = os.getuid()
        # network=True is the production shape (manager.ensure_vm): the one
        # restricted QEMU user-mode interface must not become a host binding.
        self.spec = DomainSpec(
            session_id="no-host-escape",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            network=True,
        )
        self.export = resolve_workspace_export(
            self.workspace,
            policy=WorkspacePolicy(
                home=self.home, data_root=self.data_root, host_uid=self.uid
            ),
        )

    # -- builders -------------------------------------------------------

    def _xml(self) -> str:
        return build_domain_xml(self.spec)

    def _contract(
        self,
        *,
        domain_xml: str | None = None,
        export: Mapping[str, object] | None = None,
        guest_roots: Sequence[str] | None = None,
    ) -> guest_contract.GuestContract:
        return guest_contract.build_guest_contract(
            session_id="no-host-escape",
            domain_name="igorek-session-no-host-escape",
            domain_xml=self._xml() if domain_xml is None else domain_xml,
            export=self.export if export is None else export,
            guest_roots=DEFAULT_GUEST_ROOTS if guest_roots is None else guest_roots,
            env=HOSTILE_ENV,
            uid=self.uid,
        )

    def _inject(self, fragment: str) -> str:
        """Append one device fragment to the generated XML (a simulated regression)."""
        xml = self._xml()
        marker = "</devices>"
        self.assertIn(marker, xml)
        return xml.replace(marker, f"  {fragment}\n  {marker}", 1)

    def _assert_verdict_closed(
        self, audit: guest_contract.ContractAudit, key: str, *, where: str
    ) -> None:
        verdict = audit.surface(key)
        self.assertIsNotNone(verdict, f"{where}: no verdict for surface {key!r}")
        assert verdict is not None
        label = verdict.path or verdict.surface.env_name or key
        self.assertFalse(verdict.in_domain_xml, f"{where}: {key} ({label}) is in the XML")
        self.assertFalse(verdict.exported, f"{where}: {key} ({label}) is exported")
        self.assertFalse(verdict.in_guest_roots, f"{where}: {key} ({label}) is a guest root")
        self.assertFalse(verdict.in_env, f"{where}: {key} ({label}) crosses in the env")
        self.assertTrue(verdict.closed, f"{where}: {key} ({label}): {verdict.detail}")


class HostSocketAbsenceTests(_ContractCase):
    """One explicit test per documented host control surface (never aggregated)."""

    def test_host_user_dbus_socket_is_absent_and_closed(self) -> None:
        path = f"/run/user/{self.uid}/bus"

        self.assertNotIn(path, self._xml())
        audit = guest_contract.assert_no_host_escape(self._contract())
        self._assert_verdict_closed(audit, "host_user_dbus", where="user D-Bus")

    def test_systemd_control_sockets_are_absent_and_closed(self) -> None:
        paths = (
            "/run/systemd/private",
            f"/run/user/{self.uid}/systemd/private",
            "/run/systemd/system",
        )
        xml = self._xml()
        for path in paths:
            with self.subTest(path=path):
                self.assertNotIn(path, xml)
        audit = guest_contract.assert_no_host_escape(self._contract())
        for key in ("host_systemd_private", "host_systemd_user_private", "host_systemd_system_dir"):
            with self.subTest(surface=key):
                self._assert_verdict_closed(audit, key, where="systemd control socket")

    def test_docker_socket_is_absent_and_closed(self) -> None:
        xml = self._xml()

        self.assertNotIn("/run/docker.sock", xml)
        self.assertNotIn("/var/run/docker.sock", xml)
        audit = guest_contract.assert_no_host_escape(self._contract())
        self._assert_verdict_closed(audit, "host_docker_socket", where="docker socket")
        self._assert_verdict_closed(audit, "host_docker_socket_var_run", where="docker socket")

    def test_libvirt_sockets_are_absent_and_closed(self) -> None:
        xml = self._xml()

        self.assertNotIn("/run/libvirt/libvirt-sock", xml)
        self.assertNotIn("/run/libvirt/libvirt-sock-ro", xml)
        audit = guest_contract.assert_no_host_escape(self._contract())
        self._assert_verdict_closed(audit, "host_libvirt_socket", where="libvirt socket")
        self._assert_verdict_closed(audit, "host_libvirt_socket_ro", where="libvirt socket")

    def test_ssh_agent_socket_is_absent_and_closed(self) -> None:
        path = f"/run/user/{self.uid}/ssh-agent.socket"
        xml = self._xml()

        self.assertNotIn(path, xml)
        self.assertNotIn("SSH_AUTH_SOCK", xml)
        audit = guest_contract.assert_no_host_escape(self._contract())
        self._assert_verdict_closed(audit, "host_ssh_agent_socket", where="ssh-agent socket")
        self._assert_verdict_closed(audit, "host_ssh_agent_env", where="ssh-agent socket")

    def test_system_dbus_socket_is_absent_and_closed(self) -> None:
        path = "/run/dbus/system_bus_socket"

        self.assertNotIn(path, self._xml())
        audit = guest_contract.assert_no_host_escape(self._contract())
        self._assert_verdict_closed(audit, "host_system_dbus", where="system D-Bus")

    def test_every_resolved_surface_path_is_absent_from_the_domain_xml(self) -> None:
        xml = self._xml()

        for surface in host_escape.resolve_surfaces(self.uid):
            with self.subTest(surface=surface.key):
                if surface.path:
                    self.assertNotIn(surface.path, xml)
                    self.assertNotIn(surface.path.rstrip("/") + "/", xml)
        for forbidden in FORBIDDEN_XML_PATHS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, xml)

    def test_domain_xml_contains_no_host_binding_device(self) -> None:
        xml = self._xml()

        for tag in host_escape.FORBIDDEN_DEVICE_TAGS:
            with self.subTest(tag=tag):
                self.assertEqual(
                    ET.fromstring(xml).findall(f".//{tag}"),
                    [],
                    f"<{tag}> is a host binding and must not appear in the domain XML",
                )
        self.assertNotIn("hostfwd", xml)
        self.assertNotIn('accessmode="passthrough"', xml)
        self.assertNotIn("accessmode='passthrough'", xml)

    def test_no_host_dbus_or_ssh_env_name_crosses_into_the_guest(self) -> None:
        for name in ("DBUS_SESSION_BUS_ADDRESS", "DBUS_SYSTEM_BUS_ADDRESS", "SSH_AUTH_SOCK"):
            with self.subTest(name=name):
                self.assertIn(name, HOSTILE_ENV, "the hostile env must really carry it")
                self.assertNotIn(name, self._contract().env_names)


class GuestContractClosureTests(_ContractCase):
    """The consolidated guest contract must list every surface as closed."""

    def test_contract_audit_has_no_violations(self) -> None:
        audit = guest_contract.assert_no_host_escape(self._contract())

        self.assertEqual(audit.violations, ())
        self.assertTrue(audit.closed)

    def test_each_surface_is_closed_in_the_guest_contract(self) -> None:
        audit = guest_contract.assert_no_host_escape(self._contract())

        for surface in host_escape.resolve_surfaces(self.uid):
            with self.subTest(surface=surface.key, path=surface.path or surface.env_name):
                self._assert_verdict_closed(audit, surface.key, where="contract")

    def test_contract_passes_no_control_path_into_the_guest(self) -> None:
        audit = guest_contract.assert_no_host_escape(self._contract())

        self.assertEqual(audit.exposed_control_paths, ())

    def test_export_set_is_the_single_workspace_export(self) -> None:
        contract = self._contract()

        self.assertEqual(contract.export["host_path"], str(self.workspace))
        self.assertEqual(contract.export["guest_path"], GUEST_WORKSPACE_PATH)
        self.assertEqual(contract.export["extra_exports"], [])
        self.assertEqual(contract.export["sandbox"], "namespace")
        self.assertEqual(tuple(contract.export_host_paths()), (str(self.workspace),))

    def test_guest_roots_exclude_every_control_root(self) -> None:
        contract = self._contract()

        for control in host_escape.GUEST_CONTROL_ROOTS:
            with self.subTest(control=control):
                for root in contract.guest_roots:
                    self.assertFalse(
                        root == control or root.startswith(control.rstrip("/") + "/"),
                        f"guest root {root!r} reaches control root {control!r}",
                    )
        self.assertIn(GUEST_WORKSPACE_PATH, contract.guest_roots)
        self.assertIn("/tmp", contract.guest_roots)

    def test_generated_xml_and_export_pass_their_own_scanners(self) -> None:
        self.assertEqual(
            host_escape.scan_domain_xml(
                self._xml(), allowed_export_paths=(str(self.workspace),)
            ),
            (),
        )
        self.assertEqual(host_escape.scan_export(self.export), ())
        self.assertEqual(host_escape.scan_guest_roots(DEFAULT_GUEST_ROOTS), ())

    def test_rendered_contract_reports_every_surface_closed(self) -> None:
        rendered = guest_contract.render_contract(self._contract())

        for surface in host_escape.resolve_surfaces(self.uid):
            with self.subTest(surface=surface.key):
                self.assertIn(surface.key, rendered)
                self.assertIn("closed=yes", rendered)
        self.assertIn("systemd-run --user: closed=yes", rendered)
        self.assertIn("violations: none", rendered)


class SystemdRunEscapeTests(_ContractCase):
    """The documented ``systemd-run --user`` host escape is structurally closed."""

    def test_documented_systemd_run_escape_is_closed(self) -> None:
        audit = guest_contract.assert_no_host_escape(self._contract())
        verdict = audit.systemd_run

        self.assertEqual(verdict.user_bus_path, f"/run/user/{self.uid}/bus")
        self.assertFalse(verdict.host_bus_bound, "the user bus socket is bound in the XML")
        self.assertFalse(verdict.host_bus_exported, "the user bus socket is exported")
        self.assertFalse(verdict.host_bus_in_env, "DBUS_SESSION_BUS_ADDRESS crosses to the guest")
        self.assertEqual(verdict.host_reach, "none")
        self.assertTrue(verdict.closed, verdict.reason)

    def test_systemd_run_is_refused_not_merely_unavailable(self) -> None:
        contract = self._contract()
        rendered = guest_contract.render_contract(contract)

        self.assertIn("systemd-run --user: closed=yes", rendered)
        self.assertIn(f"/run/user/{self.uid}/bus", rendered)
        # The guest sandbox clears its environment: no D-Bus address is passed.
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", contract.env_names)
        self.assertNotIn("--setenv\x00DBUS_SESSION_BUS_ADDRESS", contract.domain_xml)

    def test_closure_is_not_vacuous_when_the_bus_is_bound(self) -> None:
        xml = self._inject(
            '<channel type="unix">'
            f'<source mode="bind" path="/run/user/{self.uid}/bus"/>'
            '<target type="virtio" name="org.qemu.guest_agent.0"/>'
            "</channel>"
        )
        audit = guest_contract.audit_guest_contract(self._contract(domain_xml=xml))

        self.assertFalse(audit.systemd_run.closed)
        self.assertEqual(audit.systemd_run.host_reach, "host_user_bus")
        self.assertTrue(audit.violations)
        self.assertFalse(audit.closed)


class ClosureDetectabilityTests(_ContractCase):
    """A regression that adds /run, a host socket or a control path MUST fail."""

    def test_added_run_filesystem_export_is_detected(self) -> None:
        xml = self._inject(
            '<filesystem type="mount">'
            '<source dir="/run"/>'
            '<target dir="host-run"/>'
            "</filesystem>"
        )
        contract = self._contract(domain_xml=xml)
        audit = guest_contract.audit_guest_contract(contract)
        codes = {violation.code for violation in audit.violations}

        self.assertIn("filesystem_export_not_allowed", codes)
        self.assertTrue(
            any(code == "host_control_path" for code in codes),
            sorted(codes),
        )
        with self.assertRaises(IsolationError) as ctx:
            guest_contract.assert_no_host_escape(contract)
        self.assertTrue(ctx.exception.reason.startswith("host_escape_surface"))

    def test_added_host_socket_channel_is_detected(self) -> None:
        xml = self._inject(
            '<channel type="unix">'
            f'<source mode="bind" path="/run/user/{self.uid}/bus"/>'
            '<target type="virtio" name="org.qemu.guest_agent.0"/>'
            "</channel>"
        )
        audit = guest_contract.audit_guest_contract(self._contract(domain_xml=xml))
        codes = {violation.code for violation in audit.violations}

        self.assertIn("host_binding", codes)
        self.assertIn("host_control_surface", codes)
        verdict = audit.surface("host_user_dbus")
        assert verdict is not None
        self.assertTrue(verdict.in_domain_xml)
        self.assertFalse(verdict.closed)

    def test_forged_export_to_the_user_runtime_dir_is_detected(self) -> None:
        forged = dict(self.export)
        forged["host_path"] = f"/run/user/{self.uid}"
        audit = guest_contract.audit_guest_contract(self._contract(export=forged))

        self.assertTrue(
            any(v.code == "export_host_path_control" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )
        verdict = audit.surface("host_user_dbus")
        assert verdict is not None
        self.assertTrue(verdict.exported)
        self.assertFalse(verdict.closed)
        self.assertTrue(audit.systemd_run.host_bus_exported)
        self.assertFalse(audit.closed)

    def test_extra_export_at_a_guest_control_path_is_detected(self) -> None:
        extra_dir = self.root / "extra"
        extra_dir.mkdir()
        forged = dict(self.export)
        forged["extra_exports"] = [
            {
                "host_path": str(extra_dir),
                "guest_path": f"/run/user/{self.uid}/bus",
                "readonly": True,
            }
        ]
        audit = guest_contract.audit_guest_contract(self._contract(export=forged))

        self.assertTrue(
            any(v.code == "export_guest_path_control" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )

    def test_guest_control_root_is_detected(self) -> None:
        audit = guest_contract.audit_guest_contract(
            self._contract(guest_roots=("/workspace", "/run"))
        )

        self.assertTrue(
            any(v.code == "guest_root_control" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )

    def test_dbus_env_name_in_the_contract_is_detected(self) -> None:
        forged = dataclasses.replace(
            self._contract(),
            env_names=(*self._contract().env_names, "DBUS_SESSION_BUS_ADDRESS"),
        )
        audit = guest_contract.audit_guest_contract(forged)

        verdict = audit.surface("host_dbus_session_env")
        assert verdict is not None
        self.assertTrue(verdict.in_env)
        self.assertFalse(verdict.closed)
        self.assertTrue(audit.systemd_run.host_bus_in_env)
        self.assertFalse(audit.closed)

    def test_socket_file_export_is_detected(self) -> None:
        sock_path = self.root / "agent.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(str(sock_path))
        forged = dict(self.export)
        forged["host_path"] = str(sock_path)
        audit = guest_contract.audit_guest_contract(self._contract(export=forged))

        self.assertTrue(
            any(v.code == "export_host_path_socket" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )

    def test_passthrough_accessmode_is_detected(self) -> None:
        xml = self._inject(
            '<filesystem type="mount" accessmode="passthrough">'
            f'<source dir="{self.workspace}"/>'
            '<target dir="workspace"/>'
            "</filesystem>"
        )
        audit = guest_contract.audit_guest_contract(self._contract(domain_xml=xml))

        self.assertTrue(
            any(v.code == "virtiofs_accessmode_passthrough" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )

    def test_unrestricted_network_interface_is_detected(self) -> None:
        xml = self._inject('<interface type="bridge"><source bridge="br0"/></interface>')
        audit = guest_contract.audit_guest_contract(self._contract(domain_xml=xml))

        self.assertTrue(
            any(v.code == "network_interface_not_restricted" for v in audit.violations),
            [f"{v.where}/{v.code}" for v in audit.violations],
        )


class ProductionGuardTests(_ContractCase):
    """The closure is enforced by the generators, not only by this test file."""

    def test_build_domain_xml_runs_the_host_escape_scan(self) -> None:
        with mock.patch.object(
            domain_module,
            "assert_domain_xml_clean",
            side_effect=IsolationError("host_escape_surface:test"),
        ) as guard:
            with self.assertRaises(IsolationError):
                build_domain_xml(self.spec)
        guard.assert_called_once()

    def test_resolve_workspace_export_runs_the_export_scan(self) -> None:
        with mock.patch.object(
            workspace_module,
            "assert_export_clean",
            side_effect=IsolationError("host_escape_surface:test"),
        ) as guard:
            with self.assertRaises(IsolationError):
                resolve_workspace_export(
                    self.workspace,
                    policy=WorkspacePolicy(
                        home=self.home, data_root=self.data_root, host_uid=self.uid
                    ),
                )
        guard.assert_called_once()

    def test_workspace_resolver_refuses_control_roots(self) -> None:
        policy = WorkspacePolicy(
            home=self.home, data_root=self.data_root, host_uid=self.uid
        )

        for denied in ("/run", f"/run/user/{self.uid}", "/var/run", "/proc", "/sys", "/dev"):
            with self.subTest(denied=denied):
                with self.assertRaises(IsolationError) as ctx:
                    resolve_workspace_export(denied, policy=policy)
                self.assertTrue(
                    ctx.exception.reason.startswith("sensitive_path_denied:"),
                    ctx.exception.reason,
                )

    def test_no_host_escape_scan_refuses_a_hand_built_run_mount(self) -> None:
        xml = (
            "<domain type='kvm'><name>forged</name><devices>"
            '<filesystem type="mount"><source dir="/run"/><target dir="run"/></filesystem>'
            "</devices></domain>"
        )

        with self.assertRaises(IsolationError) as ctx:
            host_escape.assert_domain_xml_clean(xml, allowed_export_paths=())

        self.assertTrue(ctx.exception.reason.startswith("host_escape_surface"))
        self.assertEqual(ctx.exception.code, "host_escape_surface")


if __name__ == "__main__":
    unittest.main()
