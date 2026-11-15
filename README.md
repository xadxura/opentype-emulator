# opentype-emulator

Quick and dirty tool in Python to help understand complicated sets of substitution rules in OpenType.

This is a lookup emulator, not a complete text-shaping engine. The horizontal,
static-font GPOS stages are complete; see [the GPOS plan](gpos.md).

## Getting started

Install the runtime dependency with `python -m pip install -r requirements.txt`.
The input is a TTX XML font dump, not a binary TTF/OTF. FontTools can produce
one with `python -m fontTools.ttx myfont.ttf`.

For substitution-only simulation:

```python
import sys
sys.path.insert(0, "src")

from ttxfont import Simulator
from ttxread import read_ttx

font = read_ttx("myfont.ttx", positioning=False)
sim = Simulator(font)
sim.suppressed = ["ss01", "rtlm"]
sim.set_string("+*")  # Or sim.set_tokens(["plus", "asterisk"])
print(sim.steps_str(), end="")
print(sim.shaped_str())
```

See [example.py](src/example.py) for further substitution-only examples.
`positioning=False` skips GPOS loading and execution. Reported coordinates
currently describe baseline horizontal layout, not a fully shaped rendering.

## Substitution support

- Single (GSUB type 1), multiple including deletion (type 2), alternate
  (type 3), and ligature substitutions (type 4).
- Contextual (type 5) and chained contextual substitutions (type 6), formats
  1, 2, and 3, both direct and extension-wrapped (type 7).
- Backtrack, input, and lookahead matching with GDEF lookup flags, mark
  attachment classes, and mark filtering sets.
- Nested lookup records executed in stored order against the modified input
  sequence. Insertions, deletions, and ligatures update tracked positions,
  including across nested contexts and ignored glyphs.
- First-matching rule/subtable precedence. Processing advances past the
  modified input sequence, not past lookahead.
- Reverse chaining single substitutions (type 8, format 1), direct or
  extension-wrapped, scan from the logical end toward the start. Context
  matching observes substitutions already made to the right.

All eight GSUB lookup types are implemented. Alternate selection uses a
1-based per-feature index, defaulting to the first alternate:

```python
sim.feature_values = {"ss01": 2, "calt": 1}
sim.set_string("+*")
```

`Font.apply` and `Font.render` also accept `feature_values={"ss01": 2}`.
A value of `0` disables a feature (of any lookup type); `suppressed` takes
precedence over feature values. Values must be nonnegative integers. If an
alternate index exceeds a glyph's available alternatives, that subtable does
not apply; later subtables are tried, and otherwise the glyph is unchanged.
Nested alternate lookups inherit the invoking feature's value.

Trace applications retain the existing keys and add `before` (the glyph
sequence before application) and `children` (nested applications).
`before_glyph_infos` and `glyph_infos` are immutable snapshots of per-run
`glyphrun.GlyphInfo` records. They preserve ligature origins/components and
multiple-substitution ancestry through nested lookups, deletion, and later
single/alternate/reverse substitutions. Ligature and contextual input matching
respect component boundaries, including the ignored-parent exception.
Recursive lookup execution is limited to 64 levels and raises `ValueError`
if that limit is exceeded. Invalid sequence indexes and missing referenced
lookups also raise `ValueError`. Unsupported GSUB subtables raise
`NotImplementedError` rather than producing a silently incomplete result.

### Remaining limitations

Script/language-specific feature selection, shaping stages,
Unicode normalization/reordering, bidirectional layout, and variable-font
feature variations are not implemented. All non-suppressed GSUB features are
combined in lookup-list order; a lookup shared by features runs once if any
referencing feature is enabled. If a lookup is shared by features with different
values, the first enabled feature in the font's feature-list order supplies
the value; this is an emulator policy, not full script-specific feature-mask
processing. Feature values apply to the entire input, not character ranges.
Input glyph order is logical order.

This tool is not a replacement for HarfBuzz or a browser's shaping engine.

## Positioning API

All nine GPOS lookup types are implemented for horizontal, static-font runs:

- Type 1: single positioning, formats 1 and 2.
- Type 2: explicit-pair and class-pair positioning, formats 1 and 2.
- Type 3: cursive attachment, format 1.
- Type 4: mark-to-base positioning.
- Type 5: mark-to-ligature positioning, with component-aware anchor selection.
- Type 6: mark-to-mark positioning, including stacked marks.
- Types 7 and 8: contextual and chained contextual positioning, formats 1-3.
- Type 9: extension wrappers for types 1-8.

Adjustments accumulate in lookup-list order. The first matching subtable wins.
Pair matching uses GDEF filtering; class zero includes unclassified glyphs.
If `ValueFormat2` is zero, the second glyph remains available to start the
next pair. Otherwise both glyphs are consumed, even if the second record
contains only zero values.

Contextual positioning shares normalized matching with GSUB and executes all
lookup records in stored order, including repeated actions on one glyph.
Each nested lookup uses its own flags to filter surrounding glyphs, but does
not reject its starting glyph: the outer `SequenceIndex` already selected it.
The outer cursor advances past its matched input, never past lookahead or a
child's larger match. No-op contexts and failed child actions still consume
the matched input and block later matching rules/subtables. GPOS input matching
respects ligature-component boundaries without GSUB's ignored-parent exception.
Nested actions share attachments and provenance with the outer run. Recursion
is capped at 64 levels; invalid sequence indexes and missing lookups raise
`ValueError`.

Mark-to-base searches backward past marks to the nearest nonmark; it cannot
cross an uncovered base. Mark-to-mark searches backward through eligible
marks, respecting mark filtering/attachment classes, but never crosses a
nonmark, even with `IgnoreBaseGlyphs` or `IgnoreLigatures`. Missing parent
anchors allow later subtables to be tried. Attachment replaces earlier mark
placement, while subsequent placement adjustments remain additive.

Mark-to-ligature uses matching GSUB provenance to choose a component; its
index is clamped to the available anchor rows. Without matching provenance
(including a ligature supplied directly as a glyph token), the final
component is used. A missing anchor does not select a different component;
later subtables are tried instead. Zero-component ligature records do not
match.

Non-first multiple-substitution outputs can be skipped in type 4/5 parent
searches unless covered by the current subtable. For type 4, a preceding mark
or a break in expansion continuity ends this skipping. Mark-to-mark rejects
ordinary marks belonging to different ligature components, while retaining
the exception for marks that are themselves newly formed ligatures.

Horizontal mark attachments resolve X against the parent's final position and
advances. The parent's Y position is captured when attaching, matching
HarfBuzz's cross-stream attachment behavior; later parent Y adjustments do
not move previously attached children. Reattachment replaces the earlier link.
Anchor formats 1 and 2 use stored, unhinted font-unit coordinates. Format 3
is supported only when its device/variation tables are absent.

Cursive attachment joins the current glyph's entry to the previous eligible
glyph's exit. It adjusts horizontal advances/offsets and links only the Y
positions. Missing anchors try later subtables; overlapping lookups can
reverse and re-root existing cursive chains. The `RightToLeft` lookup flag
chooses the chain's baseline end, independently of the run's direction.
Mark and cursive attachments can replace each other, and each coordinate axis
is resolved independently. A mixed link cycle can be valid; a dependency
cycle on a single axis raises `ValueError`.

All four scalar ValueRecord fields are parsed. For horizontal layout,
`XPlacement`, `YPlacement`, and `XAdvance` are applied additively; `YAdvance`
is retained in parsed rules but not applied, as required by OpenType.
Horizontal runs support explicit `direction="ltr"` (default) or `"rtl"`:

```python
tokens, positions, applications, origins = font.render(tokens, direction="rtl")
sim.direction = "rtl"
sim.set_tokens(tokens)
```

`Font.apply`, `Font.render`, `Font.shape`, the GPOS lookup entry points, and
the origin/attachment resolvers accept the direction keyword. Tokens,
position records, origins, and trace indexes remain in logical order in
both directions. RTL origins accumulate advances from the logical end,
starting at X=0 for the leftmost visual glyph. Offsets still use positive
X to the right and positive Y upward; advances may be negative after cursive
adjustment. Unlike HarfBuzz's visual-order output, arrays are not reversed.
Direction does not select scripts/features, reorder GSUB input, or perform
bidi analysis, Unicode mirroring, or Arabic joining. Callers must supply a
preprocessed single logical run. Vertical/unknown directions raise
`ValueError`.

Use `read_ttx(..., positioning=False)` for GSUB-only simulation, or suppress
individual positioning features with `suppressed` or a zero feature value.
GPOS features use the same all-non-suppressed-feature policy as GSUB; a shared
lookup runs once if any referencing feature is enabled. Unknown lookup types
raise `NotImplementedError` when executed.
Unsupported ValueRecord fields, anchor device/variation adjustments, and
reserved bits are rejected at load time, including in otherwise disabled
supported features. Invalid record counts, class matrices, anchors, and
inconsistent lookup/subtable types also raise explicit errors.

Component provenance is local to each `Font.apply`/`Font.render` run, not
stored on the font or inferred from glyph names. In `GlyphInfo`,
`ligature_id` identifies a generated origin, `component` is 1-based for
attached marks and 0-based for multiple-substitution pieces, and
`ligature_components` records a generated ligature's source component count.
The `multiplied` flag distinguishes expansion pieces; nested ligatures remap
internal and trailing marks and avoid double-counting expansion pieces.
These records do not implement Unicode clustering or script shaping.

The tuple shapes returned by `Font.apply` and `Font.render` are unchanged.
Their `positionings` elements, also exposed as `Simulator.positionings`, are
now immutable `gpos.GlyphPosition` records rather than dictionaries:

- `x_advance`, `y_advance`: movement of the pen after each glyph.
- `x_offset`, `y_offset`: displacement of that glyph from the pen origin.

Initial `x_advance` values come from the final, post-GSUB glyphs' horizontal
metrics. The other fields start at zero. Missing metrics raise `ValueError`.
Script-dependent mark advance zeroing is not performed automatically; use
the font's metrics and explicit GPOS adjustments for lookup-only simulation.
`position.adjusted(...)` returns a new record with additive adjustments;
it does not mutate the original.

`Font.shape(tokens, positionings)` now returns actual horizontal-run glyph
origins: accumulated advances plus each glyph's local offsets. Offsets do not
move the pen. Legacy positioning dictionaries are explicitly rejected.

`gpos.GlyphAttachment` stores a parent index and parent-relative displacement
separately from local position records. `Font.shape(..., attachments={...})`
and `gpos.resolve_origins(...)` can resolve these relationships, including
chains. Attachment offsets are in font units with positive Y upward; the
child's local offsets are added to its parent-relative origin. An optional
`parent_y` freezes the parent's Y origin; automatic horizontal mark attachments
use this snapshot. Without it, both axes follow the parent. Attachments do not
alter pen advances. With `cursive=True`, only Y follows the parent; X keeps
its own pen-relative origin. Cursive execution separately changes advances.
Invalid indexes and same-axis cycles raise `ValueError`.
`Font.apply` keeps these relationships internally across GPOS lookups and
returns resolved, pen-relative `GlyphPosition` records. Thus `Font.render` and
ordinary `Font.shape` calls do not apply attachment offsets twice.

GPOS application traces use `gpos.positioning_application(...)`.
`before_positionings` and `positionings` are tuple snapshots of
immutable records; token and affected-index containers are also snapshots.
Runtime trace positions are already resolved. `before_attachments` and
`attachments` contain immutable `(child_index, GlyphAttachment)` tuples for
inspecting links, not for resolving those position snapshots a second time.
Runtime GPOS traces also include `direction` and the final `glyph_infos` snapshot. Type 5
applications add `ligature_component`, the selected 1-based anchor row.
Contextual applications retain the matched input indexes and ordered nested
applications in `children`, with before/after positions and attachment snapshots.
The simulator formats these records alongside GSUB applications.
`GPOS_Lookup.apply_at` now returns `(positionings, application, jump)`, matching
the cursor-reporting convention used by GSUB lookups.

## Tests

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

`uharfbuzz` requires a compatible binary wheel or native build tools. On Windows
ARM, an x64 Python interpreter can use its Windows x64 wheels.

The self-contained tests construct small fonts and compare substitution
results, all four position fields, and final glyph origins with HarfBuzz,
with separate assertions for tracing and exact position tracking.
HarfBuzz itself has known bookkeeping differences for deletion of
an entire contextual input and ligatures with differing outer/inner filters;
those edge cases have explicit emulator assertions instead of treating
HarfBuzz as an oracle.
Cursive comparisons cover both horizontal directions and both baseline flags,
extensions, filtering, nested calls, prior/later scalar adjustments, chain
reversal, and mixed mark/cursive reattachment. They include 108 exhaustive
three-glyph chain comparisons. RTL comparisons normalize HarfBuzz's arrays
back to logical order and match its script's native horizontal direction,
isolating lookup behavior from script-driven buffer reversal.

Optional integration tests use existing local fonts and YAML sequences without
copying or changing them:

```powershell
$env:OPENTYPE_TEST_FONTS = "C:\Users\anglass\source\ms-volt-src\tests"
python -m unittest discover -s tests -p test_font_corpus.py -v
```

These compare final glyph sequences for the Egyptian corpora against HarfBuzz
with all GSUB features enabled and default-ignorable characters preserved.
The Egyptian comparisons do not assert GPOS coordinates or treat the YAML's
manual `pass` flags as expected glyph sequences. Separate integration tests
isolate real single, pair, mark-to-base, and mark-to-mark lookup data from
`seg_src.ttf` in in-memory fonts and compare position fields and origins with
HarfBuzz. Mark cases exercise classes, null anchors, filtering, and extensions.
A further comparison combines the real type 5 anchor data with an in-memory
GSUB ligature fixture to check both components and all populated mark classes.
Contextual comparisons retain the full original lookup list, preserving
referenced indexes while enabling only one outer context. These exercise
real direct and extension-wrapped contexts from `seg_src.ttf` and
`eot_volt.ttf`, including no-op matches and nested scalar/attachment actions.
Positioning fixtures normalize mark metrics; contextual comparisons use
HarfBuzz's early mark-advance zeroing so explicit GPOS mark advances survive.
No source font is changed or written. Without the environment variable,
corpus tests are skipped.

Semantics reference: [OpenType contextual lookup formats and processing](https://learn.microsoft.com/en-us/typography/opentype/spec/chapter2#contextual-lookup-subtables).
