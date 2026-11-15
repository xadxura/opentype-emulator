from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace


GlyphPattern = str | Sequence[str]


class ContextRule:
    def __init__(
        self, lefts: Sequence[GlyphPattern], inputs: Sequence[GlyphPattern],
        rights: Sequence[GlyphPattern], refs: Sequence[tuple[int, int]],
    ):
        self.lefts = list(lefts)
        self.inputs = list(inputs)
        self.rights = list(rights)
        self.refs = list(refs)
        if not inputs:
            raise ValueError("A contextual rule needs at least one input glyph")
        if any(index < 0 or index >= len(inputs) for index, _ in refs):
            raise ValueError("Contextual SequenceIndex is outside the input sequence")
        if any(lookup < 0 for _, lookup in refs):
            raise ValueError("Contextual lookup index must be nonnegative")

    def length(self) -> int:
        return len(self.lefts) + len(self.inputs) + len(self.rights)

    @staticmethod
    def _matches(glyph: str, pattern: GlyphPattern) -> bool:
        return glyph == pattern if isinstance(pattern, str) else glyph in pattern

    def match_positions(
        self, tokens: Sequence[str], pos: int, eligible: Callable[[int], bool],
    ) -> tuple[int, ...] | None:
        if not 0 <= pos < len(tokens) or not self._matches(tokens[pos], self.inputs[0]):
            return None
        following = [p for p in range(pos + 1, len(tokens)) if eligible(p)]
        patterns = self.inputs[1:] + self.rights
        if len(following) < len(patterns) or any(
            not self._matches(tokens[p], pattern) for p, pattern in zip(following, patterns)
        ):
            return None
        if self.lefts:
            preceding = [p for p in range(pos) if eligible(p)]
            if len(preceding) < len(self.lefts) or any(
                not self._matches(tokens[p], pattern)
                for p, pattern in zip(preceding[-len(self.lefts):], self.lefts)
            ):
                return None
        return (pos, *following[:len(self.inputs) - 1])

    def __str__(self) -> str:
        def patterns(values):
            return " ".join(value if isinstance(value, str) else "/".join(value) for value in values)
        refs = " ".join(f"{index}->{lookup}" for index, lookup in self.refs)
        return f"{patterns(self.lefts)}|{patterns(self.inputs)}|{patterns(self.rights)} ---> {refs}"


@dataclass(frozen=True)
class GlyphInfo:
    glyph_class: int = 0
    ligature_id: int = 0
    component: int = 0
    ligature_components: int = 0
    multiplied: bool = False

    @property
    def component_count(self) -> int:
        return self.ligature_components if self.glyph_class == 2 and self.ligature_components else 1

    @property
    def ligation_count(self) -> int:
        return 0 if self.multiplied and self.component else self.component_count

    def accepts_attachment(self, previous: "GlyphInfo | None", *, ligature: bool) -> bool:
        if not self.multiplied or not self.component:
            return True
        if ligature:
            return False
        return (
            previous is None or previous.glyph_class == 3 or not previous.multiplied
            or previous.ligature_id != self.ligature_id
            or previous.component + 1 != self.component
        )

    def compatible_mark(self, other: "GlyphInfo") -> bool:
        if self.ligature_id == other.ligature_id:
            return not self.ligature_id or self.component == other.component
        return bool(
            (self.ligature_id and not self.component)
            or (other.ligature_id and not other.component)
        )


class GlyphRun:
    """Mutable per-run provenance; glyph records themselves are immutable."""

    def __init__(self, tokens: Sequence[str], classes: Mapping[str, int]):
        self.classes = classes
        self.infos = [GlyphInfo(glyph_class=classes.get(token, 0)) for token in tokens]
        self.next_ligature_id = 1

    def can_match(self, positions: Sequence[int], ignored: Callable[[int], bool]) -> bool:
        return self.match_components(self.infos, positions, ignored)

    @staticmethod
    def match_components(
        infos: Sequence[GlyphInfo], positions: Sequence[int], ignored: Callable[[int], bool],
        *, allow_ignored_parent: bool = True,
    ) -> bool:
        first = infos[positions[0]]
        for pos in positions[1:]:
            current = infos[pos]
            if first.ligature_id and first.component:
                if (first.ligature_id, first.component) == (current.ligature_id, current.component):
                    continue
                if not allow_ignored_parent:
                    return False
                # GSUB can match different components when their parent is ignored.
                parent = positions[0] - 1
                while parent >= 0 and infos[parent].ligature_id == first.ligature_id:
                    if not infos[parent].component:
                        if ignored(parent):
                            break
                        return False
                    parent -= 1
                else:
                    return False
            elif current.ligature_id and current.component and current.ligature_id != first.ligature_id:
                return False
        return True

    def _for_glyph(self, info: GlyphInfo, glyph: str, guess: int = 0) -> GlyphInfo:
        glyph_class = self.classes.get(glyph, 0) if self.classes else guess or info.glyph_class
        return replace(info, glyph_class=glyph_class)

    def substitute(
        self, before: Sequence[str], after: Sequence[str], positions: Sequence[int],
        *, multiple: bool = False, ligature: bool = False,
    ) -> None:
        if len(before) != len(self.infos):
            raise ValueError("Substitution provenance needs one record per glyph")
        count = len(after) - len(before) + len(positions)
        pos = positions[0]
        if ligature and len(positions) > 1:
            self._ligate(after[pos], positions)
        elif multiple and count != 1:
            original = self.infos[pos]
            guess = 1 if original.glyph_class == 2 else 0
            outputs = []
            for index in range(count):
                info = replace(original, multiplied=True)
                if not original.ligature_id:
                    info = replace(info, component=index, ligature_components=0)
                outputs.append(self._for_glyph(info, after[pos + index], guess))
            self.infos[pos:pos + 1] = outputs
        else:
            self.infos[pos] = self._for_glyph(self.infos[pos], after[pos])
        if len(after) != len(self.infos):
            raise ValueError("Substitution provenance and output glyph counts differ")

    def _ligate(self, output: str, positions: Sequence[int]) -> None:
        first = self.infos[positions[0]]
        following_marks = all(self.infos[pos].glyph_class == 3 for pos in positions[1:])
        mark_ligature = first.glyph_class == 3 and following_marks
        base_ligature = first.glyph_class == 1 and following_marks
        new_ligature = not mark_ligature and not base_ligature
        counts = [first.component_count] + [self.infos[pos].ligation_count for pos in positions[1:]]
        ligature_id = self.next_ligature_id if new_ligature else 0
        if new_ligature:
            self.next_ligature_id += 1
        result = replace(first, multiplied=False)
        if new_ligature:
            result = replace(
                result, ligature_id=ligature_id, component=0, ligature_components=sum(counts),
            )
        result = self._for_glyph(result, output, 2 if new_ligature else 0)
        prefix = 0
        for index, (start, end) in enumerate(zip(positions, positions[1:])):
            if new_ligature:
                for pos in range(start + 1, end):
                    info = self.infos[pos]
                    component = prefix + min(info.component or counts[index], counts[index])
                    self.infos[pos] = replace(
                        info, ligature_id=ligature_id, component=component, ligature_components=0,
                    )
            prefix += counts[index]
        last = self.infos[positions[-1]]
        if not mark_ligature and last.ligature_id:
            for pos in range(positions[-1] + 1, len(self.infos)):
                info = self.infos[pos]
                if info.ligature_id != last.ligature_id or not info.component:
                    break
                self.infos[pos] = replace(
                    info, ligature_id=ligature_id,
                    component=prefix + min(info.component, counts[-1]), ligature_components=0,
                )
        consumed = set(positions)
        self.infos = [info for pos, info in enumerate(self.infos) if pos not in consumed]
        self.infos.insert(positions[0], result)
