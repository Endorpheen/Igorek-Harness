"""Foreground chat launcher installed as the real ``igorek`` command."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    daemon_action = None
    if argv[:2] == ["app-server", "daemon"]:
        if len(argv) < 3 or argv[2] not in {"start", "stop", "restart", "version"}:
            print("Usage: igorek app-server daemon [start|stop|restart|version] [--data-dir PATH]", file=sys.stderr)
            return 2
        daemon_action, argv = argv[2], argv[3:]
    parser = argparse.ArgumentParser(prog="igorek", description="Start Igorek chat in this terminal.")
    parser.add_argument("--profile", help="Configured model profile; defaults to the last selection")
    parser.add_argument("--data-dir", help="CLI data directory")
    parser.add_argument("--no-alt-screen", action="store_true", help="Use the terminal's normal scrollback")
    parser.add_argument("--resume", nargs="?", const="", help="Resume an existing session by its ID")
    args = parser.parse_args(argv)
    root = Path(args.data_dir or os.environ.get("IGOREK_FACTORY_HOME")
                or os.environ.get("FACTORY_DATA_DIR") or Path.home() / ".igorek-agent-factory").expanduser().resolve()
    os.environ["IGOREK_FACTORY_HOME"] = str(root)
    os.environ["FACTORY_DATA_DIR"] = str(root)
    try:
        if daemon_action:
            from factory.core.cli_daemon import lifecycle
            print(json.dumps(lifecycle(daemon_action), ensure_ascii=False))
            return 0
        from factory.core.cli_bootstrap import _prompt_choice, init_cli_environment
        from factory.core.runtime import get_profile_config, get_profiles_dir, list_profile_names
        from factory.core.worker_presence import private_directory, write_private
        directory = private_directory(root / "run" / "cli")
        state = directory / "selection.json"
        names = list_profile_names(get_profiles_dir())
        if not names:
            if not sys.stdin.isatty():
                raise ValueError("No configured profiles. Run igorek-factory init in a terminal first.")
            result = init_cli_environment(data_dir=str(root))
            names = result["profile_names"]
        previous = None
        if state.exists():
            previous = json.loads(state.read_text()).get("profile")
        if args.resume:
            from factory.core.cli_chat import _read_session_meta, _session_path
            previous = (_read_session_meta(_session_path(args.resume)) or {}).get("profile", previous)
        profile = args.profile or (previous if previous in names else None)
        if profile is None:
            if len(names) == 1:
                profile = names[0]
            elif sys.stdin.isatty():
                profile = _prompt_choice("Выберите профиль Игорька", [(name, name) for name in names], default=names[0])
            else:
                raise ValueError("Choose a configured profile with --profile: " + ", ".join(names))

        from factory.core.cli_session_runtime import SessionRuntime
        from factory.core.cli_chat_rich import run_rich_chat
        from factory.core.cli_chat import generate_session_name, _session_path
        from factory.core.cli_run import _bootstrap_stack_env
        _bootstrap_stack_env()
        session = args.resume or generate_session_name()
        if args.resume is not None and (not args.resume or not _session_path(args.resume).is_file()):
            raise ValueError("Use --resume with an existing session ID")
        with SessionRuntime() as runtime:
            def select(name):
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) or name not in list_profile_names(get_profiles_dir()):
                    raise ValueError("Unknown configured profile: " + name)
                get_profile_config(name)
                runtime.select(name)
                write_private(state, {"profile": name})
            select(profile)
            return run_rich_chat(profile=profile, session=session, data_dir=str(root),
                                 on_profile_change=select, before_turn=runtime.select,
                                 exit_on_interrupt=True, submit_task=runtime.submit,
                                 start_fullscreen=False if args.no_alt_screen else None,
                                 classic_style=os.environ.get("IGOREK_TUI_CLASSIC_STYLE", "modern"))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
