from dataclasses import FrozenInstanceError
import io
from itertools import product
import unittest

from lxml import etree

from test_chained_substitution import build_font
import test_gpos_adjustments as adjustments
from test_gpos_adjustments import wrap_positioning
from test_gpos_foundations import load_font
from gpos import GlyphPosition
from ttxfont import Font, GPOS_Lookup, Simulator
from ttxread import read_GPOS


def positioning_record(sequence=0, lookup=0):
    return (
        '<PosLookupRecord index="0">'
        f'<SequenceIndex value="{sequence}"/><LookupListIndex value="{lookup}"/>'
        '</PosLookupRecord>'
    )


def context_source(subtable, *, chained=True, extension=False):
    source = build_font("feature kern {pos c <10 20 -30 0>;} kern;")
    xml = io.BytesIO()
    source.saveXML(xml, tables=["GPOS"])
    root = etree.fromstring(xml.getvalue())
    root.find(".//Feature/LookupListIndex").set("value", "1")
    typ = 8 if chained else 7
    if extension:
        subtable = (
            '<ExtensionPos index="0" Format="1">'
            f'<ExtensionLookupType value="{typ}"/>{subtable}</ExtensionPos>'
        )
        typ = 9
    root.find(".//LookupList").append(etree.fromstring(
        f'<Lookup index="1"><LookupType value="{typ}"/><LookupFlag value="0"/>'
        + subtable + '</Lookup>'
    ))
    source.importXML(io.BytesIO(etree.tostring(root)))
    return source


class ContextPositioningTests(unittest.TestCase):
    assert_positions = adjustments.PositionAdjustmentTests.assert_positions

    def test_chained_formats_and_extensions(self):
        record = positioning_record()
        subtables = [
            '<ChainContextPos index="0" Format="1">'
            '<Coverage><Glyph value="c"/></Coverage>'
            '<ChainPosRuleSet index="0"><ChainPosRule index="0">'
            '<Backtrack index="0" value="b"/><Backtrack index="1" value="a"/>'
            '<Input index="0" value="q"/><LookAhead index="0" value="d"/>'
            + record + '</ChainPosRule></ChainPosRuleSet></ChainContextPos>',
            '<ChainContextPos index="0" Format="2">'
            '<Coverage><Glyph value="c"/></Coverage>'
            '<BacktrackClassDef><ClassDef glyph="a" class="1"/>'
            '<ClassDef glyph="b" class="2"/></BacktrackClassDef>'
            '<InputClassDef><ClassDef glyph="q" class="1"/></InputClassDef>'
            '<LookAheadClassDef><ClassDef glyph="y" class="1"/></LookAheadClassDef>'
            '<ChainPosClassSet index="0"><ChainPosClassRule index="0">'
            '<Backtrack index="0" value="2"/><Backtrack index="1" value="1"/>'
            '<Input index="0" value="1"/><LookAhead index="0" value="0"/>'
            + record + '</ChainPosClassRule></ChainPosClassSet></ChainContextPos>',
            '<ChainContextPos index="0" Format="3">'
            '<BacktrackCoverage index="0"><Glyph value="b"/></BacktrackCoverage>'
            '<BacktrackCoverage index="1"><Glyph value="a"/></BacktrackCoverage>'
            '<InputCoverage index="0"><Glyph value="c"/></InputCoverage>'
            '<InputCoverage index="1"><Glyph value="q"/></InputCoverage>'
            '<LookAheadCoverage index="0"><Glyph value="d"/></LookAheadCoverage>'
            + record + '</ChainContextPos>',
        ]
        for format, subtable in enumerate(subtables, 1):
            for extension in (False, True):
                source = context_source(subtable, extension=extension)
                for text, count in (("abcqd", 1), ("bacqd", 0), ("abcqy", 0), ("cqd", 0)):
                    with self.subTest(format=format, extension=extension, text=text):
                        positions, applications, _ = self.assert_positions(source, text)
                        self.assertEqual(len(applications), count)
                        if count:
                            self.assertEqual(positions[2], GlyphPosition(470, 0, 10, 20))
                            self.assertEqual(applications[0]["posses"], (2, 3))
                            self.assertEqual(applications[0]["children"][0]["posses"], (2,))

    def test_contextual_formats_and_extensions(self):
        record = positioning_record(1)
        subtables = [
            '<ContextPos index="0" Format="1"><Coverage><Glyph value="a"/></Coverage>'
            '<PosRuleSet index="0"><PosRule index="0"><Input index="0" value="c"/>'
            + record + '</PosRule></PosRuleSet></ContextPos>',
            '<ContextPos index="0" Format="2"><Coverage><Glyph value="a"/></Coverage>'
            '<ClassDef><ClassDef glyph="c" class="1"/></ClassDef>'
            '<PosClassSet index="0"><PosClassRule index="0"><Class index="0" value="1"/>'
            + record + '</PosClassRule></PosClassSet></ContextPos>',
            '<ContextPos index="0" Format="3"><Coverage index="0"><Glyph value="a"/></Coverage>'
            '<Coverage index="1"><Glyph value="c"/></Coverage>'
            + record + '</ContextPos>',
        ]
        for format, subtable in enumerate(subtables, 1):
            for extension in (False, True):
                source = context_source(subtable, chained=False, extension=extension)
                for text, count in (("ac", 1), ("bc", 0), ("aa", 0), ("", 0)):
                    with self.subTest(format=format, extension=extension, text=text):
                        _, applications, _ = self.assert_positions(source, text)
                        self.assertEqual(len(applications), count)

    def test_multiple_records_same_glyph_and_nested_contexts(self):
        source = build_font(
            "lookup first {pos a <10 20 -30 0>;} first;"
            "lookup second {pos a <-5 6 -7 0>;} second;"
            "lookup inner {pos a' lookup first lookup second b;} inner;"
            "feature kern {pos a' lookup inner b;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "ab")
        self.assertEqual(positions[0], GlyphPosition(463, 0, 5, 26))
        children = applications[0]["children"][0]["children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0]["positionings"], children[1]["before_positionings"])

    def test_record_order_is_not_sorted_by_sequence_index(self):
        for reverse in (False, True):
            source = build_font(
                "markClass m <anchor 0 0> @TOP;"
                "lookup move {pos a <40 50 0 0>;} move;"
                "lookup attach {pos base a <anchor 100 200> mark @TOP;} attach;"
                "feature kern {pos a' lookup move m' lookup attach;} kern;"
            )
            context = source["GPOS"].table.LookupList.Lookup[-1].SubTable[0]
            if reverse:
                context.PosLookupRecord.reverse()
            with self.subTest(reverse=reverse):
                _, applications, origins = self.assert_positions(source, "am")
                self.assertEqual(origins[-1], (140, 200 if reverse else 250))
                children = applications[0]["children"]
                self.assertEqual(children[0]["posses"], (1,) if reverse else (0,))

    def test_nested_lookup_flags_do_not_reject_current_glyph(self):
        for action in (
            "pos m <10 20 0 0>;",
            "pos m <10 20 0 0> a <30 40 0 0>;",
        ):
            source = build_font(
                "table GDEF {GlyphClassDef [a b], , [m q], ;} GDEF;"
                "lookup inner {lookupflag IgnoreMarks;" + action + "} inner;"
                "feature kern {pos m' lookup inner;} kern;"
            )
            with self.subTest(action=action):
                positions, _, _ = self.assert_positions(source, "mqa")
                self.assertEqual((positions[0].x_offset, positions[0].y_offset), (10, 20))

    def test_nested_context_flags_filter_context_not_current(self):
        source = build_font(
            "table GDEF {GlyphClassDef [a], , [m q], ;} GDEF;"
            "lookup adjust {pos m <10 20 0 0>;} adjust;"
            "lookup inner {lookupflag IgnoreMarks;pos m' lookup adjust a;} inner;"
            "feature kern {pos m' lookup inner;} kern;"
        )
        positions, _, _ = self.assert_positions(source, "mqa")
        self.assertEqual(positions[0], GlyphPosition(0, 0, 10, 20))

    def test_lookup_flags_mark_classes_and_filtering_sets(self):
        for flag, text in (
            ("IgnoreMarks", "amqbc"),
            ("UseMarkFilteringSet [m]", "aqbc"),
            ("MarkAttachmentType [m]", "aqbc"),
            ("IgnoreBaseGlyphs", "axbc"),
            ("IgnoreLigatures", "aybc"),
        ):
            source = build_font(
                "table GDEF {GlyphClassDef [a b c x], [y], [m q], ;} GDEF;"
                "lookup adjust {pos b <10 20 0 0>;} adjust;"
                "feature kern {lookupflag " + flag + ";pos a b' lookup adjust c;} kern;"
            )
            with self.subTest(flag=flag):
                _, applications, _ = self.assert_positions(source, text)
                # IgnoreBaseGlyphs also filters the current b, preventing scanning.
                self.assertEqual(len(applications), 0 if flag == "IgnoreBaseGlyphs" else 1)

    def test_outer_context_advances_over_input_not_lookahead(self):
        source = build_font(
            "lookup adjust {pos a 10;} adjust;"
            "feature kern {pos a' lookup adjust a;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "aaa")
        self.assertEqual([p.x_advance for p in positions], [510, 510, 500])
        self.assertEqual(len(applications), 2)

    def test_noop_match_advances_over_entire_input_and_blocks_later_rules(self):
        source = build_font(
            "lookup adjust {pos a 10;} adjust;"
            "feature kern {ignore pos a' a';pos a' lookup adjust;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "aaa")
        self.assertEqual([p.x_advance for p in positions], [500, 500, 510])
        self.assertEqual(len(applications), 2)
        self.assertEqual(applications[0]["children"], ())
        self.assertEqual(applications[0]["posses"], (0, 1))

    def test_nonapplicable_child_still_consumes_matched_context(self):
        source = build_font(
            "lookup adjust {pos c 10;} adjust;"
            "feature kern {pos a' lookup adjust a';pos a' 20;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "aaa")
        self.assertEqual([p.x_advance for p in positions], [500, 500, 520])
        self.assertEqual(applications[0]["children"], ())

    def test_nested_pair_outside_input_does_not_consume_outer_next_glyph(self):
        for value in (0, 20):
            source = build_font(
                "lookup pair {pos a -10 b " + str(value) + ";} pair;"
                "lookup move {pos b 40;} move;"
                "feature kern {pos a' lookup pair;pos b' lookup move;} kern;"
            )
            with self.subTest(value=value):
                positions, applications, _ = self.assert_positions(source, "abb")
                self.assertEqual([p.x_advance for p in positions], [490, 540 + value, 540])
                self.assertEqual(len(applications), 3)

    def test_nested_context_consuming_more_input_does_not_extend_outer_match(self):
        source = build_font(
            "lookup adjust {pos a 10;} adjust;"
            "lookup inner {pos a' lookup adjust b';} inner;"
            "lookup move {pos b 40;} move;"
            "feature kern {pos a' lookup inner;pos b' lookup move;} kern;"
        )
        positions, _, _ = self.assert_positions(source, "ab")
        self.assertEqual([p.x_advance for p in positions], [510, 540])

    def test_pair_action_uses_its_own_flags(self):
        source = build_font(
            "table GDEF {GlyphClassDef [a b], , [m], ;} GDEF;"
            "lookup pair {lookupflag IgnoreMarks;pos a b -30;} pair;"
            "feature kern {pos a' lookup pair;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "amb")
        self.assertEqual(positions[0].x_advance, 470)
        self.assertEqual(applications[0]["children"][0]["posses"], (0, 2))

    def test_contextual_mark_attachments_share_state_with_later_lookups(self):
        source = build_font(
            "markClass m <anchor 10 20> @TOP;"
            "lookup attach {pos base a <anchor 110 220> mark @TOP;} attach;"
            "lookup context {pos a m' lookup attach;} context;"
            "lookup move {pos a <40 50 -30 0>;} move;"
            "feature mark {lookup context;lookup move;} mark;"
        )
        _, applications, origins = self.assert_positions(source, "am")
        self.assertEqual(origins, [(40, 50), (140, 200)])
        self.assertEqual(applications[0]["attachments"], applications[1]["before_attachments"])
        self.assertEqual(applications[0]["children"][0]["attachments"], applications[0]["attachments"])

    def test_gsub_component_boundaries_apply_to_positioning_context(self):
        for flag in ("", "IgnoreLigatures"):
            source = build_font(
                "table GDEF {GlyphClassDef [a b], [x], [m q], ;} GDEF;"
                "lookup lig {lookupflag IgnoreMarks;sub a b by x;} lig;"
                "feature calt {lookup lig;} calt;"
                "lookup adjust {pos m <10 20 0 0>;} adjust;"
                "feature kern {" + ("lookupflag " + flag + ";" if flag else "")
                + "pos m' lookup adjust q';} kern;"
            )
            for text, count in (("ambq", 0), ("amqb", 1), ("abmq", 1)):
                with self.subTest(flag=flag, text=text):
                    _, applications, _ = self.assert_positions(source, text)
                    self.assertEqual(sum("positionings" in a for a in applications), count)

    def test_extension_wrapped_nested_context_and_action(self):
        source = wrap_positioning(build_font(
            "lookup adjust {pos b <10 20 -30 0>;} adjust;"
            "lookup inner {pos a b' lookup adjust;} inner;"
            "feature kern {pos a b' lookup inner;} kern;"
        ))
        self.assert_positions(source, "ab")

    def test_nested_mark_to_ligature_uses_gsub_component_provenance(self):
        source = build_font(
            "markClass m <anchor 0 0> @TOP;"
            "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "lookup attach {pos ligature x <anchor 100 200> mark @TOP"
            " ligComponent <anchor 300 400> mark @TOP;} attach;"
            "feature mark {pos m' lookup attach;} mark;"
        )
        _, applications, origins = self.assert_positions(source, "ambm")
        contexts = [a for a in applications if "positionings" in a]
        self.assertEqual(
            [a["children"][0]["ligature_component"] for a in contexts], [1, 2],
        )
        self.assertEqual(origins, [(0, 0), (100, 200), (300, 400)])

    def test_nested_mark_to_mark_uses_prior_context_attachment(self):
        source = build_font(
            "markClass [m q] <anchor 0 0> @TOP;"
            "lookup base {pos base a <anchor 100 200> mark @TOP;} base;"
            "lookup stack {pos mark m <anchor 30 40> mark @TOP;} stack;"
            "feature mark {pos m' lookup base;pos m q' lookup stack;} mark;"
        )
        _, applications, origins = self.assert_positions(source, "amq")
        self.assertEqual(origins, [(0, 0), (100, 200), (130, 240)])
        self.assertEqual(applications[-1]["children"][0]["attachments"][1][1].parent, 1)

    def test_many_sibling_actions_do_not_exhaust_recursion_depth(self):
        source = build_font(
            "lookup adjust {pos a 1;} adjust;feature kern {pos a' "
            + " ".join(["lookup adjust"] * 65) + ";} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "a")
        self.assertEqual(positions[0].x_advance, 565)
        self.assertEqual(len(applications[0]["children"]), 65)

    def test_empty_rule_sets_and_first_matching_rule_precedence(self):
        for format, sets in (
            (1, '<PosRuleSet index="0"/>'
                '<PosRuleSet index="1"><PosRule index="0"/>'
                '<PosRule index="1">' + positioning_record() + '</PosRule></PosRuleSet>'),
            (2, '<ClassDef><ClassDef glyph="c" class="1"/></ClassDef>'
                '<PosClassSet index="0"/>'
                '<PosClassSet index="1"><PosClassRule index="0"/>'
                '<PosClassRule index="1">' + positioning_record()
                + '</PosClassRule></PosClassSet>'),
        ):
            source = context_source(
                f'<ContextPos index="0" Format="{format}">'
                '<Coverage><Glyph value="a"/><Glyph value="c"/></Coverage>'
                + sets + '</ContextPos>', chained=False,
            )
            with self.subTest(format=format):
                positions, applications, _ = self.assert_positions(source, "ac")
                self.assertEqual([p.x_advance for p in positions], [500, 500])
                self.assertEqual(len(applications), 1)
                self.assertEqual(applications[0]["posses"], (1,))
                self.assertEqual(applications[0]["children"], ())

    def test_feature_controls_and_repeated_runs(self):
        source = build_font("feature kern {pos a' 10 b;} kern;")
        self.assert_positions(source, "ab", suppressed=["kern"])
        self.assert_positions(source, "ab", values={"kern": 0})
        font = load_font(source)
        self.assertEqual(font.render(list("ab")), font.render(list("ab")))
        self.assertEqual(font.render([]), ([], [], [], []))

    def test_trace_snapshots_nested_positions_and_simulator_output(self):
        source = build_font(
            "lookup first {pos a 10;} first;lookup second {pos a 20;} second;"
            "feature kern {pos a' lookup first lookup second b;} kern;"
        )
        font = load_font(source)
        tokens, positions, applications = font.apply(list("ab"))
        application = applications[0]
        self.assertEqual(application["before_positionings"][0], GlyphPosition(500))
        self.assertEqual(application["positionings"][0], GlyphPosition(530))
        self.assertEqual(len(application["children"]), 2)
        positions[0] = GlyphPosition(1)
        tokens[0] = "x"
        self.assertEqual(application["children"][0]["positionings"][0], GlyphPosition(510))
        with self.assertRaises(FrozenInstanceError):
            application["positionings"][0].x_advance = 0
        simulator = Simulator(font)
        simulator.set_string("ab")
        self.assertIn("feature: kern", simulator.steps_str())
        self.assertIn("x_advance=530", simulator.steps_str())

    def test_short_contextual_pair_sequences_match_harfbuzz(self):
        source = build_font(
            "lookup pair {pos a -10 b 20;pos b a -30;} pair;"
            "lookup single {pos a <5 -6 0 0>;} single;"
            "feature kern {pos a' lookup pair b';pos b a' lookup single;} kern;"
        )
        for sequence in product("abm", repeat=4):
            text = "".join(sequence)
            with self.subTest(text=text):
                self.assert_positions(source, text)


class ContextPositioningErrorTests(unittest.TestCase):
    def table(self, body):
        return etree.fromstring(
            '<GPOS><LookupList><Lookup index="0"><LookupType value="7"/>'
            '<LookupFlag value="0"/>' + body + '</Lookup></LookupList></GPOS>'
        )

    def test_unsupported_format_and_invalid_sequence_indexes(self):
        with self.assertRaises(NotImplementedError):
            read_GPOS(self.table('<ContextPos Format="4"/>'), Font())
        for index in (-1, 1):
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, "SequenceIndex"):
                    read_GPOS(self.table(
                        '<ContextPos Format="3"><Coverage><Glyph value="a"/></Coverage>'
                        + positioning_record(index) + '</ContextPos>'
                    ), Font())

    def test_missing_coverage_and_missing_record_fields(self):
        for body in (
            '<ContextPos Format="3"/>',
            '<ContextPos Format="1"><PosRuleSet index="0"/></ContextPos>',
            '<ContextPos Format="3"><Coverage><Glyph value="a"/></Coverage>'
            '<PosLookupRecord><SequenceIndex value="0"/></PosLookupRecord></ContextPos>',
        ):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    read_GPOS(self.table(body), Font())

    def test_unknown_nested_lookup_and_recursion_are_explicit_errors(self):
        for target in (0, 99):
            with self.subTest(target=target):
                font = Font()
                read_GPOS(self.table(
                    '<ContextPos Format="3"><Coverage><Glyph value="a"/></Coverage>'
                    + positioning_record(0, target) + '</ContextPos>'
                ), font)
                with self.assertRaisesRegex(ValueError, "recursion|lookup index"):
                    font.GPOS_lookups[0].apply(["a"], [GlyphPosition(500)], font)

    def test_nested_cursive_lookup_executes(self):
        source = build_font(
            "lookup cursive {pos cursive a <anchor NULL> <anchor 100 200>;"
            "pos cursive b <anchor 10 20> <anchor NULL>;} cursive;"
            "feature kern {pos a b' lookup cursive;} kern;"
        )
        self.assertEqual(load_font(source).render(["a", "b"])[1], [
            GlyphPosition(100), GlyphPosition(490, 0, -10, 180),
        ])

    def test_invalid_coverage_rule_set_index_is_an_error(self):
        with self.assertRaises(ValueError):
            read_GPOS(self.table(
                '<ContextPos Format="1"><Coverage><Glyph value="a"/></Coverage>'
                '<PosRuleSet index="3"><PosRule index="0"/>'
                '</PosRuleSet></ContextPos>'
            ), Font())

    def test_negative_lookup_class_and_missing_glyph_are_explicit_errors(self):
        for body in (
            '<ContextPos Format="3"><Coverage><Glyph value="a"/></Coverage>'
            + positioning_record(0, -1) + '</ContextPos>',
            '<ContextPos Format="2"><Coverage><Glyph value="a"/></Coverage>'
            '<ClassDef><ClassDef glyph="a" class="-1"/></ClassDef></ContextPos>',
            '<ContextPos Format="3"><Coverage><Glyph/></Coverage></ContextPos>',
            '<ContextPos Format="1"><Coverage><Glyph value="a"/></Coverage>'
            '<PosRuleSet><PosRule index="0"/></PosRuleSet></ContextPos>',
        ):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    read_GPOS(self.table(body), Font())


if __name__ == "__main__":
    unittest.main()
