from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from glyphrun import ContextRule


class ContextPositioning(ContextRule):
    def __str__(self) -> str:
        return "Contextual positioning " + super().__str__()


@dataclass(frozen=True)
class GlyphPosition:
    x_advance: int = 0
    y_advance: int = 0
    x_offset: int = 0
    y_offset: int = 0

    def adjusted(
        self, *, x_advance: int = 0, y_advance: int = 0,
        x_offset: int = 0, y_offset: int = 0,
    ) -> "GlyphPosition":
        return replace(
            self,
            x_advance=self.x_advance + x_advance,
            y_advance=self.y_advance + y_advance,
            x_offset=self.x_offset + x_offset,
            y_offset=self.y_offset + y_offset,
        )


@dataclass(frozen=True)
class GlyphAttachment:
    """Parent-relative mark link or Y-only cursive link."""

    parent: int
    x_offset: int = 0
    y_offset: int = 0
    parent_y: int | None = None
    cursive: bool = False


@dataclass(frozen=True)
class PositionMatch:
    adjustments: tuple[tuple[int, GlyphPosition], ...]
    next_pos: int
    attachment: GlyphAttachment | None = None
    component: int | None = None


@dataclass(frozen=True)
class SinglePositioning:
    values: Mapping[str, GlyphPosition]

    def match(
        self, tokens: Sequence[str], pos: int, second: int | None,
    ) -> PositionMatch | None:
        value = self.values.get(tokens[pos])
        if value is None:
            return None
        return PositionMatch(((pos, value),), pos + 1)

    def __str__(self) -> str:
        return "Single positioning"


PairKey = tuple[str, str] | tuple[int, int]
PairValues = tuple[GlyphPosition, GlyphPosition]


class PairPositioning:
    def __init__(self, values: Mapping[PairKey, PairValues], has_second_value: bool):
        self.values = values
        self.has_second_value = has_second_value

    def values_for(self, first: str, second: str) -> PairValues | None:
        return self.values.get((first, second))

    def match(
        self, tokens: Sequence[str], pos: int, second: int | None,
    ) -> PositionMatch | None:
        if second is None:
            return None
        values = self.values_for(tokens[pos], tokens[second])
        if values is None:
            return None
        # A present second ValueRecord consumes the pair, even when all values are zero.
        next_pos = second + 1 if self.has_second_value else second
        return PositionMatch(((pos, values[0]), (second, values[1])), next_pos)

    def __str__(self) -> str:
        return "Pair positioning"


class ClassPairPositioning(PairPositioning):
    def __init__(
        self, coverage: Sequence[str], class1: Mapping[str, int],
        class2: Mapping[str, int], values: Mapping[PairKey, PairValues],
        has_second_value: bool,
    ):
        super().__init__(values, has_second_value)
        self.coverage = frozenset(coverage)
        self.class1 = class1
        self.class2 = class2

    def values_for(self, first: str, second: str) -> PairValues | None:
        if first not in self.coverage:
            return None
        key = (self.class1.get(first, 0), self.class2.get(second, 0))
        if key not in self.values:
            raise ValueError("Pair positioning class index is outside the class matrix")
        return self.values[key]

    def __str__(self) -> str:
        return "Class pair positioning"


Anchor = tuple[int, int]


@dataclass(frozen=True)
class CursivePositioning:
    anchors: Mapping[str, tuple[Anchor | None, Anchor | None]]

    def match(
        self, tokens: Sequence[str], pos: int, previous: int | None,
    ) -> tuple[Anchor, Anchor] | None:
        entry = self.anchors.get(tokens[pos], (None, None))[0]
        exit = None if previous is None else self.anchors.get(tokens[previous], (None, None))[1]
        return None if entry is None or exit is None else (exit, entry)

    def __str__(self) -> str:
        return "Cursive attachment"


def validate_direction(direction: str) -> None:
    if direction not in ("ltr", "rtl"):
        raise ValueError("Horizontal direction must be 'ltr' or 'rtl'")


def attachment_offsets(
    positions: Sequence[GlyphPosition], attachments: Mapping[int, GlyphAttachment], index: int,
) -> Anchor:
    """Offsets before deferred attachment propagation."""
    position = positions[index]
    link = attachments.get(index)
    if link is None:
        return position.x_offset, position.y_offset
    return (
        position.x_offset + (0 if link.cursive else link.x_offset),
        position.y_offset + link.y_offset + (0 if link.cursive else link.parent_y or 0),
    )


def apply_cursive_attachment(
    positions: Sequence[GlyphPosition], attachments: dict[int, GlyphAttachment],
    previous: int, current: int, exit: Anchor, entry: Anchor,
    *, direction: str, right_to_left: bool,
) -> list[GlyphPosition]:
    validate_direction(direction)
    result = list(positions)
    first_x, _ = attachment_offsets(result, attachments, previous)
    second_x, _ = attachment_offsets(result, attachments, current)
    if direction == "ltr":
        result[previous] = replace(result[previous], x_advance=exit[0] + first_x)
        delta = entry[0] + second_x
        result[current] = result[current].adjusted(x_advance=-delta, x_offset=-delta)
    else:
        delta = exit[0] + first_x
        result[previous] = result[previous].adjusted(x_advance=-delta, x_offset=-delta)
        result[current] = replace(result[current], x_advance=entry[0] + second_x)

    child, parent = (previous, current) if right_to_left else (current, previous)
    y = entry[1] - exit[1] if right_to_left else exit[1] - entry[1]

    def connect(child: int, parent: int, y: int) -> None:
        x, _ = attachment_offsets(result, attachments, child)
        result[child] = replace(result[child], x_offset=x, y_offset=0)
        attachments[child] = GlyphAttachment(parent, y_offset=y, cursive=True)

    # Reverse an existing cursive path so its old subtree follows the new parent.
    edges = []
    node = child
    visiting = set()
    while node in attachments and attachments[node].cursive:
        if node in visiting:
            raise ValueError("Cyclic cursive attachment")
        visiting.add(node)
        old_parent = attachments[node].parent
        x, old_y = attachment_offsets(result, attachments, node)
        del attachments[node]
        result[node] = replace(result[node], x_offset=x, y_offset=old_y)
        if old_parent == parent:
            break
        edges.append((node, old_parent, old_y))
        node = old_parent
    for old_child, old_parent, old_y in reversed(edges):
        connect(old_parent, old_child, -old_y)
    connect(child, parent, y)
    if parent in attachments and attachments[parent].parent == child:
        x, _ = attachment_offsets(result, attachments, parent)
        del attachments[parent]
        result[parent] = replace(result[parent], x_offset=x, y_offset=0)
    return result


@dataclass(frozen=True)
class MarkRecord:
    mark_class: int
    anchor: Anchor


@dataclass(frozen=True)
class MarkPositioning:
    marks: Mapping[str, MarkRecord]
    parents: Mapping[str, Mapping[int, Anchor]]
    to_mark: bool

    def match(
        self, tokens: Sequence[str], pos: int, parent: int | None,
    ) -> PositionMatch | None:
        mark = self.marks.get(tokens[pos])
        if mark is None or parent is None:
            return None
        anchor = self.parents.get(tokens[parent], {}).get(mark.mark_class)
        if anchor is None:
            return None
        return PositionMatch((), pos + 1, GlyphAttachment(
            parent, anchor[0] - mark.anchor[0], anchor[1] - mark.anchor[1],
        ))

    def __str__(self) -> str:
        return "Mark-to-mark attachment" if self.to_mark else "Mark-to-base attachment"


@dataclass(frozen=True)
class LigaturePositioning:
    marks: Mapping[str, MarkRecord]
    parents: Mapping[str, tuple[Mapping[int, Anchor], ...]]

    def match(
        self, tokens: Sequence[str], pos: int, parent: int | None, component: int,
    ) -> PositionMatch | None:
        mark = self.marks.get(tokens[pos])
        if mark is None or parent is None:
            return None
        components = self.parents.get(tokens[parent], ())
        if not components:
            return None
        selected = min(component, len(components)) - 1 if component > 0 else len(components) - 1
        anchor = components[selected].get(mark.mark_class)
        if anchor is None:
            return None
        return PositionMatch((), pos + 1, GlyphAttachment(
            parent, anchor[0] - mark.anchor[0], anchor[1] - mark.anchor[1],
        ), selected)

    def __str__(self) -> str:
        return "Mark-to-ligature attachment"


def resolve_origins(
    positions: Sequence[GlyphPosition],
    attachments: Mapping[int, GlyphAttachment] | None = None,
    *, direction: str = "ltr",
) -> list[tuple[int, int]]:
    validate_direction(direction)
    attachments = {} if attachments is None else attachments
    for child, attachment in attachments.items():
        if not 0 <= child < len(positions) or not 0 <= attachment.parent < len(positions):
            raise ValueError("Attachment indexes must refer to glyphs in this run")

    pen_x = pen_y = 0
    origins = [(0, 0)] * len(positions)
    order = range(len(positions)) if direction == "ltr" else range(len(positions) - 1, -1, -1)
    for index in order:
        position = positions[index]
        origins[index] = (pen_x + position.x_offset, pen_y + position.y_offset)
        pen_x += position.x_advance
        pen_y += position.y_advance

    for child, attachment in attachments.items():
        if attachment.parent_y is not None:
            origins[child] = (
                origins[child][0],
                attachment.parent_y + attachment.y_offset + positions[child].y_offset,
            )
    # Cursive follows only Y; automatic marks follow only X. A mixed link
    # cycle can therefore be valid even though a cycle on either axis is not.
    for axis in (0, 1):
        resolved = {
            index for index, link in attachments.items()
            if (axis == 0 and link.cursive) or (axis == 1 and link.parent_y is not None)
        }
        for child in attachments:
            chain = []
            visiting = set()
            current = child
            while current in attachments and current not in resolved:
                if current in visiting:
                    raise ValueError("Cyclic glyph attachment")
                visiting.add(current)
                chain.append(current)
                current = attachments[current].parent
            for index in reversed(chain):
                attachment = attachments[index]
                position = positions[index]
                delta = attachment.x_offset + position.x_offset if axis == 0 else (
                    attachment.y_offset + position.y_offset
                )
                value = origins[attachment.parent][axis] + delta
                x, y = origins[index]
                origins[index] = (value, y) if axis == 0 else (x, value)
                resolved.add(index)
    return origins


def resolve_positions(
    positions: Sequence[GlyphPosition],
    attachments: Mapping[int, GlyphAttachment],
    *, direction: str = "ltr",
) -> list[GlyphPosition]:
    """Convert local positions and attachment links to pen-relative records."""
    validate_direction(direction)
    if not attachments:
        return list(positions)
    origins = resolve_origins(positions, attachments, direction=direction)
    baseline = resolve_origins(positions, direction=direction)
    result = []
    for position, (x, y), (base_x, base_y) in zip(positions, origins, baseline):
        result.append(replace(
            position, x_offset=position.x_offset + x - base_x,
            y_offset=position.y_offset + y - base_y,
        ))
    return result


def positioning_application(
    *, index: str, tokens: Sequence[str], posses: Sequence[int], rule: object,
    before: Sequence[GlyphPosition], after: Sequence[GlyphPosition],
    children: Sequence[dict] = (),
    before_attachments: Mapping[int, GlyphAttachment] | None = None,
    attachments: Mapping[int, GlyphAttachment] | None = None,
    direction: str = "ltr",
) -> dict:
    if len(before) != len(tokens) or len(after) != len(tokens):
        raise ValueError("Positioning snapshots must have one record per glyph")
    return {
        "direction": direction,
        "index": index,
        "tokens": tuple(tokens),
        "posses": tuple(posses),
        "rule": rule,
        "before_positionings": tuple(before),
        "positionings": tuple(after),
        "children": tuple(children),
        "before_attachments": tuple(sorted((before_attachments or {}).items())),
        "attachments": tuple(sorted((attachments or {}).items())),
    }
