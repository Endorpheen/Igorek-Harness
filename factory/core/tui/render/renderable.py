"""Renderable trait and built-in renderables — Python port of codex-rs/tui/src/render/renderable.rs.

Codex uses ratatui's 2D Buffer; this port uses ``list[str]`` (one ANSI-styled
line per element). The public surface mirrors the Rust file 1:1:
    Rect, Insets, Renderable (ABC), RenderableItem, ColumnRenderable,
    FlexRenderable, RowRenderable, InsetRenderable, RenderableExt.

Rust blanket impls for ``&str`` / ``String`` / ``Span`` / ``Line`` /
``Paragraph`` are mapped to the closest Python idioms (plain ``str`` /
``ANSIBlock``-like wrappers); the ``Option<R>`` blanket is preserved.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple, Union


@dataclass
class Insets:
    """Inset rectangle padding — port of codex::render::Insets."""
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @classmethod
    def tlbr(cls, top: int, left: int, bottom: int, right: int) -> "Insets":
        return cls(left=left, top=top, right=right, bottom=bottom)

    @classmethod
    def vh(cls, v: int, h: int) -> "Insets":
        return cls(left=h, top=v, right=h, bottom=v)


@dataclass
class Rect:
    """Terminal rectangle — port of ratatui::layout::Rect."""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    def bottom(self) -> int:
        return self.y + self.height

    def is_empty(self) -> bool:
        return self.width == 0 or self.height == 0

    def intersection(self, other: "Rect") -> "Rect":
        x1 = max(self.x, other.x)
        y1 = max(self.y, other.y)
        x2 = min(self.x + self.width, other.x + other.width)
        y2 = min(self.y + self.height, other.y + other.height)
        if x2 <= x1 or y2 <= y1:
            return Rect(0, 0, 0, 0)
        return Rect(x1, y1, x2 - x1, y2 - y1)

    def inset(self, insets: Insets) -> "Rect":
        horizontal = insets.left + insets.right
        vertical = insets.top + insets.bottom
        return Rect(
            x=self.x + insets.left,
            y=self.y + insets.top,
            width=max(0, self.width - horizontal),
            height=max(0, self.height - vertical),
        )


class Renderable(ABC):
    """Port of codex::render::renderable::Renderable trait."""

    @abstractmethod
    def render(self, area: Rect, buf: List[str]) -> None: ...

    def desired_height(self, width: int) -> int:
        return 0

    def cursor_pos(self, _area: Rect) -> Optional[Tuple[int, int]]:
        return None

    def cursor_style(self, _area: Rect) -> str:
        return "default"


class _UnitRenderable(Renderable):
    """Blanket impl for ``()`` — renders nothing, height 0."""

    def render(self, _area: Rect, _buf: List[str]) -> None:
        pass

    def desired_height(self, _width: int) -> int:
        return 0


for _u in ((),):
    pass


def _str_renderable_render(self, area: Rect, buf: List[str]) -> None:
    if area.height <= 0 or area.width <= 0:
        return
    text = str(self)
    buf.append(text)


def _str_desired_height(self, _width: int) -> int:
    return 1


class _StringRenderable(Renderable):
    """Wrapper that gives ``str`` the ``Renderable`` interface — port of
    ``impl Renderable for &str`` and ``impl Renderable for String``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def render(self, area: Rect, buf: List[str]) -> None:
        if area.height <= 0 or area.width <= 0:
            return
        buf.append(self._text)

    def desired_height(self, _width: int) -> int:
        return 1


class _SpanRenderable(Renderable):
    """Port of ``impl Renderable for Span<'a>`` — single styled line."""

    def __init__(self, text: str) -> None:
        self._text = text

    def render(self, area: Rect, buf: List[str]) -> None:
        if area.height <= 0 or area.width <= 0:
            return
        buf.append(self._text)

    def desired_height(self, _width: int) -> int:
        return 1


class _LineRenderable(Renderable):
    """Port of ``impl Renderable for Line<'a>`` — single line."""

    def __init__(self, text: str) -> None:
        self._text = text

    def render(self, area: Rect, buf: List[str]) -> None:
        if area.height <= 0 or area.width <= 0:
            return
        buf.append(self._text)

    def desired_height(self, _width: int) -> int:
        return 1


class _ParagraphRenderable(Renderable):
    """Port of ``impl Renderable for Paragraph<'a>`` — multi-line block."""

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = list(lines)

    def render(self, area: Rect, buf: List[str]) -> None:
        if area.height <= 0 or area.width <= 0:
            return
        for line in self._lines[: area.height]:
            buf.append(line)

    def desired_height(self, width: int) -> int:
        if width <= 0:
            return len(self._lines)
        return len(self._lines)


@dataclass
class RenderableItem:
    """Owns-or-borrows a Renderable — port of codex::RenderableItem<'a>."""

    _renderable: Renderable

    def render(self, area: Rect, buf: List[str]) -> None:
        self._renderable.render(area, buf)

    def desired_height(self, width: int) -> int:
        return self._renderable.desired_height(width)

    def cursor_pos(self, area: Rect) -> Optional[Tuple[int, int]]:
        return self._renderable.cursor_pos(area)

    def cursor_style(self, area: Rect) -> str:
        return self._renderable.cursor_style(area)

    def inset(self, insets: Insets) -> "RenderableItem":
        return RenderableItem(InsetRenderable(self, insets))


def renderable_item_from(r: Renderable) -> RenderableItem:
    return RenderableItem(r)


class ColumnRenderable(Renderable):
    """Lays children out in a column — port of codex::ColumnRenderable<'a>."""

    def __init__(self) -> None:
        self._children: List[RenderableItem] = []

    @classmethod
    def with_children(cls, children: Iterable[RenderableItem]) -> "ColumnRenderable":
        c = cls()
        c._children = list(children)
        return c

    def push(self, child: Renderable) -> None:
        self._children.append(RenderableItem(child))

    def render(self, area: Rect, buf: List[str]) -> None:
        y = area.y
        for child in self._children:
            child_height = child.desired_height(area.width)
            child_area = Rect(area.x, y, area.width, child_height).intersection(area)
            if not child_area.is_empty():
                child.render(child_area, buf)
            y += child_area.height

    def desired_height(self, width: int) -> int:
        return sum(c.desired_height(width) for c in self._children)

    def cursor_pos(self, area: Rect) -> Optional[Tuple[int, int]]:
        y = area.y
        for child in self._children:
            child_area = Rect(area.x, y, area.width, child.desired_height(area.width)).intersection(area)
            if not child_area.is_empty():
                pos = child.cursor_pos(child_area)
                if pos is not None:
                    return pos
            y += child_area.height
        return None

    def cursor_style(self, area: Rect) -> str:
        y = area.y
        for child in self._children:
            child_area = Rect(area.x, y, area.width, child.desired_height(area.width)).intersection(area)
            if not child_area.is_empty() and child.cursor_pos(child_area) is not None:
                return child.cursor_style(child_area)
            y += child_area.height
        return "default"


class FlexChild:
    """One entry of a ``FlexRenderable`` — port of codex::FlexChild<'a>."""

    def __init__(self, flex: int, child: RenderableItem) -> None:
        self.flex = flex
        self.child = child


class FlexRenderable(Renderable):
    """Column with flex factors — port of codex::FlexRenderable<'a>.

    Loosely inspired by Flutter's Flex widget.
    """

    def __init__(self) -> None:
        self._children: List[FlexChild] = []

    def push(self, flex: int, child: Renderable) -> None:
        self._children.append(FlexChild(flex, RenderableItem(child)))

    def allocate(self, area: Rect) -> List[Rect]:
        allocated_rects: List[Rect] = []
        child_sizes = [0] * len(self._children)
        allocated_size = 0
        total_flex = 0
        max_size = area.height
        last_flex_child_idx = 0

        for i, fc in enumerate(self._children):
            if fc.flex > 0:
                total_flex += fc.flex
                last_flex_child_idx = i
            else:
                h = min(
                    fc.child.desired_height(area.width),
                    max(0, max_size - allocated_size),
                )
                child_sizes[i] = h
                allocated_size += h

        free_space = max(0, max_size - allocated_size)
        allocated_flex_space = 0
        if total_flex > 0 and free_space > 0:
            space_per_flex = free_space // total_flex
            for i, fc in enumerate(self._children):
                if fc.flex > 0:
                    max_child_extent = (
                        free_space - allocated_flex_space
                        if i == last_flex_child_idx
                        else space_per_flex * fc.flex
                    )
                    child_size = min(
                        fc.child.desired_height(area.width),
                        max_child_extent,
                    )
                    child_sizes[i] = child_size
                    allocated_flex_space += child_size

        y = area.y
        for size in child_sizes:
            allocated_rects.append(Rect(area.x, y, area.width, size))
            y += size
        return allocated_rects

    def render(self, area: Rect, buf: List[str]) -> None:
        rects = self.allocate(area)
        for rect, fc in zip(rects, self._children):
            fc.child.render(rect, buf)

    def desired_height(self, width: int) -> int:
        rects = self.allocate(Rect(0, 0, width, 65535))
        if not rects:
            return 0
        return rects[-1].bottom()

    def cursor_pos(self, area: Rect) -> Optional[Tuple[int, int]]:
        rects = self.allocate(area)
        for rect, fc in zip(rects, self._children):
            pos = fc.child.cursor_pos(rect)
            if pos is not None:
                return pos
        return None

    def cursor_style(self, area: Rect) -> str:
        rects = self.allocate(area)
        for rect, fc in zip(rects, self._children):
            if fc.child.cursor_pos(rect) is not None:
                return fc.child.cursor_style(rect)
        return "default"


class RowRenderable(Renderable):
    """Lays children out in a row with fixed widths — port of codex::RowRenderable<'a>."""

    def __init__(self) -> None:
        self._children: List[Tuple[int, RenderableItem]] = []

    def push(self, width: int, child: Renderable) -> None:
        self._children.append((width, RenderableItem(child)))

    def render(self, area: Rect, buf: List[str]) -> None:
        x = area.x
        for width, child in self._children:
            available_width = max(0, area.width - (x - area.x))
            child_area = Rect(x, area.y, min(width, available_width), area.height)
            if child_area.is_empty():
                break
            child.render(child_area, buf)
            x += min(width, available_width)

    def desired_height(self, width: int) -> int:
        max_height = 0
        width_remaining = width
        for child_width, child in self._children:
            w = min(child_width, width_remaining)
            if w == 0:
                break
            h = child.desired_height(w)
            if h > max_height:
                max_height = h
            width_remaining = max(0, width_remaining - w)
        return max_height

    def cursor_pos(self, area: Rect) -> Optional[Tuple[int, int]]:
        x = area.x
        for width, child in self._children:
            available_width = max(0, area.width - (x - area.x))
            child_area = Rect(x, area.y, min(width, available_width), area.height)
            if not child_area.is_empty():
                pos = child.cursor_pos(child_area)
                if pos is not None:
                    return pos
            x += min(width, available_width)
        return None

    def cursor_style(self, area: Rect) -> str:
        x = area.x
        for width, child in self._children:
            available_width = max(0, area.width - (x - area.x))
            child_area = Rect(x, area.y, min(width, available_width), area.height)
            if not child_area.is_empty() and child.cursor_pos(child_area) is not None:
                return child.cursor_style(child_area)
            x += min(width, available_width)
        return "default"


@dataclass
class InsetRenderable(Renderable):
    """Inset wrapper — port of codex::InsetRenderable<'a>."""

    child: RenderableItem
    insets: Insets

    def render(self, area: Rect, buf: List[str]) -> None:
        self.child.render(area.inset(self.insets), buf)

    def desired_height(self, width: int) -> int:
        return (
            self.child.desired_height(max(0, width - self.insets.left - self.insets.right))
            + self.insets.top
            + self.insets.bottom
        )

    def cursor_pos(self, area: Rect) -> Optional[Tuple[int, int]]:
        return self.child.cursor_pos(area.inset(self.insets))

    def cursor_style(self, area: Rect) -> str:
        return self.child.cursor_style(area.inset(self.insets))


def inset(r: Renderable, insets: Insets) -> RenderableItem:
    """Build an InsetRenderable — port of codex::RenderableExt::inset."""
    return RenderableItem(InsetRenderable(RenderableItem(r), insets))


__all__ = [
    "Insets",
    "Rect",
    "Renderable",
    "RenderableItem",
    "renderable_item_from",
    "ColumnRenderable",
    "FlexChild",
    "FlexRenderable",
    "RowRenderable",
    "InsetRenderable",
    "inset",
    "_UnitRenderable",
    "_StringRenderable",
    "_SpanRenderable",
    "_LineRenderable",
    "_ParagraphRenderable",
]
