import io
from pathlib import Path
import sys
import unittest

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from lxml import etree
import uharfbuzz as hb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ttxfont import (
    ChainSubstitution3, Font, GSUB_Lookup, LigSubstitution,
    Simulator, SingleSubstitution1, filter_glyph,
)
from ttxread import read_GSUB, read_ttx


GLYPHS = [".notdef", "a", "b", "c", "d", "x", "y", "z", "q", "m"]


def build_font(features):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(GLYPHS)
    builder.setupCharacterMap({ord(name): name for name in GLYPHS if len(name) == 1})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in GLYPHS})
    builder.setupHorizontalMetrics({name: (500, 0) for name in GLYPHS})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Chain tests", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.setupMaxp()
    addOpenTypeFeaturesFromString(builder.font, features)
    return builder.font


def read_substitutions(source):
    output = io.BytesIO()
    source.saveXML(output, tables=["GSUB"])
    font = Font()
    font.glyphs = source.getGlyphOrder()
    font.width = {glyph: metrics[0] for glyph, metrics in source["hmtx"].metrics.items()}
    if "GDEF" in source:
        gdef = source["GDEF"].table
        if gdef.GlyphClassDef is not None:
            font.glyph_to_class = gdef.GlyphClassDef.classDefs.copy()
        if gdef.MarkAttachClassDef is not None:
            font.mark_to_class = gdef.MarkAttachClassDef.classDefs.copy()
        if getattr(gdef, "MarkGlyphSetsDef", None) is not None:
            font.index_to_glyphs = {
                index: coverage.glyphs
                for index, coverage in enumerate(gdef.MarkGlyphSetsDef.Coverage)
            }
    read_GSUB(etree.fromstring(output.getvalue()).find("GSUB"), font)
    return font


def reference_shape(source, text, features=None, script="latn"):
    output = io.BytesIO()
    source.save(output)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction = "ltr"
    buffer.script = script
    hb.shape(hb.Font(hb.Face(output.getvalue())), buffer, features or {"calt": True})
    order = source.getGlyphOrder()
    return [order[info.codepoint] for info in buffer.glyph_infos]


class ChainedSubstitutionTests(unittest.TestCase):
    def assert_shapes(self, features, text, expected):
        source = build_font(features)
        self.assertEqual(reference_shape(source, text), expected)
        font = read_substitutions(source)
        actual, _, applications = font.apply(list(text))
        self.assertEqual(actual, expected)
        return applications

    def test_records_index_the_modified_input_sequence(self):
        for rule, expected in [
            ("sub a by x y;", ["x", "q", "b", "c"]),
            ("sub a b by x;", ["x", "q"]),
            ("sub a by NULL;", ["b", "q"]),
        ]:
            with self.subTest(rule=rule):
                self.assert_shapes(
                    "lookup first {" + rule + "} first;"
                    "lookup second {sub b by z; sub y by q; sub c by q;} second;"
                    "feature calt {sub a' lookup first b' lookup second c';} calt;",
                    "abc", expected,
                )

    def test_backtrack_order_and_lookahead(self):
        self.assert_shapes(
            "feature calt {sub a b c' d by x;} calt;",
            "abcdcbcd", ["a", "b", "x", "d", "c", "b", "c", "d"],
        )

    def test_no_backtrack_after_first_position(self):
        self.assert_shapes(
            "feature calt {sub a' b by x;} calt;", "caab", ["c", "a", "x", "b"],
        )

    def test_first_matching_rule_wins_not_longest(self):
        self.assert_shapes(
            "feature calt {sub a' b by x; sub a' b c by y;} calt;",
            "abc", ["x", "b", "c"],
        )

    def test_advance_past_input_but_not_lookahead(self):
        self.assert_shapes(
            "lookup change {sub a by x;} change;"
            "feature calt {sub a' lookup change a';} calt;",
            "aaa", ["x", "a", "a"],
        )
        self.assert_shapes(
            "feature calt {sub a' a by x;} calt;", "aaa", ["x", "x", "a"],
        )

    def test_noop_match_blocks_later_rules_and_skips_input(self):
        self.assert_shapes(
            "feature calt {ignore sub a' a'; sub a' by x;} calt;",
            "aaa", ["a", "a", "x"],
        )

    def test_nested_lookup_uses_its_own_flags(self):
        self.assert_shapes(
            "@BASE = [a b x]; @MARK = [m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "lookup change {lookupflag IgnoreMarks; sub m by x;} change;"
            "feature calt {sub a m' lookup change b;} calt;",
            "amb", ["a", "x", "b"],
        )
        self.assert_shapes(
            "@BASE = [a b x]; @MARK = [m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "lookup change {lookupflag IgnoreMarks; sub a b by x;} change;"
            "feature calt {sub a' lookup change m' b';} calt;",
            "amb", ["x", "m"],
        )

    def test_top_level_lookup_skips_ignored_glyphs(self):
        self.assert_shapes(
            "@BASE = [a b x]; @MARK = [m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag IgnoreMarks; sub m by x;} calt;",
            "amb", ["a", "m", "b"],
        )

    def test_ignore_marks_in_all_contexts(self):
        self.assert_shapes(
            "@BASE = [a b c d x]; @MARK = [m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag IgnoreMarks; sub a b c' d by x;} calt;",
            "ambmcmmmd", ["a", "m", "b", "m", "x", "m", "m", "m", "d"],
        )

    def test_nested_contextual_substitution(self):
        applications = self.assert_shapes(
            "lookup inner {sub a' b by x;} inner;"
            "feature calt {sub c a' lookup inner b;} calt;",
            "cab", ["c", "x", "b"],
        )
        self.assertIn("/", applications[0]["index"])

    def test_repeated_and_out_of_order_records(self):
        self.assert_shapes(
            "lookup first {sub b by y;} first;"
            "lookup second {sub a by x;} second;"
            "lookup third {sub x by z;} third;"
            "feature calt {sub a' lookup second lookup third b' lookup first;} calt;",
            "ab", ["z", "y"],
        )

    def test_records_are_not_sorted_by_sequence_index(self):
        source = build_font(
            "lookup change_b {sub b by y;} change_b;"
            "lookup change_a {sub a' y by x;} change_a;"
            "feature calt {sub a' lookup change_a b' lookup change_b;} calt;"
        )
        outer = source["GSUB"].table.LookupList.Lookup[-1]
        outer.SubTable[0].SubstLookupRecord.reverse()
        self.assertEqual(reference_shape(source, "ab"), ["x", "y"])
        self.assertEqual(read_substitutions(source).apply(list("ab"))[0], ["x", "y"])

    def test_ignored_base_and_ligature_classes(self):
        for flag, classes in [
            ("IgnoreBaseGlyphs", "GlyphClassDef [m], , , ;"),
            ("IgnoreLigatures", "GlyphClassDef , [m], , ;"),
        ]:
            with self.subTest(flag=flag):
                self.assert_shapes(
                    "table GDEF {" + classes + "} GDEF;"
                    "feature calt {lookupflag " + flag + "; sub a' b by x;} calt;",
                    "amb", ["x", "m", "b"],
                )

    def test_mark_filters_do_not_filter_bases_or_unclassified_glyphs(self):
        font = Font()
        font.glyph_to_class = {"a": 1, "m": 3, "q": 3}
        font.mark_to_class = {"m": 1, "q": 2}
        font.index_to_glyphs = {0: ["q"]}
        lookup = GSUB_Lookup(0, "6")
        lookup.mark_class = 1
        self.assertTrue(filter_glyph("a", font, lookup))
        self.assertTrue(filter_glyph("x", font, lookup))
        self.assertTrue(filter_glyph("m", font, lookup))
        self.assertFalse(filter_glyph("q", font, lookup))
        lookup.filter_set = 0
        self.assertTrue(filter_glyph("q", font, lookup))
        self.assertFalse(filter_glyph("m", font, lookup))
        lookup.ignore_marks = True
        self.assertFalse(filter_glyph("q", font, lookup))

    def test_recursion_reports_an_error(self):
        font = Font()
        lookup = GSUB_Lookup(0, "6")
        lookup.add(ChainSubstitution3([], ["a"], [], [(0, 0)]))
        font.add_GSUB_lookup(0, lookup)
        with self.assertRaisesRegex(ValueError, "recurs"):
            lookup.apply(["a"], font)

    def test_ligature_consumes_only_matching_positions(self):
        font = Font()
        font.glyph_to_class = {"m": 3}
        outer = GSUB_Lookup(0, "6")
        outer.add(ChainSubstitution3([], ["a", "m", "b", "c"], [], [(0, 1), (1, 2)]))
        ligature = GSUB_Lookup(1, "4")
        ligature.ignore_marks = True
        ligature.add(LigSubstitution(["a", "b"], "x"))
        change = GSUB_Lookup(2, "1")
        change.add(SingleSubstitution1("m", "y"))
        for lookup in [outer, ligature, change]:
            font.add_GSUB_lookup(lookup.index, lookup)
        actual, _ = outer.apply(["a", "m", "b", "c"], font)
        self.assertEqual(actual, ["x", "y", "c"])

    def test_nested_edits_update_the_outer_input_sequence(self):
        self.assert_shapes(
            "lookup expand {sub a by x y;} expand;"
            "lookup inner {sub a' lookup expand b';} inner;"
            "lookup change {sub y by q;} change;"
            "feature calt {sub a' lookup inner b' lookup change c';} calt;",
            "abc", ["x", "q", "b", "c"],
        )

    def test_deletion_can_exhaust_the_input_sequence(self):
        source = build_font(
            "lookup delete {sub a by NULL;} delete;"
            "lookup change {sub b by x;} change;"
            "feature calt {sub a' lookup delete lookup change;} calt;"
        )
        # A deleted input must not retarget a later record onto lookahead glyphs.
        self.assertEqual(read_substitutions(source).apply(list("aab"))[0], ["b"])

    def test_reference_outside_shortened_input_is_not_applied_to_lookahead(self):
        self.assert_shapes(
            "lookup lig {sub a b by x;} lig;"
            "lookup change {sub c by y;} change;"
            "feature calt {sub a' lookup lig b' lookup change c;} calt;",
            "abc", ["x", "c"],
        )

    def test_failed_nested_action_still_consumes_the_matched_context(self):
        self.assert_shapes(
            "lookup miss {sub b by x;} miss;"
            "feature calt {sub a' lookup miss a'; sub a' by y;} calt;",
            "aaa", ["a", "a", "y"],
        )

    def test_mark_filtering_set(self):
        self.assert_shapes(
            "@BASE = [a b x]; @MARK = [m q];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag UseMarkFilteringSet [m]; sub a' b by x;} calt;",
            "aqbamb", ["x", "q", "b", "a", "m", "b"],
        )

    def test_mark_attachment_class(self):
        self.assert_shapes(
            "@BASE = [a b x]; @MARK = [m q];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag MarkAttachmentType [m]; sub a' b by x;} calt;",
            "aqbamb", ["x", "q", "b", "a", "m", "b"],
        )

    def test_shared_lookup_runs_if_any_referencing_feature_is_enabled(self):
        source = build_font(
            "lookup shared {sub a' b by x;} shared;"
            "feature calt {lookup shared;} calt;"
            "feature ss01 {lookup shared;} ss01;"
        )
        font = read_substitutions(source)
        expected = reference_shape(source, "ab", {"calt": True, "ss01": False})
        actual, _, applications = font.apply(list("ab"), suppressed=["ss01"])
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["x", "b"])
        self.assertEqual(applications[0]["feature"], "calt")
        self.assertEqual(font.apply(list("ab"), suppressed=["calt", "ss01"])[0], ["a", "b"])

    def test_trace_keeps_before_after_and_nested_applications(self):
        source = build_font(
            "lookup expand {sub a by x y;} expand;"
            "feature calt {sub a' lookup expand b;} calt;"
        )
        tokens = ["a", "b"]
        actual, _, applications = read_substitutions(source).apply(tokens)
        self.assertEqual(tokens, ["a", "b"])
        self.assertEqual(actual, ["x", "y", "b"])
        application = applications[0]
        self.assertEqual(application["before"], ["a", "b"])
        self.assertEqual(application["tokens"], ["x", "y", "b"])
        self.assertEqual(application["posses"], [0])
        self.assertEqual(len(application["children"]), 1)
        self.assertEqual(application["children"][0]["before"], ["a", "b"])

    def test_simulator_accepts_empty_results(self):
        source = build_font(
            "lookup delete {sub a by NULL;} delete;"
            "feature calt {sub a' lookup delete;} calt;"
        )
        simulator = Simulator(read_substitutions(source))
        simulator.set_tokens(["a", "a"])
        self.assertEqual(simulator.tokens, [])
        self.assertEqual(simulator.places, [])
        self.assertIn("feature: calt", simulator.steps_str())
        self.assertEqual(simulator.shaped_str(), "Shaped\n")
        simulator.set_tokens([])
        self.assertEqual(simulator.steps_str(), "")

    def test_unknown_nested_lookup_is_an_explicit_error(self):
        font = Font()
        lookup = GSUB_Lookup(0, "6")
        lookup.add(ChainSubstitution3([], ["a"], [], [(0, 99)]))
        with self.assertRaisesRegex(ValueError, "lookup index: 99"):
            lookup.apply(["a"], font)


class ContextParserTests(unittest.TestCase):
    def source_with_context(self, subtable, extension=False, chained=True):
        source = build_font("feature calt {sub c by x;} calt;")
        output = io.BytesIO()
        source.saveXML(output, tables=["GSUB"])
        root = etree.fromstring(output.getvalue())
        root.find(".//Feature/LookupListIndex").set("value", "1")
        typ = 6 if chained else 5
        if extension:
            subtable = (
                '<ExtensionSubst index="0" Format="1">'
                f'<ExtensionLookupType value="{typ}"/>{subtable}</ExtensionSubst>'
            )
            typ = 7
        lookup = etree.fromstring(
            f'<Lookup index="1"><LookupType value="{typ}"/>'
            f'<LookupFlag value="0"/>{subtable}</Lookup>'
        )
        root.find(".//LookupList").append(lookup)
        source.importXML(io.BytesIO(etree.tostring(root)))
        return source

    def test_chained_formats_and_extensions(self):
        record = (
            '<SubstLookupRecord index="0"><SequenceIndex value="0"/>'
            '<LookupListIndex value="0"/></SubstLookupRecord>'
        )
        subtables = [
            '<ChainContextSubst index="0" Format="1">'
            '<Coverage><Glyph value="c"/></Coverage>'
            '<ChainSubRuleSet index="0"><ChainSubRule index="0">'
            '<Backtrack index="0" value="b"/><Backtrack index="1" value="a"/>'
            '<Input index="0" value="q"/>'
            '<LookAhead index="0" value="d"/>'
            + record + '</ChainSubRule></ChainSubRuleSet></ChainContextSubst>',
            '<ChainContextSubst index="0" Format="2">'
            '<Coverage><Glyph value="c"/></Coverage>'
            '<BacktrackClassDef><ClassDef glyph="a" class="1"/>'
            '<ClassDef glyph="b" class="2"/></BacktrackClassDef>'
            '<InputClassDef><ClassDef glyph="q" class="1"/></InputClassDef>'
            '<LookAheadClassDef><ClassDef glyph="y" class="1"/></LookAheadClassDef>'
            '<ChainSubClassSet index="0"><ChainSubClassRule index="0">'
            '<Backtrack index="0" value="2"/><Backtrack index="1" value="1"/>'
            '<Input index="0" value="1"/>'
            '<LookAhead index="0" value="0"/>'
            + record + '</ChainSubClassRule></ChainSubClassSet></ChainContextSubst>',
            '<ChainContextSubst index="0" Format="3">'
            '<BacktrackCoverage index="0"><Glyph value="b"/></BacktrackCoverage>'
            '<BacktrackCoverage index="1"><Glyph value="a"/></BacktrackCoverage>'
            '<InputCoverage index="0"><Glyph value="c"/></InputCoverage>'
            '<InputCoverage index="1"><Glyph value="q"/></InputCoverage>'
            '<LookAheadCoverage index="0"><Glyph value="d"/></LookAheadCoverage>'
            + record + '</ChainContextSubst>',
        ]
        for format, subtable in enumerate(subtables, 1):
            for extension in (False, True):
                with self.subTest(format=format, extension=extension):
                    source = self.source_with_context(subtable, extension)
                    font = read_substitutions(source)
                    for text, expected in [
                        ("abcqd", ["a", "b", "x", "q", "d"]),
                        ("bacqd", list("bacqd")),
                        ("abcqy", list("abcqy")),
                    ]:
                        self.assertEqual(reference_shape(source, text), expected)
                        self.assertEqual(font.apply(list(text))[0], expected)

    def test_contextual_formats_and_extensions(self):
        record = (
            '<SubstLookupRecord index="0"><SequenceIndex value="1"/>'
            '<LookupListIndex value="0"/></SubstLookupRecord>'
        )
        subtables = [
            '<ContextSubst index="0" Format="1">'
            '<Coverage><Glyph value="a"/></Coverage>'
            '<SubRuleSet index="0"><SubRule index="0">'
            '<Input index="0" value="c"/>'
            + record + '</SubRule></SubRuleSet></ContextSubst>',
            '<ContextSubst index="0" Format="2">'
            '<Coverage><Glyph value="a"/></Coverage>'
            '<ClassDef><ClassDef glyph="c" class="1"/></ClassDef>'
            '<SubClassSet index="0"><SubClassRule index="0">'
            '<Class index="0" value="1"/>'
            + record + '</SubClassRule></SubClassSet></ContextSubst>',
            '<ContextSubst index="0" Format="3">'
            '<Coverage index="0"><Glyph value="a"/></Coverage>'
            '<Coverage index="1"><Glyph value="c"/></Coverage>'
            + record + '</ContextSubst>',
        ]
        for format, subtable in enumerate(subtables, 1):
            for extension in (False, True):
                with self.subTest(format=format, extension=extension):
                    source = self.source_with_context(subtable, extension, chained=False)
                    font = read_substitutions(source)
                    for text, expected in [("ac", ["a", "x"]), ("bc", ["b", "c"])]:
                        self.assertEqual(reference_shape(source, text), expected)
                        self.assertEqual(font.apply(list(text))[0], expected)

    def test_minimal_font_loads_without_optional_tables(self):
        source = build_font("feature calt {sub a' b by x;} calt;")
        output = io.BytesIO()
        source.saveXML(output)
        output.seek(0)
        font = read_ttx(output, positioning=False)
        self.assertEqual(font.string_to_tokens("ab"), ["a", "b"])
        self.assertEqual(font.apply(["a", "b"])[0], ["x", "b"])

    def test_unsupported_subtable_is_an_explicit_error(self):
        font = Font()
        table = etree.fromstring(
            '<GSUB><LookupList><Lookup index="0">'
            '<LookupType value="3"/><LookupFlag value="0"/>'
            '<UnknownSubst index="0"/></Lookup></LookupList></GSUB>'
        )
        with self.assertRaisesRegex(NotImplementedError, "UnknownSubst"):
            read_GSUB(table, font)

    def test_invalid_sequence_index_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "SequenceIndex"):
            ChainSubstitution3([], ["a"], [], [(1, 0)])

    def test_unsupported_context_format_is_an_explicit_error(self):
        table = etree.fromstring(
            '<GSUB><LookupList><Lookup index="0">'
            '<LookupType value="6"/><LookupFlag value="0"/>'
            '<ChainContextSubst index="0" Format="4"/></Lookup></LookupList></GSUB>'
        )
        with self.assertRaisesRegex(NotImplementedError, "format: 4"):
            read_GSUB(table, Font())


if __name__ == "__main__":
    unittest.main()
