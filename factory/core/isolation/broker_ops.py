"""Typed host-operation catalog: the finite op set and its argument schemas.

Every host operation a guest can ever request is declared here as a
:class:`HostOpSpec`: a host-owned name from the broker seam's
``HOST_OP_REGISTRY`` plus a typed argument schema. The catalog is deliberately
closed - a spec outside the seam registry is refused at construction - and the
argument kinds are deliberately narrow:

* :attr:`ArgKind.NAME` - an ``[a-z][a-z0-9_]*`` token;
* :attr:`ArgKind.CHOICE` - a NAME restricted to the spec's closed choices;
* :attr:`ArgKind.INTEGER` - a bounded integer;
* :attr:`ArgKind.BOOLEAN` - a strict boolean.

There is no free-form string kind, no list, no nested object and no argv: a
prompt-injected guest cannot express ``rm -rf /home`` because no schema can
carry it, and a slash, space, ``~``, ``;`` or ``$`` never validates as a value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

from .broker_seam import HOST_OP_REGISTRY
from .channel import JsonValue
from .errors import IsolationError

__all__ = [
    "BROKER_VERSION",
    "MAX_ARGUMENT_NAME",
    "MAX_OPERATION_NAME",
    "PROBLEM_CHOICE_NOT_ALLOWED",
    "PROBLEM_INVALID_VALUE",
    "PROBLEM_MISSING_ARGUMENT",
    "PROBLEM_OUT_OF_RANGE",
    "PROBLEM_UNKNOWN_ARGUMENT",
    "ArgKind",
    "ArgValue",
    "HostActionError",
    "HostOpAction",
    "HostOpArgument",
    "HostOpArgumentError",
    "HostOpRegistry",
    "HostOpResultError",
    "HostOpSpec",
    "default_host_op_registry",
    "narrow_json_result",
    "safe_argument_label",
    "safe_operation_label",
    "validate_host_op_args",
]

#: Version of the request/response envelope the broker speaks.
BROKER_VERSION: Final[int] = 1
MAX_OPERATION_NAME: Final[int] = 64
MAX_ARGUMENT_NAME: Final[int] = 32

PROBLEM_UNKNOWN_ARGUMENT: Final[str] = "unknown_argument"
PROBLEM_MISSING_ARGUMENT: Final[str] = "missing_argument"
PROBLEM_INVALID_VALUE: Final[str] = "invalid_value"
PROBLEM_CHOICE_NOT_ALLOWED: Final[str] = "choice_not_allowed"
PROBLEM_OUT_OF_RANGE: Final[str] = "out_of_range"

_OPERATION_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_ARGUMENT_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]*$")

type ArgValue = str | int | bool

#: One typed host action: validated arguments in, channel-safe result out.
type HostOpAction = Callable[[Mapping[str, ArgValue]], Mapping[str, JsonValue]]


class HostOpResultError(Exception):
    """An action returned a value that cannot cross the channel."""


class HostActionError(IsolationError):
    """One typed host action failed or refused to run.

    ``code`` is the stable machine reason the broker writes into the response
    and the audit trail; ``executed`` states whether any host command or effect
    was attempted, so an op that fails closed reports ``False``.
    """

    def __init__(self, reason: str, detail: str = "", *, executed: bool = True) -> None:
        super().__init__(reason, detail)
        self.executed = executed


def narrow_json_result(value: object) -> JsonValue:
    """Narrow an action result to the channel's JSON tree, or raise :class:`HostOpResultError`."""
    match value:
        case str() | bool() | int() | float() | None:
            return value
        case list() | tuple():
            return [narrow_json_result(item) for item in value]
        case dict():
            narrowed: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise HostOpResultError("non_string_key")
                narrowed[key] = narrow_json_result(item)
            return narrowed
        case _:
            raise HostOpResultError(type(value).__name__)


class HostOpArgumentError(Exception):
    """One violated schema rule; :attr:`detail` is a stable ``problem:argument`` pair."""

    def __init__(self, problem: str, argument: str) -> None:
        self.problem = problem
        self.argument = argument
        super().__init__(f"{problem}:{argument}")

    @property
    def detail(self) -> str:
        return f"{self.problem}:{self.argument}"


class ArgKind(StrEnum):
    """The closed set of argument kinds; text exists only as a validated NAME."""

    NAME = "name"
    CHOICE = "choice"
    INTEGER = "integer"
    BOOLEAN = "boolean"


@dataclass(frozen=True, slots=True)
class HostOpArgument:
    """One typed argument of one operation."""

    name: str
    kind: ArgKind
    required: bool = False
    choices: frozenset[str] = frozenset()
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        if not _ARGUMENT_PATTERN.match(self.name) or len(self.name) > MAX_ARGUMENT_NAME:
            raise IsolationError("invalid_argument_name", self.name)
        match self.kind:
            case ArgKind.NAME | ArgKind.BOOLEAN:
                if self.choices or self.minimum is not None or self.maximum is not None:
                    raise IsolationError("invalid_argument_spec", f"{self.name}:{self.kind}")
            case ArgKind.CHOICE:
                if not self.choices or self.minimum is not None or self.maximum is not None:
                    raise IsolationError("invalid_argument_spec", f"{self.name}:{self.kind}")
                if any(not _ARGUMENT_PATTERN.match(choice) for choice in self.choices):
                    raise IsolationError("invalid_argument_spec", f"{self.name}:bad_choice")
            case ArgKind.INTEGER:
                if self.choices:
                    raise IsolationError("invalid_argument_spec", f"{self.name}:{self.kind}")
                if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
                    raise IsolationError("invalid_argument_spec", f"{self.name}:bounds")
            case _ as unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class HostOpSpec:
    """One operation: a registry name plus its typed argument schema."""

    op: str
    arguments: tuple[HostOpArgument, ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if not _OPERATION_PATTERN.match(self.op) or len(self.op) > MAX_OPERATION_NAME:
            raise IsolationError("invalid_operation_name", self.op)
        names = [argument.name for argument in self.arguments]
        if len(set(names)) != len(names):
            raise IsolationError("duplicate_argument", self.op)


class HostOpRegistry:
    """Closed :class:`HostOpSpec` catalog, bounded by the seam's op registry."""

    __slots__ = ("_specs",)

    def __init__(self, specs: Iterable[HostOpSpec]) -> None:
        by_op: dict[str, HostOpSpec] = {}
        for spec in specs:
            if spec.op not in HOST_OP_REGISTRY:
                raise IsolationError("unregistered_operation", spec.op)
            if spec.op in by_op:
                raise IsolationError("duplicate_operation", spec.op)
            by_op[spec.op] = spec
        self._specs = by_op

    @property
    def ops(self) -> frozenset[str]:
        return frozenset(self._specs)

    def all_specs(self) -> tuple[HostOpSpec, ...]:
        return tuple(spec for _, spec in sorted(self._specs.items()))

    def spec(self, op: str) -> HostOpSpec | None:
        return self._specs.get(op)

    def __contains__(self, op: object) -> bool:
        return op in self._specs

    def __len__(self) -> int:
        return len(self._specs)


def default_host_op_registry() -> HostOpRegistry:
    """The catalog the host runtime ships; later todos back these with actions."""
    return HostOpRegistry(
        (
            HostOpSpec("runtime.status", summary="read-only broker capability snapshot"),
            HostOpSpec(
                "runtime.restart",
                (HostOpArgument("target", ArgKind.CHOICE, required=True, choices=frozenset({"gateway", "runtime"})),),
                summary="restart one named host runtime unit",
            ),
            HostOpSpec("runtime.update", summary="host-side update through the documented safe path"),
            HostOpSpec(
                "vault.fetch",
                (
                    HostOpArgument("service", ArgKind.NAME, required=True),
                    HostOpArgument("ttl_s", ArgKind.INTEGER, minimum=60, maximum=3600),
                ),
                summary="short-lived secret materialization on the host",
            ),
        )
    )


def validate_host_op_args(spec: HostOpSpec, args: Mapping[str, object]) -> dict[str, ArgValue]:
    """Return the typed arguments of one request, or raise :class:`HostOpArgumentError`."""
    known = {argument.name: argument for argument in spec.arguments}
    for key in args:
        if key not in known:
            raise HostOpArgumentError(PROBLEM_UNKNOWN_ARGUMENT, safe_argument_label(key))
    validated: dict[str, ArgValue] = {}
    for argument in spec.arguments:
        if argument.name not in args:
            if argument.required:
                raise HostOpArgumentError(PROBLEM_MISSING_ARGUMENT, argument.name)
            continue
        validated[argument.name] = _validate_value(argument, args[argument.name])
    return validated


def safe_operation_label(raw: object) -> str:
    """A guest-supplied op name reduced to a token that is safe to echo and audit."""
    if isinstance(raw, str) and len(raw) <= MAX_OPERATION_NAME and _OPERATION_PATTERN.match(raw):
        return raw
    return "<invalid>"


def safe_argument_label(raw: object) -> str:
    """A guest-supplied argument name reduced to a token that is safe to audit."""
    if isinstance(raw, str) and len(raw) <= MAX_ARGUMENT_NAME and _ARGUMENT_PATTERN.match(raw):
        return raw
    return "<invalid>"


def _validate_value(argument: HostOpArgument, raw: object) -> ArgValue:
    match argument.kind:
        case ArgKind.NAME:
            if not isinstance(raw, str) or len(raw) > MAX_ARGUMENT_NAME or not _ARGUMENT_PATTERN.match(raw):
                raise HostOpArgumentError(PROBLEM_INVALID_VALUE, argument.name)
            return raw
        case ArgKind.CHOICE:
            if not isinstance(raw, str) or raw not in argument.choices:
                raise HostOpArgumentError(PROBLEM_CHOICE_NOT_ALLOWED, argument.name)
            return raw
        case ArgKind.INTEGER:
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise HostOpArgumentError(PROBLEM_INVALID_VALUE, argument.name)
            if (argument.minimum is not None and raw < argument.minimum) or (
                argument.maximum is not None and raw > argument.maximum
            ):
                raise HostOpArgumentError(PROBLEM_OUT_OF_RANGE, argument.name)
            return raw
        case ArgKind.BOOLEAN:
            if not isinstance(raw, bool):
                raise HostOpArgumentError(PROBLEM_INVALID_VALUE, argument.name)
            return raw
        case _ as unreachable:
            assert_never(unreachable)
