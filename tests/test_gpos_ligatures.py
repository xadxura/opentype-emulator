from copy import deepcopy
from dataclasses import FrozenInstanceError
import io
from itertools import product
import unittest

from fontTools.ttLib.tables import otTables
from lxml import etree

from test_chained_substitution import build_font
from test_alternate_reverse import wrap_extensions
import test_gpos_adjustments as adjustments
from test_gpos_adjustments import wrap_positioning
from test_gpos_foundations import load_font
from ttxfont import Font
from ttxread import read_GPOS


CLASSES = "table GDEF {GlyphClassDef [a b c d], [x y z], [m q], ;} GDEF;"
MARKS = "markClass m <anchor 10 20> @TOP;markClass q <anchor 5 6> @TOP;"
LIGATURE = (
    "pos ligature x <anchor 110 220> mark @TOP"
    " ligComponent <anchor 310 420> mark @TOP;"
)
THREE = (
    "pos ligature y <anchor 110 220> mark @TOP"
    " ligComponent <anchor 310 420> mark @TOP"
    " ligComponent <anchor 510 620> mark @TOP;"
)


class LigaturePositioningTests(unittest.TestCase):
    assert_positions = adjustments.PositionAdjustmentTests.assert_positions

    def test_components_and_extensions(self):
        for extension in (False, True):
            source = build_font(
                CLASSES + MARKS + "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
                "feature mark {" + LIGATURE + "} mark;"
            )
            if extension:
                wrap_extensions(source)
                wrap_positioning(source)
            for text in ("ambq", "abmq", "amqbm", "xm", "ambamb", "", "m"):
                with self.subTest(extension=extension, text=text):
                    self.assert_positions(source, text)
            _, applications, origins = self.assert_positions(source, "ambq")
            self.assertEqual(origins, [(0, 0), (100, 200), (305, 414)])
            attached = [a for a in applications if "positionings" in a]
            self.assertEqual([a["ligature_component"] for a in attached], [1, 2])

    def test_identical_glyph_sequences_keep_distinct_component_origins(self):
        source = build_font(
            CLASSES + MARKS + "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        inside, _, _ = self.assert_positions(source, "amb")
        outside, _, _ = self.assert_positions(source, "abm")
        self.assertNotEqual(inside[1], outside[1])

    def test_nested_ligatures_remap_internal_and_trailing_marks(self):
        for first, second, text in [
            ("sub a b by x;", "sub x c by y;", "ambmqc"),
            ("sub b c by x;", "sub a x by y;", "abmcq"),
            ("sub b c by x;", "sub a x by y;", "ambmcq"),
        ]:
            with self.subTest(first=first, text=text):
                source = build_font(
                    CLASSES + MARKS + "lookup first {lookupflag IgnoreMarks;" + first + "} first;"
                    "lookup second {lookupflag IgnoreMarks;" + second + "} second;"
                    "feature calt {lookup first;lookup second;} calt;"
                    "feature mark {" + THREE + "} mark;"
                )
                self.assert_positions(source, text)

    def test_contextual_nested_ligation_and_later_edits_preserve_provenance(self):
        for edit in ("sub m by q;", "sub m by q q;", "sub m by NULL;", "sub x by y;"):
            with self.subTest(edit=edit):
                source = build_font(
                    CLASSES + MARKS
                    + "lookup lig {lookupflag IgnoreMarks;sub a b by x;} lig;"
                    "lookup edit {" + edit + "} edit;"
                    "feature calt {sub a' lookup lig m b;lookup edit;} calt;"
                    "feature mark {" + LIGATURE + THREE + "} mark;"
                )
                self.assert_positions(source, "amb")

    def test_mark_to_mark_rejects_different_components_but_accepts_same_component(self):
        source = build_font(
            CLASSES + MARKS + "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
            "feature mkmk {pos mark m <anchor 70 80> mark @TOP;} mkmk;"
        )
        for text, count in (("ambq", 0), ("amqb", 1), ("abmq", 1), ("ambmq", 1)):
            with self.subTest(text=text):
                _, applications, _ = self.assert_positions(source, text)
                self.assertEqual(sum(a.get("feature") == "mkmk" for a in applications), count)

    def test_mark_ligature_keeps_its_original_parent_component(self):
        source = build_font(
            "table GDEF {GlyphClassDef [a b], [x], [m q z], ;} GDEF;"
            + MARKS + "markClass z <anchor 15 25> @TOP;"
            "lookup base {lookupflag IgnoreMarks;sub a b by x;} base;"
            "lookup marks {sub m q by z;} marks;"
            "feature calt {lookup base;lookup marks;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        _, _, origins = self.assert_positions(source, "amqb")
        self.assertEqual(origins[-1], (95, 195))

    def test_mark_ligation_does_not_cross_components_unless_parent_is_ignored(self):
        for flag, expected in (("", ["x", "m", "q"]), ("IgnoreLigatures", ["x", "z"])):
            with self.subTest(flag=flag):
                source = build_font(
                    "table GDEF {GlyphClassDef [a b c], [x], [m q z], ;} GDEF;"
                    + MARKS + "markClass z <anchor 15 25> @TOP;"
                    "lookup base {lookupflag IgnoreMarks;sub a b c by x;} base;"
                    "lookup marks {" + ("lookupflag " + flag + ";" if flag else "")
                    + "sub m q by z;} marks;"
                    "feature calt {lookup base;lookup marks;} calt;"
                    "feature mark {" + LIGATURE + "} mark;"
                )
                self.assert_positions(source, "ambqc")
                self.assertEqual(load_font(source).apply(list("ambqc"))[0], expected)

    def test_context_input_component_boundaries_and_ignored_parent_exception(self):
        for flag, expected in (("", ["x", "m", "q"]), ("IgnoreLigatures", ["x", "z", "q"])):
            with self.subTest(flag=flag):
                source = build_font(
                    "table GDEF {GlyphClassDef [a b], [x], [m q z], ;} GDEF;"
                    + MARKS + "markClass z <anchor 15 25> @TOP;"
                    "lookup base {lookupflag IgnoreMarks;sub a b by x;} base;"
                    "lookup edit {sub m by z;} edit;"
                    "lookup context {" + ("lookupflag " + flag + ";" if flag else "")
                    + "sub m' lookup edit q';} context;"
                    "feature calt {lookup base;lookup context;} calt;"
                    "feature mark {" + LIGATURE + "} mark;"
                )
                self.assert_positions(source, "ambq")
                self.assertEqual(load_font(source).apply(list("ambq"))[0], expected)

    def test_mark_to_mark_allows_a_mark_that_is_itself_a_new_ligature(self):
        source = build_font(
            CLASSES + MARKS + "feature calt {sub a b by m;} calt;"
            "feature mkmk {pos mark m <anchor 70 80> mark @TOP;} mkmk;"
        )
        _, _, origins = self.assert_positions(source, "abq")
        self.assertEqual(origins, [(0, 0), (65, 74)])

    def test_component_index_clamps_to_available_anchors(self):
        source = build_font(
            CLASSES + MARKS + "feature calt {lookupflag IgnoreMarks;sub a b c by x;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        self.assert_positions(source, "abmcq")

    def test_nearest_parent_is_a_barrier_regardless_of_ignore_flags(self):
        for flag in ("", "IgnoreBaseGlyphs", "IgnoreLigatures"):
            source = build_font(
                CLASSES + MARKS + "feature mark {"
                + ("lookupflag " + flag + ";" if flag else "") + LIGATURE + "} mark;"
            )
            for text in ("xam", "xym"):
                with self.subTest(flag=flag, text=text):
                    _, applications, _ = self.assert_positions(source, text)
                    self.assertEqual(applications, [])

    def test_multiple_substitution_parent_selection_for_types_four_and_five(self):
        for ligature in (False, True):
            for covered_last in (False, True):
                with self.subTest(ligature=ligature, covered_last=covered_last):
                    rules = LIGATURE if ligature else "pos base x <anchor 110 220> mark @TOP;"
                    if covered_last:
                        rules += (
                            "pos ligature c <anchor 410 520> mark @TOP;" if ligature
                            else "pos base c <anchor 410 520> mark @TOP;"
                        )
                    source = build_font(
                        CLASSES + MARKS + "feature calt {sub a by x c;} calt;"
                        "feature mark {" + rules + "} mark;"
                    )
                    self.assert_positions(source, "am")

    def test_multiple_outputs_with_intervening_mark_use_type_specific_base_search(self):
        for ligature in (False, True):
            with self.subTest(ligature=ligature):
                source = build_font(
                    CLASSES + MARKS + "feature calt {sub a by x m c;} calt;"
                    "feature mark {"
                    + (LIGATURE if ligature else "pos base x <anchor 110 220> mark @TOP;")
                    + "} mark;"
                )
                self.assert_positions(source, "aq")

    def test_multiple_components_contribute_one_component_when_religated(self):
        source = build_font(
            CLASSES + MARKS + "lookup expand {sub a by b m c;} expand;"
            "lookup lig {lookupflag IgnoreMarks;sub b c d by y;} lig;"
            "feature calt {lookup expand;lookup lig;} calt;"
            "feature mark {" + THREE + "} mark;"
        )
        self.assert_positions(source, "adq")

    def test_multiple_attached_marks_preserve_parent_components(self):
        source = build_font(
            CLASSES + MARKS + "lookup lig {lookupflag IgnoreMarks;sub a b by x;} lig;"
            "lookup expand {sub m by q q;} expand;"
            "feature calt {lookup lig;lookup expand;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        _, _, origins = self.assert_positions(source, "amb")
        self.assertEqual(origins[-2:], [(105, 214), (105, 214)])

    def test_single_alternate_and_reverse_keep_components(self):
        for edit in (
            "sub m by q;", "sub m from [q];", "rsub x m' by q;",
        ):
            with self.subTest(edit=edit):
                source = build_font(
                    CLASSES + MARKS + "lookup lig {lookupflag IgnoreMarks;sub a b by x;} lig;"
                    "lookup edit {" + edit + "} edit;"
                    "feature calt {lookup lig;lookup edit;} calt;"
                    "feature mark {" + LIGATURE + "} mark;"
                )
                self.assert_positions(source, "amb")

    def test_one_output_multiple_keeps_components(self):
        source = build_font(
            CLASSES + MARKS + "lookup lig {lookupflag IgnoreMarks;sub a b by x;} lig;"
            "lookup edit {sub m by q;} edit;"
            "feature calt {lookup lig;lookup edit;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        lookup = source["GSUB"].table.LookupList.Lookup[1]
        subtable = otTables.MultipleSubst()
        subtable.mapping = {"m": ["q"]}
        lookup.LookupType = 2
        lookup.SubTable = [subtable]
        self.assert_positions(source, "amb")

    def test_multiple_mark_classes_have_independent_component_anchors(self):
        source = build_font(
            CLASSES + "markClass m <anchor 10 20> @TOP;markClass q <anchor 5 6> @BOTTOM;"
            "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "feature mark {pos ligature x <anchor 110 220> mark @TOP"
            " ligComponent <anchor 310 420> mark @BOTTOM;} mark;"
        )
        for text in ("ambq", "aqbm", "abmq"):
            with self.subTest(text=text):
                self.assert_positions(source, text)

    def test_later_adjustments_and_reattachment_use_the_current_parent(self):
        source = build_font(
            CLASSES + MARKS + "lookup first {" + LIGATURE + "} first;"
            "lookup adjust {pos x <30 40 -20 0>;pos m <5 6 0 0>;} adjust;"
            "lookup again {" + LIGATURE + "} again;"
            "feature mark {lookup first;lookup adjust;lookup again;} mark;"
        )
        self.assert_positions(source, "xm")

    def test_short_component_sequences_match_harfbuzz(self):
        classes = "table GDEF {GlyphClassDef [a b c d], [x y], [m q z], ;} GDEF;"
        for substitutions in (
            "lookup first {lookupflag IgnoreMarks;sub a b by x;} first;"
            "lookup second {sub m q by z;} second;",
            "lookup first {sub a by b m c;} first;"
            "lookup second {lookupflag IgnoreMarks;sub b c by x;} second;",
        ):
            source = build_font(
                classes + MARKS + "markClass z <anchor 15 25> @TOP;"
                + substitutions + "feature calt {lookup first;lookup second;} calt;"
                "feature mark {" + LIGATURE + "} mark;"
                "feature mkmk {pos mark m <anchor 70 80> mark @TOP;} mkmk;"
            )
            for sequence in product("abmq", repeat=4):
                text = "".join(sequence)
                with self.subTest(substitutions=substitutions, text=text):
                    self.assert_positions(source, text)

    def test_null_component_anchor_falls_through_without_switching_components(self):
        source = build_font(CLASSES + MARKS + "feature mark {" + LIGATURE + "} mark;")
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        subtable = lookup.SubTable[0]
        lookup.SubTable.append(deepcopy(subtable))
        lookup.SubTableCount = 2
        subtable.LigatureArray.LigatureAttach[0].ComponentRecord[1].LigatureAnchor[0] = None
        _, applications, _ = self.assert_positions(source, "xm")
        self.assertEqual(len(applications), 1)
        lookup.SubTable.pop()
        lookup.SubTableCount = 1
        _, applications, _ = self.assert_positions(source, "xm")
        self.assertEqual(applications, [])

    def test_zero_component_parent_does_not_match(self):
        source = build_font(CLASSES + MARKS + "feature mark {" + LIGATURE + "} mark;")
        attach = source["GPOS"].table.LookupList.Lookup[0].SubTable[0].LigatureArray.LigatureAttach[0]
        attach.ComponentRecord = []
        attach.ComponentCount = 0
        _, applications, _ = self.assert_positions(source, "xm")
        self.assertEqual(applications, [])

    def test_mark_filtering_and_feature_controls(self):
        for flag in ("UseMarkFilteringSet [m]", "MarkAttachmentType [m]", "IgnoreMarks"):
            source = build_font(
                CLASSES + MARKS + "feature mark {lookupflag " + flag + ";" + LIGATURE + "} mark;"
            )
            _, applications, _ = self.assert_positions(source, "xmq")
            self.assertEqual(len(applications), 0 if flag == "IgnoreMarks" else 1)
        self.assert_positions(source, "xm", suppressed=["mark"])
        self.assert_positions(source, "xm", values={"mark": 0})

    def test_trace_provenance_is_immutable_and_run_local(self):
        source = build_font(
            CLASSES + MARKS + "feature calt {lookupflag IgnoreMarks;sub a b by x;} calt;"
            "feature mark {" + LIGATURE + "} mark;"
        )
        source["hmtx"].metrics["m"] = (0, 0)
        font = load_font(source)
        _, _, applications = font.apply(list("amb"))
        substitution, attachment = applications
        infos = substitution["glyph_infos"]
        self.assertEqual(len(infos), 2)
        self.assertEqual(infos[0].ligature_id, infos[1].ligature_id)
        self.assertEqual(infos[1].component, 1)
        self.assertEqual(attachment["glyph_infos"], infos)
        self.assertEqual(attachment["ligature_component"], 1)
        with self.assertRaises(FrozenInstanceError):
            infos[1].component = 2
        font.apply(list("abmq"))
        self.assertEqual(infos[1].component, 1)
        self.assertEqual(font.apply(list("amb"))[2][0]["glyph_infos"], infos)


class LigatureParserTests(unittest.TestCase):
    def document(self):
        source = build_font(CLASSES + MARKS + "feature mark {" + LIGATURE + "} mark;")
        xml = io.BytesIO()
        source.saveXML(xml, tables=["GPOS"])
        return etree.fromstring(xml.getvalue()).find("GPOS")

    def test_malformed_ligature_arrays_classes_and_indexes(self):
        for kind in ("coverage", "class", "component-index", "anchor-count", "anchor-index"):
            with self.subTest(kind=kind):
                table = self.document()
                if kind == "coverage":
                    elem = table.find(".//LigatureAttach")
                    elem.getparent().remove(elem)
                elif kind == "class":
                    table.find(".//Class").set("value", "5")
                elif kind == "component-index":
                    table.find(".//ComponentRecord").set("index", "5")
                elif kind == "anchor-index":
                    table.find(".//LigatureAnchor").set("index", "5")
                else:
                    elem = table.find(".//LigatureAnchor")
                    elem.getparent().remove(elem)
                with self.assertRaises(ValueError):
                    read_GPOS(table, Font())

    def test_unsupported_formats_and_device_tables_are_errors(self):
        for kind in ("subtable", "anchor", "device"):
            with self.subTest(kind=kind):
                table = self.document()
                if kind == "subtable":
                    table.find(".//MarkLigPos").set("Format", "2")
                else:
                    anchor = table.find(".//LigatureAnchor")
                    anchor.set("Format", "3" if kind == "device" else "4")
                    if kind == "device":
                        etree.SubElement(anchor, "YDeviceTable")
                with self.assertRaises(NotImplementedError):
                    read_GPOS(table, Font())


if __name__ == "__main__":
    unittest.main()
