"""Non-secret Git route carried from the host into one guest dispatch."""
from __future__ import annotations

import ipaddress
import re
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit
import os


def validate_repository_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("git_repository_url_invalid")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or not parsed.hostname or not parsed.port
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", parsed.path)
            or any(part in {".", ".."} for part in parsed.path.split("/")[1:])):
        raise ValueError("git_repository_url_invalid")
    # The operator pins an address, not a guest-selected DNS name.
    ipaddress.ip_address(parsed.hostname)
    return value


@dataclass(frozen=True)
class GitGatewayRoute:
    repository_url: str
    gateway_url: str

    def to_wire(self) -> dict[str, str]:
        return {"repository_url": self.repository_url, "gateway_url": self.gateway_url}

    @classmethod
    def from_wire(cls, value) -> GitGatewayRoute:
        if not isinstance(value, dict) or set(value) != {"repository_url", "gateway_url"}:
            raise ValueError("git_gateway_route_invalid")
        repository, gateway = value["repository_url"], value["gateway_url"]
        if not isinstance(repository, str) or not isinstance(gateway, str):
            raise ValueError("git_gateway_route_invalid")
        validate_repository_url(repository)
        parsed = urlsplit(gateway)
        if (parsed.scheme != "http" or parsed.hostname != "10.0.2.2" or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or not re.fullmatch(r"/[0-9a-f]{64}", parsed.path)):
            raise ValueError("git_gateway_route_invalid")
        return cls(repository, gateway)


@contextmanager
def guest_git_environment(route: GitGatewayRoute | None):
    """Rewrite only the granted remote; do not persist credentials or remotes."""
    if route is None:
        yield
        return
    updates = {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "url." + route.gateway_url + ".insteadOf",
        "GIT_CONFIG_VALUE_0": route.repository_url,
        "GIT_CONFIG_KEY_1": "http.followRedirects",
        "GIT_CONFIG_VALUE_1": "false",
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
