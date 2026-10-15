"""One-shot mobile approval for a local sudo command."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import shlex
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from .runtime import get_data_root

REQUEST_TTL_S = 300
MAX_SECRET_BYTES = 16_384


class SudoRequestError(Exception):
    pass


def _database_path() -> Path:
    root = get_data_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "sudo_requests.sqlite3"
    if not path.exists():
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            # Another worker may initialize the same broker database between
            # the existence check and the exclusive create.
            pass
        else:
            os.close(descriptor)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS requests ("
        "request_id TEXT PRIMARY KEY, argv_json TEXT NOT NULL, cwd TEXT NOT NULL, "
        "socket_path TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, "
        "status TEXT NOT NULL, returncode INTEGER)"
    )
    connection.commit()
    return connection


@contextmanager
def _db():
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def create_request(argv: Sequence[str], cwd: str, socket_path: str) -> str:
    if not argv or any(not isinstance(part, str) or "\x00" in part for part in argv):
        raise SudoRequestError("command must be a non-empty argv list")
    if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise SudoRequestError("cwd must be an existing absolute directory")
    request_id = uuid.uuid4().hex
    now = int(time.time())
    with _db() as connection:
        connection.execute(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL)",
            (request_id, json.dumps(list(argv)), cwd, socket_path, now, now + REQUEST_TTL_S),
        )
    return request_id


def pending_requests() -> list[dict[str, Any]]:
    with _db() as connection:
        rows = connection.execute(
            "SELECT * FROM requests WHERE status = 'pending' AND expires_at > ? "
            "ORDER BY created_at DESC LIMIT 50",
            (int(time.time()),),
        ).fetchall()
    return [
        {
            "kind": "sudo",
            "request_id": row["request_id"],
            "task_id": "",
            "session_id": "",
            "tool": "sudo",
            "target": shlex.join(json.loads(row["argv_json"])),
            "input": None,
            "signature": "",
            "reason": "Выполнить на основном ПК с правами администратора",
            "dangerous": True,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
        for row in rows
    ]


def finish_request(request_id: str, status: str, returncode: int | None = None) -> None:
    with _db() as connection:
        connection.execute(
            "UPDATE requests SET status = ?, returncode = ? WHERE request_id = ?",
            (status, returncode, request_id),
        )


def deliver_decision(request_id: str, decision: str, password: str = "") -> dict[str, Any]:
    if decision not in {"allow_once", "reject"}:
        raise SudoRequestError("decision must be allow_once or reject")
    if decision == "allow_once" and (not password or len(password.encode("utf-8")) > MAX_SECRET_BYTES):
        raise SudoRequestError("password is required and must be at most 16 KiB")
    now = int(time.time())
    with _db() as connection:
        row = connection.execute(
            "SELECT socket_path FROM requests WHERE request_id = ? AND status = 'pending' "
            "AND expires_at > ?",
            (request_id, now),
        ).fetchone()
        if row is None:
            raise SudoRequestError("request is no longer pending")
        claimed = connection.execute(
            "UPDATE requests SET status = 'delivering' WHERE request_id = ? "
            "AND status = 'pending' AND expires_at > ?",
            (request_id, now),
        )
        if claimed.rowcount != 1:
            raise SudoRequestError("request is no longer pending")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(5)
            peer.connect(str(row["socket_path"]))
            payload = json.dumps(
                {"request_id": request_id, "decision": decision, "password": password}
            ).encode("utf-8")
            peer.sendall(payload)
            peer.shutdown(socket.SHUT_WR)
    except OSError as exc:
        finish_request(request_id, "delivery_failed")
        raise SudoRequestError(f"local command is unavailable: {exc.strerror or type(exc).__name__}") from exc
    return {"request_id": request_id, "status": "delivered"}


def _receive_decision(listener: socket.socket, request_id: str) -> dict[str, str]:
    listener.settimeout(REQUEST_TTL_S)
    peer, _ = listener.accept()
    with peer:
        credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        peer_uid = int.from_bytes(credentials[4:8], sys.byteorder)
        if peer_uid != os.getuid():
            raise SudoRequestError("decision came from a different user")
        chunks: list[bytes] = []
        total = 0
        while True:
            part = peer.recv(4096)
            if not part:
                break
            total += len(part)
            if total > MAX_SECRET_BYTES + 512:
                raise SudoRequestError("decision is too large")
            chunks.append(part)
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    if payload.get("request_id") != request_id:
        raise SudoRequestError("decision request ID mismatch")
    return payload


def run_request(argv: Sequence[str], *, cwd: str | None = None, timeout_s: int = 600) -> int:
    working_dir = str(Path(cwd or os.getcwd()).resolve())
    runtime = Path(f"/run/user/{os.getuid()}")
    if not runtime.is_dir():
        runtime = Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(prefix="igorek-sudo-", dir=runtime) as directory:
        socket_path = str(Path(directory) / "approval.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(socket_path)
            listener.listen(1)
            request_id = create_request(argv, working_dir, socket_path)
            print(f"SUDO_REQUEST {request_id}: {shlex.join(argv)}", flush=True)
            print("Ожидаю подтверждение и пароль в Igorek Remote (до 5 минут).", flush=True)
            try:
                payload = _receive_decision(listener, request_id)
            except (OSError, ValueError, SudoRequestError) as exc:
                finish_request(request_id, "failed")
                print(f"SUDO_REQUEST_FAILED: {exc}", file=sys.stderr)
                return 1
            if payload.get("decision") == "reject":
                finish_request(request_id, "rejected")
                print("SUDO_REJECTED", flush=True)
                return 1
            password = payload.get("password")
            if payload.get("decision") != "allow_once" or not isinstance(password, str) or not password:
                finish_request(request_id, "failed")
                print("SUDO_REQUEST_FAILED: invalid decision", file=sys.stderr)
                return 1
            finish_request(request_id, "running")
            try:
                subprocess.run(["sudo", "-k"], check=False, capture_output=True)
                completed = subprocess.run(
                    ["sudo", "-S", "-p", "", "--", *argv],
                    input=password + "\n",
                    text=True,
                    capture_output=True,
                    cwd=working_dir,
                    timeout=timeout_s,
                    check=False,
                )
                if completed.stdout:
                    sys.stdout.write(completed.stdout)
                if completed.stderr:
                    sys.stderr.write(completed.stderr)
                finish_request(request_id, "completed", completed.returncode)
                return completed.returncode
            except subprocess.TimeoutExpired:
                finish_request(request_id, "timeout")
                print("SUDO_TIMED_OUT", file=sys.stderr)
                return 124
            finally:
                del password
                subprocess.run(["sudo", "-k"], check=False, capture_output=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Request one sudo command in Igorek Remote")
    parser.add_argument("--timeout", type=int, default=600, help="command timeout in seconds")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required after --")
    return run_request(command, timeout_s=max(1, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
