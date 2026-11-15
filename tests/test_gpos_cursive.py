from copy import deepcopy
from itertools import product
import unittest

from lxml import etree

from test_chained_substitution import build_font
from test_gpos_adjustments import reference_positions, wrap_positioning
from test_gpos_foundations import load_font
from gpos import GlyphAttachment, GlyphPosition, resolve_origins
from ttxfont import Font, Simulator
from ttxread import read_GPOS


ANCHORS = (
    "pos cursive a <anchor 10 20> <anchor 310 120>;"
    "pos cursive b <anchor 30 70> <anchor 350 180>;"
    "pos cursive c <anchor 50 80> <anchor 400 200>;"
)


def cursive_source(flag=""):
    return build_font(
        "table GDEF {GlyphClassDef [a b c d], [x], [m q], ;} GDEF;"
        "feature curs {" + ("lookupflag " + flag + ";" if flag else "") + ANCHORS + "} curs;"
    )


class CursivePositioningTests(unittest.TestCase):
    def assert_run(self, source, text, direction="ltr", *, suppressed=()):
        if "GDEF" in source and source["GDEF"].table.GlyphClassDef is not None:
            for glyph, cls in source["GDEF"].table.GlyphClassDef.classDefs.items():
                if cls == 3:
                    source["hmtx"].metrics[glyph] = (0, 0)
        expected = reference_positions(
            source, text, {tag: False for tag in suppressed} or {"curs": True},
            direction=direction, script="arab" if direction == "rtl" else "latn",
        )
        font = load_font(source)
        tokens, positions, applications, origins = font.render(
            list(text), direction=direction, suppressed=suppressed,
        )
        self.assertEqual((tokens, positions, origins), expected)
        self.assertEqual(origins, font.shape(tokens, positions, direction=direction))
        return positions, applications, origins

    def test_default_ltr_chain_has_explicit_advances_and_offsets(self):
        _, positions, applications, origins = load_font(cursive_source()).render(list("abc"))
        self.assertEqual(positions, [
            GlyphPosition(310), GlyphPosition(320, 0, -30, 50),
            GlyphPosition(450, 0, -50, 150),
        ])
        self.assertEqual(origins, [(0, 0), (280, 50), (580, 150)])
        self.assertEqual([a["posses"] for a in applications], [(0, 1), (1, 2)])

    def test_directions_lookup_flags_and_extensions(self):
        for direction, flag, extension in product(("ltr", "rtl"), ("", "RightToLeft"), (False, True)):
            source = cursive_source(flag)
            if extension:
                wrap_positioning(source)
            with self.subTest(direction=direction, flag=flag, extension=extension):
                positions, applications, _ = self.assert_run(source, "abc", direction)
                self.assertEqual([p.y_offset for p in positions], [-150, -100, 0] if flag else [0, 50, 150])
                self.assertEqual(len(applications), 2)

    def test_missing_anchors_uncovered_glyphs_and_empty_runs(self):
        source = build_font(
            "feature curs {pos cursive a <anchor NULL> <anchor 310 120>;"
            "pos cursive b <anchor 30 70> <anchor NULL>;"
            "pos cursive c <anchor NULL> <anchor NULL>;} curs;"
        )
        for direction, text in product(("ltr", "rtl"), ("", "a", "ab", "abc", "acb", "adb", "ba")):
            with self.subTest(direction=direction, text=text):
                self.assert_run(source, text, direction)

    def test_filtering_and_ignored_intervening_advances(self):
        for flag, text in (
            ("IgnoreMarks", "amqbc"), ("UseMarkFilteringSet [m]", "aqbc"),
            ("MarkAttachmentType [m]", "aqbc"), ("IgnoreLigatures", "axbc"),
            ("", "ambc"),
        ):
            for direction in ("ltr", "rtl"):
                with self.subTest(flag=flag, direction=direction):
                    self.assert_run(cursive_source(flag), text, direction)

    def test_scalar_placement_before_and_after_cursive(self):
        for before in (False, True):
            scalar = "pos a <11 13 17 0>;pos b <19 23 29 0>;pos c <31 37 41 0>;"
            for direction, flag in product(("ltr", "rtl"), ("", "RightToLeft")):
                source = build_font(
                    ("lookup scalar {" + scalar + "} scalar;" if before else "")
                    + "feature curs {" + ("lookupflag " + flag + ";" if flag else "")
                    + ANCHORS + "} curs;"
                    + ("feature kern {lookup scalar;} kern;" if before else
                       "feature kern {" + scalar + "} kern;")
                )
                with self.subTest(before=before, direction=direction, flag=flag):
                    self.assert_run(source, "abc", direction)

    def test_overlapping_lookups_reverse_existing_cursive_chains(self):
        for direction in ("ltr", "rtl"):
            for first, second in (("", "RightToLeft"), ("RightToLeft", ""), ("", ""), ("RightToLeft", "RightToLeft")):
                source = build_font(
                    "lookup first {" + ("lookupflag " + first + ";" if first else "") + ANCHORS + "} first;"
                    "lookup second {" + ("lookupflag " + second + ";" if second else "") + ANCHORS + "} second;"
                    "feature curs {lookup first;lookup second;} curs;"
                )
                with self.subTest(direction=direction, first=first, second=second):
                    self.assert_run(source, "abca", direction)

    def test_marks_before_and_after_cursive_share_attachment_state(self):
        for before, direction, flag in product((False, True), ("ltr", "rtl"), ("", "RightToLeft")):
            mark = "lookup marks {pos base [a b c] <anchor 100 200> mark @TOP;} marks;"
            curs = "lookup join {" + ("lookupflag " + flag + " IgnoreMarks;" if flag else "lookupflag IgnoreMarks;") + ANCHORS + "} join;"
            source = build_font(
                "markClass m <anchor 10 20> @TOP;"
                + (mark + curs if before else curs + mark)
                + "feature curs {lookup join;lookup marks;} curs;"
            )
            with self.subTest(before=before, direction=direction, flag=flag):
                self.assert_run(source, "ambmcm", direction)

    def test_nested_cursive_targets_current_entry_and_uses_own_flags(self):
        for direction in ("ltr", "rtl"):
            source = build_font(
                "table GDEF {GlyphClassDef [a b c], , [m], ;} GDEF;"
                "lookup join {lookupflag IgnoreMarks;" + ANCHORS + "} join;"
                "feature curs {pos b' lookup join;pos c' lookup join;} curs;"
            )
            with self.subTest(direction=direction):
                _, applications, _ = self.assert_run(source, "ambc", direction)
                self.assertEqual([a["children"][0]["posses"] for a in applications], [(0, 2), (2, 3)])

    def test_cursive_and_mark_reattachment_replace_each_others_links(self):
        anchors = ANCHORS.replace("cursive b", "cursive m")
        for before, direction, flag in product((False, True), ("ltr", "rtl"), ("", "RightToLeft")):
            mark = "lookup marks {pos base a <anchor 110 220> mark @TOP;} marks;"
            curs = "lookup join {" + ("lookupflag " + flag + ";" if flag else "") + anchors + "} join;"
            source = build_font(
                "markClass m <anchor 10 20> @TOP;"
                + (mark + curs if before else curs + mark)
                + "feature curs {lookup join;lookup marks;} curs;"
            )
            # Keep explicit cursive advances: this is a lookup-only, unclassified
            # mark fixture, outside HarfBuzz's script-dependent zeroing policy.
            source["GDEF"].table.GlyphClassDef.classDefs["m"] = 0
            with self.subTest(before=before, direction=direction, flag=flag):
                self.assert_run(source, "amc", direction)

    def test_missing_anchor_tries_next_subtable_and_covered_record_wins(self):
        source = cursive_source()
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        first = deepcopy(lookup.SubTable[0])
        first.EntryExitRecord[0].ExitAnchor = None
        lookup.SubTable.insert(0, first)
        for direction in ("ltr", "rtl"):
            with self.subTest(direction=direction):
                self.assert_run(source, "abc", direction)

    def test_nested_cursive_bypasses_current_glyph_filter(self):
        source = build_font(
            "table GDEF {GlyphClassDef [b c], [a], , ;} GDEF;"
            "lookup join {lookupflag IgnoreBaseGlyphs;" + ANCHORS + "} join;"
            "feature curs {pos b' lookup join;} curs;"
        )
        for direction in ("ltr", "rtl"):
            with self.subTest(direction=direction):
                _, applications, _ = self.assert_run(source, "ab", direction)
                self.assertEqual(applications[0]["children"][0]["posses"], (0, 1))
                self.assertEqual(applications[0]["children"][0]["direction"], direction)

    def test_standalone_lookup_entry_and_trace_snapshots_use_direction(self):
        source = cursive_source("RightToLeft")
        font = load_font(source)
        tokens = list("ab")
        expected = reference_positions(source, "ab", direction="rtl", script="arab")[1]
        lookup = font.GPOS_lookup_list[0]
        positions, applications = lookup.apply(
            tokens, font.initial_positions(tokens), font, direction="rtl",
        )
        self.assertEqual(positions, expected)
        positions, application, jump = lookup.apply_at(
            tokens, font.initial_positions(tokens), 1, font, direction="rtl",
        )
        self.assertEqual(positions, expected)
        self.assertEqual(jump, 1)
        self.assertEqual(application, applications[0])
        self.assertEqual(application["direction"], "rtl")
        self.assertEqual(application["tokens"], ("a", "b"))
        self.assertEqual(application["posses"], (0, 1))
        positions[0] = GlyphPosition(1)
        self.assertEqual(application["positionings"], tuple(expected))

    def test_feature_controls_repeated_runs_and_simulator_direction(self):
        source = cursive_source()
        self.assert_run(source, "abc", "rtl", suppressed=("curs",))
        font = load_font(source)
        self.assertEqual(font.render(list("abc"), direction="rtl"), font.render(list("abc"), direction="rtl"))
        self.assertEqual(font.render(["a", "b"], feature_values={"curs": 0}, direction="rtl")[3], [(500, 0), (0, 0)])
        simulator = Simulator(font)
        simulator.direction = "rtl"
        simulator.set_string("abc")
        self.assertEqual(simulator.places, font.render(list("abc"), direction="rtl")[3])
        self.assertIn("feature: curs", simulator.steps_str())

    def test_rtl_scalar_pair_context_and_mark_positioning(self):
        for rules, text in (
            ("feature kern {pos a <10 20 -30 0>;} kern;", "abc"),
            ("feature kern {pos a <10 20 -30 0> b <40 50 -60 0>;} kern;", "abc"),
            ("lookup pair {pos a b -30;} pair;feature kern {pos a' lookup pair;} kern;", "abc"),
            ("markClass m <anchor 10 20> @TOP;feature mark {pos base a <anchor 110 220> mark @TOP;} mark;", "am"),
            ("markClass m <anchor 10 20> @TOP;feature mark {pos ligature x <anchor 110 220> mark @TOP;} mark;", "xm"),
            ("markClass [m q] <anchor 0 0> @TOP;feature mkmk {pos mark m <anchor 100 200> mark @TOP;} mkmk;", "mq"),
        ):
            with self.subTest(rules=rules):
                self.assert_run(build_font(rules), text, "rtl")

    def test_rtl_gsub_component_provenance_and_mark_origins(self):
        source = build_font(
            "markClass m <anchor 0 0> @TOP;"
            "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "feature mark {pos ligature x <anchor 100 200> mark @TOP"
            " ligComponent <anchor 300 400> mark @TOP;} mark;"
        )
        _, applications, origins = self.assert_run(source, "ambm", "rtl")
        self.assertEqual(origins, [(0, 0), (100, 200), (300, 400)])
        self.assertEqual(
            [a["ligature_component"] for a in applications if "ligature_component" in a],
            [1, 2],
        )

    def test_exhaustive_short_chains(self):
        for direction, flag in product(("ltr", "rtl"), ("", "RightToLeft")):
            source = cursive_source(flag)
            for sequence in product("abc", repeat=3):
                with self.subTest(direction=direction, flag=flag, sequence=sequence):
                    self.assert_run(source, "".join(sequence), direction)


class CursiveParserAndDirectionTests(unittest.TestCase):
    def test_invalid_direction_rejected_consistently(self):
        font = load_font(cursive_source())
        for direction in ("ttb", "btt", "", "RTL"):
            for action in (
                lambda: font.apply([], direction=direction),
                lambda: font.render([], direction=direction),
                lambda: font.shape([], [], direction=direction),
                lambda: resolve_origins([], direction=direction),
            ):
                with self.subTest(direction=direction):
                    with self.assertRaisesRegex(ValueError, "direction"):
                        action()

    def test_malformed_cursive_tables_fail_explicitly(self):
        for subtable, error in (
            ('<CursivePos Format="2"/>', NotImplementedError),
            ('<CursivePos Format="1"/>', ValueError),
            ('<CursivePos Format="1"><Coverage><Glyph value="a"/></Coverage></CursivePos>', ValueError),
            ('<CursivePos Format="1"><Coverage><Glyph value="a"/><Glyph value="a"/></Coverage></CursivePos>', ValueError),
            ('<CursivePos Format="1"><Coverage><Glyph value="a"/></Coverage><EntryExitRecord index="2"/></CursivePos>', ValueError),
            ('<CursivePos Format="1"><Coverage><Glyph value="a"/></Coverage>'
             '<EntryExitRecord index="0"><EntryAnchor Format="3">'
             '<XCoordinate value="0"/><YCoordinate value="0"/><XDeviceTable/>'
             '</EntryAnchor></EntryExitRecord></CursivePos>', NotImplementedError),
        ):
            with self.subTest(subtable=subtable):
                with self.assertRaises(error):
                    read_GPOS(etree.fromstring(
                        '<GPOS><LookupList><Lookup index="0"><LookupType value="3"/>'
                        '<LookupFlag value="0"/>' + subtable + '</Lookup></LookupList></GPOS>'
                    ), Font())

    def test_cursive_links_follow_only_parent_y(self):
        positions = [GlyphPosition(500, 0, 30, 40), GlyphPosition(500, 0, 10, 20)]
        self.assertEqual(resolve_origins(
            positions, {1: GlyphAttachment(0, y_offset=70, cursive=True)}, direction="rtl",
        ), [(530, 40), (10, 130)])

    def test_mixed_axis_cycle_is_valid_but_same_axis_cycle_is_not(self):
        positions = [GlyphPosition(500), GlyphPosition(500)]
        links = {
            0: GlyphAttachment(1, y_offset=70, cursive=True),
            1: GlyphAttachment(0, 10, 20, parent_y=30),
        }
        self.assertEqual(resolve_origins(positions, links), [(0, 120), (10, 50)])
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            resolve_origins(positions, {
                0: GlyphAttachment(1, cursive=True), 1: GlyphAttachment(0, cursive=True),
            })


if __name__ == "__main__":
    unittest.main()
