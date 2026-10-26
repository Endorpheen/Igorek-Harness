"""Run each Python test file in a fresh process with isolated local data."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]


def discover(paths: list[str]) -> list[Path]:
    files = set()
    for name in paths:
        path = (REPO / name).resolve()
        if not path.is_relative_to(REPO):
            raise ValueError("Test paths must be inside the repository")
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(path.rglob("test_*.py"))
        else:
            raise ValueError(f"Test path does not exist: {name}")
    if not files:
        raise ValueError("No tests discovered")
    return sorted(files)


def run_file(path: Path, index: int, output: Path, timeout: int, coverage: bool) -> dict:
    output = output.resolve()
    name = f"{index:04d}-{path.stem}"
    root = output / name
    root.mkdir()
    for directory in ("home", "data", "env"):
        (root / directory).mkdir()
    env = {key: value for key, value in os.environ.items() if key in {
        "PATH", "LANG", "LC_ALL", "DISPLAY", "XAUTHORITY", "SYSTEMROOT", "WINDIR",
        "COMSPEC", "PATHEXT", "TEMP", "TMP", "ANDROID_HOME", "JAVA_HOME",
    }}
    env.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "IGOREK_FACTORY_HOME": str(root / "home"),
        "FACTORY_DATA_DIR": str(root / "data"),
        "FACTORY_ENV_FILES_DIR": str(root / "env"),
        "IGOREK_FACTORY_REPO": str(REPO), "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1", "TERM": "dumb", "TZ": "UTC",
        "COVERAGE_FILE": str(output / f".coverage.{name}"),
    })
    xml = root / "results.xml"
    command = [sys.executable, "-u", "-m", "pytest", str(path), "-v", "--tb=short",
               "--timeout=60", "-o", "faulthandler_timeout=45", f"--junitxml={xml}"]
    if coverage:
        command += ["--cov=factory", "--cov=desktop",
                    "--cov-branch", "--cov-report="]
    started = time.monotonic()
    with (root / "output.log").open("w") as log:
        process = subprocess.Popen(command, cwd=REPO, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=os.name == "posix")
        try:
            status = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
            if os.name == "posix":
                # A terminated parent can leave children that ignored SIGTERM.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            status = 124
            log.write(f"\nTest file exceeded {timeout} seconds.\n")
    result = {"file": path.relative_to(REPO).as_posix(), "exit_code": status,
              "seconds": round(time.monotonic() - started, 2),
              "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
              "collected": 0, "subtests_passed": 0}
    if xml.exists():
        for suite in ET.parse(xml).getroot().iter("testsuite"):
            tests, failures, errors, skipped = (int(suite.get(k, 0)) for k in
                                                ("tests", "failures", "errors", "skipped"))
            result["passed"] += tests - failures - errors - skipped
            result["failed"] += failures
            result["errors"] += errors
            result["skipped"] += skipped
    text = (root / "output.log").read_text(errors="replace")
    collected = re.search(r"collected (\d+) items?", text)
    result["collected"] = int(collected[1]) if collected else 0
    summaries = [line for line in text.splitlines() if line.startswith("=")]
    if summaries:
        summary = summaries[-1]
        for key, label in (("passed", "passed"), ("failed", "failed"),
                           ("errors", "errors?"), ("skipped", "skipped"),
                           ("subtests_passed", "subtests passed")):
            match = re.search(r"\b(\d+) " + label + r"\b", summary)
            result[key] = int(match[1]) if match else 0
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["factory/tests", "tests"])
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--file-timeout", type=int, default=300)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.jobs < 1 or args.file_timeout < 1:
        parser.error("Job count and timeout must be positive")
    files = discover(args.paths)
    output = (args.output or Path(tempfile.mkdtemp(prefix="igorek-tests-"))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    print(f"Running {len(files)} files with {args.jobs} processes; evidence: {output}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        tasks = [pool.submit(run_file, path, index, output, args.file_timeout, args.coverage)
                 for index, path in enumerate(files)]
        for task in as_completed(tasks):
            result = task.result()
            results.append(result)
            marker = "PASS" if result["exit_code"] == 0 else "FAIL"
            print(f"[{len(results)}/{len(files)}] {marker} {result['file']} "
                  f"({result['passed']} passed, {result['failed']} failed, "
                  f"{result['errors']} errors, {result['skipped']} skipped)", flush=True)
    totals = {key: sum(result[key] for result in results)
              for key in ("collected", "passed", "failed", "errors", "skipped", "subtests_passed")}
    (output / "summary.json").write_text(json.dumps({"totals": totals, "files": results}, indent=2) + "\n")
    if args.coverage:
        from coverage import Coverage
        measured = Coverage(data_file=str(output / ".coverage"), branch=True)
        measured.combine(data_paths=[str(output)], keep=True)
        measured.save()
        measured.json_report(outfile=str(output / "coverage.json"))
        measured.xml_report(outfile=str(output / "coverage.xml"))
        measured.report(skip_empty=True)
    print(json.dumps(totals), flush=True)
    return int(any(result["exit_code"] != 0 for result in results))


if __name__ == "__main__":
    raise SystemExit(main())
