from copy import deepcopy
from dataclasses import FrozenInstanceError
import io
import unittest

from lxml import etree

from test_chained_substitution import build_font
import test_gpos_adjustments as adjustments
from test_gpos_adjustments import wrap_positioning
from test_gpos_foundations import load_font
from gpos import GlyphAttachment, GlyphPosition, resolve_origins
from ttxfont import Font
from ttxread import read_GPOS


MARKS = (
    "markClass m <anchor 10 20> @TOP;"
    "markClass q <anchor 5 6> @TOP;"
)
BASE = "pos base a <anchor 110 220> mark @TOP;"
STACK = (
    "pos mark m <anchor 70 80> mark @TOP;"
    "pos mark q <anchor 25 30> mark @TOP;"
)


class MarkPositioningTests(unittest.TestCase):
    assert_positions = adjustments.PositionAdjustmentTests.assert_positions

    def test_base_marks_and_stacks_with_extensions(self):
        for extension in (False, True):
            with self.subTest(extension=extension):
                source = build_font(
                    MARKS + "feature mark {" + BASE + "} mark;"
                    "feature mkmk {" + STACK + "} mkmk;"
                )
                if extension:
                    wrap_positioning(source)
                positions, applications, origins = self.assert_positions(source, "amqq")
                self.assertEqual(origins, [(0, 0), (100, 200), (165, 274), (185, 298)])
                self.assertEqual(positions[-1], GlyphPosition(0, 0, -315, 298))
                self.assertEqual(applications[-1]["attachments"], (
                    (1, GlyphAttachment(0, 100, 200, 0)),
                    (2, GlyphAttachment(1, 65, 74, 200)),
                    (3, GlyphAttachment(2, 20, 24, 274)),
                ))

    def test_multiple_marks_attach_independently_to_base(self):
        source = build_font(MARKS + "feature mark {" + BASE + "} mark;")
        _, applications, origins = self.assert_positions(source, "amqm")
        self.assertEqual(origins, [(0, 0), (100, 200), (105, 214), (100, 200)])
        self.assertEqual(len(applications), 3)

    def test_base_search_stops_at_nearest_nonmark_even_when_ignored(self):
        for flag in ("", "IgnoreBaseGlyphs", "IgnoreLigatures"):
            source = build_font(
                "table GDEF {GlyphClassDef [a b], [y], [m q], ;} GDEF;"
                + MARKS + "feature mark {"
                + ("lookupflag " + flag + ";" if flag else "") + BASE + "} mark;"
            )
            for text in ("abm", "aym", "axm", "bqm"):
                with self.subTest(flag=flag, text=text):
                    _, applications, _ = self.assert_positions(source, text)
                    self.assertEqual(applications, [])

    def test_unclassified_parent_in_base_coverage_can_attach(self):
        source = build_font(MARKS + "feature mark {" + BASE + "} mark;")
        source["GDEF"].table.GlyphClassDef.classDefs.pop("a", None)
        _, applications, origins = self.assert_positions(source, "am")
        self.assertEqual(len(applications), 1)
        self.assertEqual(origins[-1], (100, 200))

    def test_mark_search_does_not_skip_uncovered_marks_or_nonmarks(self):
        for flag in ("", "IgnoreBaseGlyphs", "IgnoreLigatures"):
            source = build_font(
                "table GDEF {GlyphClassDef [a b x], [y], [m q z], ;} GDEF;"
                + MARKS + "feature mkmk {"
                + ("lookupflag " + flag + ";" if flag else "")
                + "pos mark m <anchor 70 80> mark @TOP;} mkmk;"
            )
            for text in ("mazq", "mxq", "myq", "mzq", "mqq"):
                with self.subTest(flag=flag, text=text):
                    _, applications, _ = self.assert_positions(source, text)
                    self.assertEqual(len(applications), 1 if text == "mqq" else 0)

    def test_mark_filtering_and_attachment_classes(self):
        for flag in ("UseMarkFilteringSet [m q]", "MarkAttachmentType [m q]"):
            for to_mark in (False, True):
                with self.subTest(flag=flag, to_mark=to_mark):
                    source = build_font(
                        "table GDEF {GlyphClassDef [a], , [m q z], ;} GDEF;"
                        + MARKS + "feature mark {lookupflag " + flag + ";"
                        + ("pos mark m <anchor 70 80> mark @TOP;" if to_mark else BASE)
                        + "} mark;"
                    )
                    _, applications, origins = self.assert_positions(
                        source, "mzq" if to_mark else "azq",
                    )
                    self.assertEqual(len(applications), 1)
                    self.assertEqual(origins[-1], (65, 74) if to_mark else (105, 214))

    def test_ignore_marks_overrides_filtering_set(self):
        source = build_font(
            MARKS + "feature mark {lookupflag IgnoreMarks UseMarkFilteringSet [m q];"
            + BASE + "} mark;"
        )
        _, applications, _ = self.assert_positions(source, "amq")
        self.assertEqual(applications, [])

    def test_filtering_set_overrides_attachment_class(self):
        source = build_font(
            MARKS + "feature mark {lookupflag MarkAttachmentType [m] "
            "UseMarkFilteringSet [q];" + BASE + "} mark;"
        )
        _, applications, origins = self.assert_positions(source, "amq")
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["posses"], (2,))
        self.assertEqual(origins[-1], (105, 214))

    def test_distinct_classes_and_null_parent_anchors(self):
        source = build_font(
            "markClass m <anchor 10 20> @TOP;"
            "markClass q <anchor 5 6> @BOTTOM;"
            "feature mark {pos base a <anchor 110 220> mark @TOP;"
            "pos base b <anchor 30 -40> mark @BOTTOM;} mark;"
        )
        for text, count in (("am", 1), ("aq", 0), ("bm", 0), ("bq", 1)):
            with self.subTest(text=text):
                _, applications, _ = self.assert_positions(source, text)
                self.assertEqual(len(applications), count)

    def test_null_anchor_falls_through_to_later_subtable(self):
        for to_mark in (False, True):
            source = build_font(
                MARKS + "feature mark {" + (STACK if to_mark else BASE) + "} mark;"
            )
            lookup = source["GPOS"].table.LookupList.Lookup[0]
            first = lookup.SubTable[0]
            lookup.SubTable.append(deepcopy(first))
            lookup.SubTableCount = 2
            if to_mark:
                first.Mark2Array.Mark2Record[0].Mark2Anchor[0] = None
            else:
                first.BaseArray.BaseRecord[0].BaseAnchor[0] = None
            _, applications, _ = self.assert_positions(source, "mq" if to_mark else "am")
            self.assertEqual(len(applications), 1)

    def test_first_matching_subtable_wins_even_for_zero_displacement(self):
        source = build_font(MARKS + "feature mark {" + BASE + "} mark;")
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        lookup.SubTable.append(deepcopy(lookup.SubTable[0]))
        lookup.SubTableCount = 2
        anchor = lookup.SubTable[0].BaseArray.BaseRecord[0].BaseAnchor[0]
        anchor.XCoordinate, anchor.YCoordinate = 10, 20
        _, applications, origins = self.assert_positions(source, "am")
        self.assertEqual(len(applications), 1)
        self.assertEqual(origins[-1], (0, 0))

    def test_placements_before_attachment_are_replaced_and_later_ones_accumulate(self):
        for before in (False, True):
            for target in ("a", "m"):
                with self.subTest(before=before, target=target):
                    adjustment = "lookup adjust {pos " + target + " <40 50 -30 0>;} adjust;"
                    attach = "lookup attach {" + BASE + "} attach;"
                    source = build_font(
                        MARKS + (adjustment + attach if before else attach + adjustment)
                        + "feature mark {lookup "
                        + ("adjust; lookup attach;" if before else "attach; lookup adjust;")
                        + "} mark;"
                    )
                    self.assert_positions(source, "amm", reference_script="deva")

    def test_later_parent_movements_defer_x_but_snapshot_y(self):
        for target in ("a", "m", "q"):
            with self.subTest(target=target):
                source = build_font(
                    MARKS + "lookup base {" + BASE + "} base;"
                    "lookup stack {" + STACK + "} stack;"
                    "lookup adjust {pos " + target + " <30 -40 50 0>;} adjust;"
                    "feature mark {lookup base; lookup stack; lookup adjust;} mark;"
                )
                _, _, origins = self.assert_positions(source, "amqqb", reference_script="deva")
                self.assertEqual(origins[3][1], 258 if target == "q" else 298)

    def test_reattachment_replaces_earlier_links_and_local_placements(self):
        source = build_font(
            MARKS + "lookup base {" + BASE + "} base;"
            "lookup stack {" + STACK + "} stack;"
            "lookup move {pos m <40 50 0 0>;} move;"
            "lookup again {pos base a <anchor 210 320> mark @TOP;} again;"
            "feature mark {lookup base; lookup stack; lookup move; lookup again;} mark;"
        )
        _, applications, origins = self.assert_positions(source, "amq")
        self.assertEqual(origins, [(0, 0), (200, 300), (205, 314)])
        self.assertEqual(applications[-1]["attachments"][-1], (2, GlyphAttachment(0, 205, 314, 0)))

    def test_mark_advance_adjustments_do_not_break_attachment_arithmetic(self):
        source = build_font(
            MARKS + "lookup advances {pos m <0 0 30 0>;pos q <0 0 70 0>;} advances;"
            "lookup base {" + BASE + "} base;lookup stack {" + STACK + "} stack;"
            "feature mark {lookup advances;lookup base;lookup stack;} mark;"
        )
        # Latin shaping zeros mark advances after GPOS. Devanagari's early
        # zeroing leaves these explicit GPOS adjustments intact on ASCII input.
        positions, _, origins = self.assert_positions(source, "amqqb", reference_script="deva")
        self.assertEqual([p.x_advance for p in positions], [500, 30, 70, 70, 500])
        self.assertEqual(origins[-1], (670, 0))

    def test_gsub_results_use_final_glyph_metrics_and_anchors(self):
        source = build_font(
            MARKS + "feature calt {sub b by a;sub c by a a;} calt;"
            "feature mark {" + BASE + "} mark;"
        )
        self.assert_positions(source, "bmcm")

    def test_anchor_formats_use_unhinted_coordinates(self):
        for format in (1, 2, 3):
            for to_mark in (False, True):
                with self.subTest(format=format, to_mark=to_mark):
                    source = build_font(
                        MARKS + "feature mark {" + (STACK if to_mark else BASE) + "} mark;"
                    )
                    subtable = source["GPOS"].table.LookupList.Lookup[0].SubTable[0]
                    mark_array = subtable.Mark1Array if to_mark else subtable.MarkArray
                    parent_array = subtable.Mark2Array if to_mark else subtable.BaseArray
                    records = parent_array.Mark2Record if to_mark else parent_array.BaseRecord
                    anchors = [r.MarkAnchor for r in mark_array.MarkRecord]
                    anchors += [a for r in records for a in (
                        r.Mark2Anchor if to_mark else r.BaseAnchor
                    ) if a is not None]
                    for anchor in anchors:
                        anchor.Format = format
                        if format == 2:
                            anchor.AnchorPoint = 0
                        if format == 3:
                            anchor.XDeviceTable = anchor.YDeviceTable = None
                    self.assert_positions(source, "mq" if to_mark else "am")

    def test_feature_controls_and_empty_or_parentless_input(self):
        source = build_font(MARKS + "feature mark {" + BASE + "} mark;")
        for text in ("", "m", "qma"):
            self.assert_positions(source, text)
        self.assert_positions(source, "am", suppressed=["mark"])
        self.assert_positions(source, "am", values={"mark": 0})

    def test_trace_snapshots_are_resolved_immutable_and_run_local(self):
        source = build_font(
            MARKS + "feature mark {" + BASE + "} mark;feature mkmk {" + STACK + "} mkmk;"
        )
        source["hmtx"].metrics["m"] = source["hmtx"].metrics["q"] = (0, 0)
        font = load_font(source)
        tokens, positions, applications, origins = font.render(list("amqq"))
        first = applications[0]
        self.assertEqual(first["before_attachments"], ())
        self.assertEqual(first["attachments"], ((1, GlyphAttachment(0, 100, 200, 0)),))
        self.assertEqual(resolve_origins(first["positionings"])[1], (100, 200))
        for previous, current in zip(applications, applications[1:]):
            self.assertEqual(previous["positionings"], current["before_positionings"])
            self.assertEqual(previous["attachments"], current["before_attachments"])
        positions[1] = GlyphPosition(123)
        tokens[1] = "x"
        self.assertEqual(first["positionings"][1], GlyphPosition(0, 0, -400, 200))
        with self.assertRaises(FrozenInstanceError):
            first["attachments"][0][1].parent = 3
        self.assertEqual(font.render(list("amqq"))[3], origins)
        self.assertEqual(font.render(["q"])[2], [])

    def test_direct_lookup_entry_returns_resolved_positions(self):
        source = build_font(MARKS + "feature mark {" + BASE + "} mark;")
        source["hmtx"].metrics["m"] = (0, 0)
        font = load_font(source)
        lookup = font.GPOS_lookup_list[0]
        initial = font.initial_positions(["a", "m"])
        positions, application, jump = lookup.apply_at(["a", "m"], initial, 1, font)
        self.assertEqual(positions, [GlyphPosition(500), GlyphPosition(0, 0, -400, 200)])
        self.assertEqual(jump, 1)
        self.assertEqual(application["positionings"], tuple(positions))
        self.assertEqual(lookup.apply(["a", "m"], initial, font)[0], positions)


class MarkParserTests(unittest.TestCase):
    def document(self, *, to_mark=False):
        source = build_font(
            MARKS + "feature mark {" + (STACK if to_mark else BASE) + "} mark;"
        )
        xml = io.BytesIO()
        source.saveXML(xml, tables=["GPOS"])
        return etree.fromstring(xml.getvalue()).find("GPOS")

    def test_unknown_formats_and_device_or_variation_anchors_are_rejected(self):
        for to_mark in (False, True):
            for kind in ("format", "device", "variation"):
                with self.subTest(to_mark=to_mark, kind=kind):
                    table = self.document(to_mark=to_mark)
                    anchor = table.find(".//MarkAnchor")
                    anchor.set("Format", "4" if kind == "format" else "3")
                    if kind != "format":
                        device = etree.SubElement(anchor, "XDeviceTable")
                        etree.SubElement(device, "DeltaFormat", value="32768" if kind == "variation" else "1")
                    with self.assertRaises(NotImplementedError):
                        read_GPOS(table, Font())

    def test_missing_coordinates_anchor_point_or_mark_anchor_are_errors(self):
        for field in ("XCoordinate", "YCoordinate", "MarkAnchor", "AnchorPoint"):
            with self.subTest(field=field):
                table = self.document()
                anchor = table.find(".//MarkAnchor")
                if field == "AnchorPoint":
                    anchor.set("Format", "2")
                else:
                    elem = anchor if field == "MarkAnchor" else anchor.find(field)
                    elem.getparent().remove(elem)
                with self.assertRaises(ValueError):
                    read_GPOS(table, Font())

    def test_invalid_classes_counts_and_indexes_are_errors(self):
        for to_mark in (False, True):
            for kind in ("class", "count", "parent-count", "index", "anchor-count"):
                with self.subTest(to_mark=to_mark, kind=kind):
                    table = self.document(to_mark=to_mark)
                    subtable = table.find(".//MarkMarkPos" if to_mark else ".//MarkBasePos")
                    if kind == "class":
                        subtable.find(".//Class").set("value", "3")
                    elif kind == "count":
                        elem = subtable.find(".//MarkRecord")
                        elem.getparent().remove(elem)
                    elif kind == "parent-count":
                        elem = subtable.find(".//Mark2Record" if to_mark else ".//BaseRecord")
                        elem.getparent().remove(elem)
                    elif kind == "index":
                        subtable.find(".//MarkRecord").set("index", "9")
                    else:
                        elem = subtable.find(".//Mark2Anchor" if to_mark else ".//BaseAnchor")
                        elem.getparent().remove(elem)
                    with self.assertRaises(ValueError):
                        read_GPOS(table, Font())

    def test_unknown_mark_subtable_formats_are_rejected(self):
        for to_mark in (False, True):
            table = self.document(to_mark=to_mark)
            table.find(".//MarkMarkPos" if to_mark else ".//MarkBasePos").set("Format", "2")
            with self.assertRaisesRegex(NotImplementedError, "format"):
                read_GPOS(table, Font())


if __name__ == "__main__":
    unittest.main()
