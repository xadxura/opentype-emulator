from dataclasses import FrozenInstanceError
import io
import unittest

from fontTools.ttLib.tables import otTables
import uharfbuzz as hb

from test_chained_substitution import build_font, read_substitutions, read_ttx, Simulator
from gpos import (
    GlyphAttachment, GlyphPosition, positioning_application, resolve_origins, resolve_positions,
)
from ttxfont import Font, GPOS_Lookup


def load_font(source, *, positioning=True):
    output = io.BytesIO()
    source.saveXML(output)
    output.seek(0)
    return read_ttx(output, positioning=positioning)


class PositionModelTests(unittest.TestCase):
    def test_initial_advances_use_post_substitution_glyphs(self):
        source = build_font("feature calt {sub a by x y;} calt;")
        source["hmtx"].metrics["x"] = (300, 0)
        source["hmtx"].metrics["y"] = (700, 0)
        font = read_substitutions(source)
        tokens, positions, _ = font.apply(["a", "b"])
        self.assertEqual(tokens, ["x", "y", "b"])
        self.assertEqual(positions, [
            GlyphPosition(x_advance=300),
            GlyphPosition(x_advance=700),
            GlyphPosition(x_advance=500),
        ])
        self.assertEqual(font.shape(tokens, positions), [(0, 0), (300, 0), (1000, 0)])

    def test_empty_input(self):
        font = Font()
        self.assertEqual(font.apply([]), ([], [], []))
        self.assertEqual(font.render([]), ([], [], [], []))
        self.assertEqual(resolve_origins([]), [])

    def test_offsets_do_not_change_the_pen(self):
        positions = [
            GlyphPosition(300, 0, 20, -10),
            GlyphPosition(700, 0, -30, 40),
            GlyphPosition(0, 0, 5, 6),
        ]
        self.assertEqual(resolve_origins(positions), [(20, -10), (270, 40), (1005, 6)])

    def test_advance_adjustments_accumulate_and_move_following_glyphs(self):
        first = GlyphPosition(500)
        adjusted = first.adjusted(x_advance=-100, y_advance=25, x_offset=10)
        adjusted = adjusted.adjusted(x_advance=-50, y_offset=-15)
        self.assertEqual(adjusted, GlyphPosition(350, 25, 10, -15))
        self.assertEqual(first, GlyphPosition(500))
        self.assertEqual(
            resolve_origins([adjusted, GlyphPosition(600), GlyphPosition(200)]),
            [(10, -15), (350, 25), (950, 25)],
        )

    def test_zero_and_negative_advances(self):
        self.assertEqual(
            resolve_origins([GlyphPosition(0), GlyphPosition(-100), GlyphPosition(50)]),
            [(0, 0), (0, 0), (-100, 0)],
        )

    def test_records_are_immutable(self):
        position = GlyphPosition(500)
        with self.assertRaises(FrozenInstanceError):
            position.x_offset = 100
        attachment = GlyphAttachment(0, 20, 30)
        with self.assertRaises(FrozenInstanceError):
            attachment.parent = 1

    def test_missing_metrics_are_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "horizontal advance for glyph: missing"):
            Font().apply(["missing"])

    def test_shape_requires_matching_record_count(self):
        with self.assertRaisesRegex(ValueError, "one record per glyph"):
            Font().shape(["a"], [])

    def test_shape_explicitly_rejects_legacy_dictionaries(self):
        with self.assertRaisesRegex(TypeError, "GlyphPosition"):
            Font().shape(["a"], [{}])

    def test_unadjusted_layout_matches_harfbuzz(self):
        source = build_font("feature calt {sub a by x;} calt;")
        source["hmtx"].metrics["x"] = (300, 0)
        source["hmtx"].metrics["b"] = (725, 0)
        binary = io.BytesIO()
        source.save(binary)
        buffer = hb.Buffer()
        buffer.add_str("aba")
        buffer.direction = "ltr"
        buffer.script = "latn"
        hb.shape(hb.Font(hb.Face(binary.getvalue())), buffer, {"calt": True})
        font = load_font(source)
        tokens, positions, _, origins = font.render(list("aba"))
        expected = [
            GlyphPosition(p.x_advance, p.y_advance, p.x_offset, p.y_offset)
            for p in buffer.glyph_positions
        ]
        self.assertEqual(tokens, ["x", "b", "x"])
        self.assertEqual(positions, expected)
        self.assertEqual(origins, [(0, 0), (300, 0), (1025, 0)])

    def test_simulator_reports_real_baseline_origins(self):
        source = build_font("feature calt {sub a by x;} calt;")
        source["hmtx"].metrics["x"] = (300, 0)
        simulator = Simulator(load_font(source))
        simulator.set_string("ab")
        self.assertEqual(simulator.positionings, [GlyphPosition(300), GlyphPosition(500)])
        self.assertEqual(simulator.places, [(0, 0), (300, 0)])
        self.assertEqual(simulator.shaped_str(), "Shaped\nx(0, 0) b(300, 0) ")


class AttachmentFoundationTests(unittest.TestCase):
    def test_parent_relative_attachment_ignores_intervening_advances(self):
        positions = [GlyphPosition(500, 0, 10, 20), GlyphPosition(600), GlyphPosition(0)]
        attachments = {2: GlyphAttachment(0, 150, 300)}
        self.assertEqual(
            resolve_origins(positions, attachments),
            [(10, 20), (500, 0), (160, 320)],
        )
        self.assertEqual(positions[2], GlyphPosition(0))
        self.assertEqual(attachments, {2: GlyphAttachment(0, 150, 300)})

    def test_chains_include_local_offsets_without_changing_advances(self):
        positions = [
            GlyphPosition(500, 0, 10, 20),
            GlyphPosition(0, 0, 5, 6),
            GlyphPosition(0, 0, -2, 3),
            GlyphPosition(200),
        ]
        attachments = {2: GlyphAttachment(1, 25, 50), 1: GlyphAttachment(0, 100, 200)}
        self.assertEqual(
            Font().shape(list("abmc"), positions, attachments=attachments),
            [(10, 20), (115, 226), (138, 279), (500, 0)],
        )

    def test_forward_parent_reference(self):
        self.assertEqual(
            resolve_origins([GlyphPosition(300), GlyphPosition(500)],
                            {0: GlyphAttachment(1, -20, 40)}),
            [(280, 40), (300, 0)],
        )

    def test_attachment_cycles_are_an_explicit_error(self):
        for attachments in [
            {0: GlyphAttachment(0)},
            {0: GlyphAttachment(1), 1: GlyphAttachment(0)},
        ]:
            with self.subTest(attachments=attachments):
                with self.assertRaisesRegex(ValueError, "Cyclic"):
                    resolve_origins([GlyphPosition(500), GlyphPosition(0)], attachments)

    def test_invalid_attachment_indexes_are_an_explicit_error(self):
        for attachments in [
            {-1: GlyphAttachment(0)}, {1: GlyphAttachment(0)},
            {0: GlyphAttachment(-1)}, {0: GlyphAttachment(1)},
        ]:
            with self.subTest(attachments=attachments):
                with self.assertRaisesRegex(ValueError, "indexes"):
                    resolve_origins([GlyphPosition(500)], attachments)

    def test_long_chains_do_not_depend_on_python_recursion(self):
        positions = [GlyphPosition(0) for _ in range(1500)]
        attachments = {i: GlyphAttachment(i - 1, 1, 2) for i in range(1499, 0, -1)}
        self.assertEqual(resolve_origins(positions, attachments)[-1], (1499, 2998))


class PositionTraceTests(unittest.TestCase):
    def test_parent_y_snapshot_keeps_x_dynamic(self):
        positions = [GlyphPosition(500, 0, 10, 30), GlyphPosition(20, 0, 5, 6)]
        attachments = {1: GlyphAttachment(0, 100, 200, parent_y=7)}
        self.assertEqual(resolve_origins(positions, attachments), [(10, 30), (115, 213)])
        resolved = resolve_positions(positions, attachments)
        self.assertEqual(resolved, [positions[0], GlyphPosition(20, 0, -385, 213)])
        self.assertEqual(resolve_origins(resolved), resolve_origins(positions, attachments))
        self.assertEqual(positions[1], GlyphPosition(20, 0, 5, 6))

    def test_snapshots_are_independent_of_later_list_changes(self):
        tokens = ["a", "b"]
        before = [GlyphPosition(500), GlyphPosition(700)]
        after = [before[0].adjusted(x_advance=-50), before[1].adjusted(x_offset=20)]
        application = positioning_application(
            index="0", tokens=tokens, posses=[0, 1], rule="test adjustment",
            before=before, after=after,
        )
        before[0] = GlyphPosition(1)
        after[0] = GlyphPosition(2)
        tokens[0] = "x"
        self.assertEqual(application["tokens"], ("a", "b"))
        self.assertEqual(application["before_positionings"], (GlyphPosition(500), GlyphPosition(700)))
        self.assertEqual(
            application["positionings"],
            (GlyphPosition(450), GlyphPosition(700, 0, 20, 0)),
        )
        self.assertEqual(resolve_origins(application["positionings"]), [(0, 0), (470, 0)])
        with self.assertRaises(FrozenInstanceError):
            application["positionings"][0].x_advance = 0

    def test_snapshot_lengths_must_match_tokens(self):
        with self.assertRaisesRegex(ValueError, "one record per glyph"):
            positioning_application(
                index="0", tokens=["a"], posses=[0], rule=None, before=[], after=[],
            )

    def test_simulator_formats_new_positioning_traces(self):
        simulator = Simulator(Font())
        application = positioning_application(
            index="0", tokens=["a"], posses=[0], rule="test adjustment",
            before=[GlyphPosition(500)], after=[GlyphPosition(450, 0, 10, 20)],
        )
        application["feature"] = "kern"
        simulator.applications = [application]
        text = simulator.steps_str()
        self.assertIn("feature: kern, lookup: 0, pos: 0", text)
        self.assertIn("x_advance=450", text)
        self.assertIn("x_offset=10", text)


class MigrationTests(unittest.TestCase):
    def test_direct_and_extension_pair_execution_and_suppression(self):
        for extension in (False, True):
            with self.subTest(extension=extension):
                source = build_font("feature kern {pos a b -50;} kern;")
                if extension:
                    for lookup in source["GPOS"].table.LookupList.Lookup:
                        subtables = []
                        for subtable in lookup.SubTable:
                            wrapper = otTables.ExtensionPos()
                            wrapper.Format = 1
                            wrapper.ExtensionLookupType = lookup.LookupType
                            wrapper.ExtSubTable = subtable
                            subtables.append(wrapper)
                        lookup.SubTable = subtables
                        lookup.LookupType = 9
                font = load_font(source)
                self.assertEqual(font.GPOS_lookup_list[0].typ, "9/2" if extension else "2")
                self.assertEqual(font.render(["a", "b"])[3], [(0, 0), (450, 0)])
                self.assertEqual(
                    font.render(["a", "b"], suppressed=["kern"])[3], [(0, 0), (500, 0)],
                )
                self.assertEqual(
                    font.render(["a", "b"], feature_values={"kern": 0})[3], [(0, 0), (500, 0)],
                )
                self.assertEqual(
                    load_font(source, positioning=False).render(["a", "b"])[3],
                    [(0, 0), (500, 0)],
                )

    def test_unknown_lookup_entry_is_explicitly_blocked(self):
        with self.assertRaisesRegex(NotImplementedError, "unsupported"):
            GPOS_Lookup(0, "10").apply_at(["a"], [GlyphPosition(500)], 0, Font())


if __name__ == "__main__":
    unittest.main()
