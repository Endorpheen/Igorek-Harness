import os
import subprocess
from pathlib import Path

import pytest

from factory.cli import _build_parser


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "factory/scripts"


def test_legacy_command_removed_and_current_commands_retained():
    parser = _build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert "monitor" not in choices
    assert {"api", "worker", "web", "mobile", "run", "chat", "gateway", "telegram"} <= choices.keys()
    assert not {"router", "orchestrator"} & choices.keys()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["monitor"])
    assert error.value.code == 2
    assert parser.parse_args(["web", "--port", "5177"]).port == 5177


def test_no_legacy_module_unit_or_launch_dependencies():
    assert not (ROOT / "factory/web_monitor_fixed.py").exists()
    assert not (ROOT / "factory/deploy/agentfactory-monitor.service").exists()
    for name in ("run_local.sh", "run_lan.sh", "smoke_auth.sh", "smoke_p1.sh", "update_safe.sh",
                 "backup_restore.sh", "preflight_mini_pc.sh", "firewall_allow_main_pc.sh"):
        source = (SCRIPTS / name).read_text()
        assert "web_monitor_fixed" not in source, name
        assert "MONITOR_PORT" not in source, name
        assert "agentfactory-monitor.service" not in source, name
    assert "agentfactory-monitor" not in (ROOT / "factory/deploy/agentfactory.target").read_text()


def executable(path, content):
    path.write_text("#!/bin/sh\n" + content)
    path.chmod(0o700)


@pytest.fixture
def shell_env(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": str(commands) + ":/usr/bin:/bin", "QA_CALLS": str(log)}
    return commands, log, env


@pytest.mark.parametrize("name", ["run_local.sh", "run_lan.sh"])
def test_launch_scripts_run_only_api_and_keep_configuration(tmp_path, shell_env, name):
    commands, log, env = shell_env
    executable(commands / "python3", 'printf "%s|%s|%s\\n" "$*" "$FACTORY_API_KEY" "$MODE" >> "$QA_CALLS"\n')
    config = tmp_path / "fixture.env"
    config.write_text("FACTORY_API_KEY=fixture-key\nFACTORY_API_PORT=12345\nMODE=local\n")
    result = subprocess.run(["bash", str(SCRIPTS / name), str(config)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == ["factory_api.py --port 12345|fixture-key|local"]


def privileged_fixture(tmp_path, name):
    source = (SCRIPTS / name).read_text()
    source = source.replace('SYSTEMD_DIR="/etc/systemd/system"', 'SYSTEMD_DIR="' + str(tmp_path / "units") + '"')
    source = source.replace('if [[ $EUID -ne 0 ]]; then', 'if false; then', 1)
    target = tmp_path / name
    target.write_text(source)
    return target


def test_systemd_install_migrates_old_unit_without_starting_monitor(tmp_path, shell_env):
    commands, log, env = shell_env
    executable(commands / "systemctl", 'printf "%s\\n" "$*" >> "$QA_CALLS"\n')
    units = tmp_path / "units"
    units.mkdir()
    (units / "agentfactory-monitor.service").write_text("retired fixture\n")
    config = tmp_path / "fixture.env"
    config.write_text("FACTORY_API_KEY=fixture-key\n")
    script = privileged_fixture(tmp_path, "install_systemd.sh")
    result = subprocess.run(["bash", str(script), str(ROOT), str(config)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (units / "agentfactory-monitor.service").exists()
    assert (units / "agentfactory-api.service").read_bytes() == (ROOT / "factory/deploy/agentfactory-api.service").read_bytes()
    assert "agentfactory-monitor" not in (units / "agentfactory.target").read_text()
    calls = log.read_text().splitlines()
    assert calls[0] == "disable --now agentfactory-monitor.service"
    assert "enable --now agentfactory.target" in calls
    assert not any("monitor" in call and not call.startswith("disable") for call in calls)


def test_firewall_script_retains_api_rules_and_never_opens_monitor(tmp_path, shell_env):
    commands, log, env = shell_env
    executable(commands / "ufw", 'printf "%s\\n" "$*" >> "$QA_CALLS"\nprintf "Status: active\\n"\n')
    script = privileged_fixture(tmp_path, "firewall_allow_main_pc.sh")
    env.update(API_PORT="12345", MAIN_PC_IP="192.0.2.10")
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "allow proto tcp from 192.0.2.10 to any port 12345" in calls
    assert "deny 12345/tcp" in calls
    assert "9999" not in calls


@pytest.mark.parametrize("name", ["run_local.sh", "run_lan.sh", "install_systemd.sh", "uninstall_systemd.sh",
                                    "smoke_auth.sh", "smoke_p1.sh", "update_safe.sh", "backup_restore.sh",
                                    "preflight_mini_pc.sh", "firewall_allow_main_pc.sh"])
def test_modified_deployment_scripts_parse(name):
    result = subprocess.run(["bash", "-n", str(SCRIPTS / name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
