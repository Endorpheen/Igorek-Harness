"""CLI commands for the mobile bridge: `igorek-factory mobile <serve|pair|devices|revoke>`."""

from __future__ import annotations

import os
import time
from typing import Any

from .server import BridgeConfig, MobileBridge, serve_forever
from .store import MobileStore


def _apply_data_dir(data_dir: str | None) -> None:
    if data_dir and "FACTORY_DATA_DIR" not in os.environ and "IGOREK_FACTORY_HOME" not in os.environ:
        os.environ["FACTORY_DATA_DIR"] = data_dir


def _format_timestamp(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(value)))
    except (OverflowError, OSError, ValueError):
        return "-"


def _print_pairing(payload: dict[str, Any]) -> None:
    print("Код сопряжения нового устройства:")
    print()
    print(f"    {payload['code']}")
    print()
    print(f"Действует до: {_format_timestamp(payload['expires_at'])} (одноразовый).")
    print("Введите его в Android-приложении → «Добавить устройство».")


def _print_devices(devices: list[dict[str, Any]]) -> None:
    if not devices:
        print("Сопряжённых устройств нет. Выполните: igorek-factory mobile pair")
        return
    print(f"{'device_id':38}  {'name':18}  {'platform':9}  {'created':16}  {'last seen':16}  revoked")
    for device in devices:
        print(
            f"{device['device_id']:38}  {str(device['name'])[:18]:18}  "
            f"{str(device['platform'])[:9]:9}  {_format_timestamp(device['created_at']):16}  "
            f"{_format_timestamp(device['last_seen_at']):16}  "
            f"{'yes' if device['revoked_at'] else 'no'}"
        )


def run_mobile_command(args: Any) -> int:
    _apply_data_dir(getattr(args, "data_dir", None))
    command = str(getattr(args, "mobile_command", "") or "")
    store = MobileStore(db_path=None)

    if command == "pair":
        _print_pairing(store.create_pairing_code(ttl_s=int(getattr(args, "ttl", 300)), created_by="cli"))
        return 0

    if command == "devices":
        _print_devices(store.list_devices(include_revoked=True))
        return 0

    if command == "revoke":
        device_id = str(getattr(args, "device_id", "") or "")
        if store.revoke_device(device_id):
            print(f"Доступ устройства {device_id} отозван.")
            return 0
        print(f"Активное устройство не найдено: {device_id}")
        return 1

    if command == "serve":
        config = BridgeConfig(
            host=str(getattr(args, "host", "127.0.0.1")),
            port=int(getattr(args, "port", 47821)),
            tls_cert=str(getattr(args, "tls_cert", "") or ""),
            tls_key=str(getattr(args, "tls_key", "") or ""),
            profile=str(getattr(args, "profile", "") or ""),
            workdir=str(getattr(args, "workdir", "") or ""),
            poll_interval_s=float(getattr(args, "poll_interval", 1.5)),
        )
        return serve_forever(MobileBridge(config=config))

    print("Неизвестная подкоманда mobile. Доступно: serve, pair, devices, revoke.")
    return 2
