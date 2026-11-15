import io
import unittest

from fontTools.ttLib.tables import otTables

from test_chained_substitution import (
    build_font, read_substitutions, reference_shape, read_ttx, Simulator,
)
from ttxfont import AlternateSubstitution, ReverseSubstitution
from ttxread import read_reverse_subst
from lxml import etree


def wrap_extensions(source):
    for lookup in source["GSUB"].table.LookupList.Lookup:
        subtables = []
        for subtable in lookup.SubTable:
            extension = otTables.ExtensionSubst()
            extension.Format = 1
            extension.ExtensionLookupType = lookup.LookupType
            extension.ExtSubTable = subtable
            subtables.append(extension)
        lookup.SubTable = subtables
        lookup.LookupType = 7
    return source


class AlternateReverseTests(unittest.TestCase):
    def assert_shapes(self, features, text, expected, values=None, extension=False):
        source = build_font(features)
        if extension:
            wrap_extensions(source)
        self.assertEqual(reference_shape(source, text, values), expected)
        font = read_substitutions(source)
        actual, _, applications = font.apply(list(text), feature_values=values)
        self.assertEqual(actual, expected)
        return applications

    def test_alternate_selection(self):
        for extension in (False, True):
            for value, expected in [(None, "xb"), (1, "xb"), (2, "yb"), (3, "zb"),
                                    (0, "ab"), (4, "ab")]:
                with self.subTest(extension=extension, value=value):
                    self.assert_shapes(
                        "feature calt {sub a from [x y z];} calt;",
                        "ab", list(expected),
                        None if value is None else {"calt": value}, extension,
                    )

    def test_alternate_value_is_passed_through_nested_lookups(self):
        for extension in (False, True):
            self.assert_shapes(
                "lookup alt {sub a from [x y];} alt;"
                "lookup inner {sub a' lookup alt b;} inner;"
                "feature calt {sub c a' lookup inner b;} calt;",
                "cab", list("cyb"), {"calt": 2}, extension,
            )

    def test_out_of_range_alternate_is_per_glyph(self):
        self.assert_shapes(
            "feature calt {sub a from [x]; sub b from [y z];} calt;",
            "ab", list("az"), {"calt": 2},
        )

    def test_out_of_range_tries_later_subtables(self):
        source = build_font("feature calt {sub a from [x];} calt;")
        lookup = source["GSUB"].table.LookupList.Lookup[0]
        subtable = otTables.AlternateSubst()
        subtable.alternates = {"a": ["y", "z"]}
        lookup.SubTable.append(subtable)
        lookup.SubTableCount = len(lookup.SubTable)
        expected = reference_shape(source, "a", {"calt": 2})
        self.assertEqual(expected, ["z"])
        self.assertEqual(
            read_substitutions(source).apply(["a"], feature_values={"calt": 2})[0],
            expected,
        )

    def test_suppression_overrides_value_and_selection_is_not_sticky(self):
        source = build_font("feature calt {sub a from [x y];} calt;")
        font = read_substitutions(source)
        self.assertEqual(font.apply(["a"], feature_values={"calt": 2})[0], ["y"])
        self.assertEqual(font.apply(["a"])[0], ["x"])
        self.assertEqual(
            font.apply(["a"], suppressed=["calt"], feature_values={"calt": 2})[0],
            ["a"],
        )

    def test_feature_value_zero_disables_other_substitution_types(self):
        self.assert_shapes(
            "feature calt {sub a by x;} calt;", "a", ["a"], {"calt": 0},
        )

    def test_shared_lookup_uses_first_enabled_feature_value(self):
        source = build_font(
            "lookup alt {sub a from [x y];} alt;"
            "feature calt {lookup alt;} calt;"
            "feature ss01 {lookup alt;} ss01;"
        )
        font = read_substitutions(source)
        self.assertEqual(
            font.apply(["a"], feature_values={"calt": 2, "ss01": 1})[0], ["y"],
        )
        self.assertEqual(
            font.apply(["a"], feature_values={"calt": 0, "ss01": 2})[0], ["y"],
        )

    def test_invalid_feature_values_report_errors(self):
        font = read_substitutions(build_font("feature calt {sub a from [x y];} calt;"))
        for value in (-1, 1.5, "2", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "nonnegative integer"):
                    font.apply(["a"], feature_values={"calt": value})

    def test_simulator_and_render_forward_selection(self):
        source = build_font("feature calt {sub a from [x y];} calt;")
        xml = io.BytesIO()
        source.saveXML(xml)
        xml.seek(0)
        font = read_ttx(xml, positioning=False)
        self.assertEqual(font.render(["a"], feature_values={"calt": 2})[0], ["y"])
        simulator = Simulator(font)
        simulator.feature_values = {"calt": 2}
        simulator.set_string("a")
        self.assertEqual(simulator.tokens, ["y"])
        self.assertIn("a -> {x y}", simulator.steps_str())

    def test_reverse_scan_observes_already_substituted_right_context(self):
        for extension in (False, True):
            applications = self.assert_shapes(
                "feature calt {rsub a' a by x;} calt;",
                "aaaa", list("xaxa"), extension=extension,
            )
            self.assertEqual([a["posses"] for a in applications], [[2], [0]])
            self.assertEqual(applications[0]["before"], list("aaaa"))
            self.assertEqual(applications[0]["tokens"], list("aaxa"))
            self.assertEqual(applications[1]["before"], list("aaxa"))
            self.assertEqual(applications[1]["tokens"], list("xaxa"))

    def test_reverse_multiple_coverages_and_backtrack_order(self):
        for extension in (False, True):
            self.assert_shapes(
                "feature calt {rsub a b [c d]' [q m] by [x y];} calt;",
                "abcqabdmcbcdm", list("abxqabymcbcdm"), extension=extension,
            )

    def test_reverse_empty_context_and_single_glyph(self):
        for text, expected in [("", ""), ("a", "x"), ("aba", "xbx")]:
            self.assert_shapes(
                "feature calt {rsub a' by x;} calt;", text, list(expected),
            )

    def test_reverse_first_matching_subtable_wins(self):
        self.assert_shapes(
            "feature calt {rsub a' b by x; rsub a' b c by y;} calt;",
            "abc", list("xbc"),
        )

    def test_reverse_ignores_marks_in_all_contexts(self):
        self.assert_shapes(
            "@BASE=[a b c d x]; @MARK=[m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag IgnoreMarks; rsub a b c' d by x;} calt;",
            "ambmcmmmd", list("ambmxmmmd"),
        )

    def test_reverse_mark_filtering_set(self):
        self.assert_shapes(
            "@BASE=[a b x]; @MARK=[m q];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag UseMarkFilteringSet [m]; rsub a' b by x;} calt;",
            "aqbamb", list("xqbamb"),
        )

    def test_reverse_mark_attachment_class(self):
        self.assert_shapes(
            "@BASE=[a b x]; @MARK=[m q];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag MarkAttachmentType [m]; rsub a' b by x;} calt;",
            "aqbamb", list("xqbamb"),
        )

    def test_alternate_lookup_skips_ignored_marks(self):
        self.assert_shapes(
            "@BASE=[a b x]; @MARK=[m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag IgnoreMarks; sub m from [x y];} calt;",
            "amb", list("amb"), {"calt": 2},
        )

    def test_reverse_simulator_trace_and_suppression(self):
        source = wrap_extensions(build_font("feature calt {rsub a' a by x;} calt;"))
        xml = io.BytesIO()
        source.saveXML(xml)
        xml.seek(0)
        simulator = Simulator(read_ttx(xml, positioning=False))
        simulator.set_string("aaaa")
        self.assertEqual(simulator.tokens, list("xaxa"))
        self.assertIn("|a|a -> x", simulator.steps_str())
        self.assertEqual([a["posses"] for a in simulator.applications], [[2], [0]])
        simulator.feature_values = {"calt": 0}
        simulator.set_string("aaaa")
        self.assertEqual(simulator.tokens, list("aaaa"))
        simulator.feature_values = {"calt": 1}
        simulator.suppressed = ["calt"]
        simulator.set_string("aaaa")
        self.assertEqual(simulator.tokens, list("aaaa"))

    def test_reverse_top_level_skips_ignored_marks(self):
        self.assert_shapes(
            "@BASE=[a b x]; @MARK=[m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature calt {lookupflag IgnoreMarks; rsub m' by x;} calt;",
            "amb", list("amb"),
        )

    def test_malformed_substitutions_report_errors(self):
        with self.assertRaisesRegex(ValueError, "at least one alternate"):
            AlternateSubstitution("a", [])
        with self.assertRaisesRegex(ValueError, "equal nonzero lengths"):
            ReverseSubstitution([], ["a", "b"], [], ["x"])
        with self.assertRaisesRegex(NotImplementedError, "format: 2"):
            read_reverse_subst(
                etree.fromstring('<ReverseChainSingleSubst Format="2"/>'), None,
            )


if __name__ == "__main__":
    unittest.main()
