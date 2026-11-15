import io
import unittest

from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables.otBase import ValueRecord
from lxml import etree
import uharfbuzz as hb

from test_chained_substitution import build_font
from test_gpos_foundations import load_font
from gpos import GlyphPosition
from ttxfont import Font, Simulator
from ttxread import read_GPOS


def wrap_positioning(source):
    for lookup in source["GPOS"].table.LookupList.Lookup:
        subtables = []
        for subtable in lookup.SubTable:
            extension = otTables.ExtensionPos()
            extension.Format = 1
            extension.ExtensionLookupType = lookup.LookupType
            extension.ExtSubTable = subtable
            subtables.append(extension)
        lookup.SubTable = subtables
        lookup.LookupType = 9
    return source


def reference_positions(source, text, features=None, *, script="latn", direction="ltr"):
    binary = io.BytesIO()
    source.save(binary)
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction = direction
    buffer.script = script
    hb.shape(hb.Font(hb.Face(binary.getvalue())), buffer, features or {"kern": True})
    order = source.getGlyphOrder()
    glyphs = [order[info.codepoint] for info in buffer.glyph_infos or []]
    positions = [
        GlyphPosition(p.x_advance, p.y_advance, p.x_offset, p.y_offset)
        for p in buffer.glyph_positions or []
    ]
    pen_x = pen_y = 0
    origins = []
    for position in positions:
        origins.append((pen_x + position.x_offset, pen_y + position.y_offset))
        pen_x += position.x_advance
        pen_y += position.y_advance
    if direction == "rtl":
        glyphs.reverse()
        positions.reverse()
        origins.reverse()
    return glyphs, positions, origins


class PositionAdjustmentTests(unittest.TestCase):
    def assert_positions(
        self, source, text, *, values=None, suppressed=(), reference_script="latn",
    ):
        if "GDEF" in source and source["GDEF"].table.GlyphClassDef is not None:
            for glyph, cls in source["GDEF"].table.GlyphClassDef.classDefs.items():
                if cls == 3:
                    source["hmtx"].metrics[glyph] = (0, 0)
        features = {} if values is None else dict(values)
        features.update({tag: False for tag in suppressed})
        expected_tokens, expected_positions, expected_origins = reference_positions(
            source, text, features, script=reference_script,
        )
        font = load_font(source)
        tokens, positions, applications, origins = font.render(
            list(text), suppressed=suppressed, feature_values=values,
        )
        self.assertEqual(tokens, expected_tokens)
        self.assertEqual(positions, expected_positions)
        self.assertEqual(origins, expected_origins)
        return positions, applications, origins

    def test_single_formats_and_extensions(self):
        for extension in (False, True):
            for feature, format in [
                ("pos [a b] <10 -20 -30 40>;", 1),
                ("pos a <10 -20 -30 40>; pos b <-5 6 70 -8>;", 2),
            ]:
                with self.subTest(format=format, extension=extension):
                    source = build_font("feature kern {" + feature + "} kern;")
                    self.assertEqual(source["GPOS"].table.LookupList.Lookup[0].SubTable[0].Format, format)
                    if extension:
                        wrap_positioning(source)
                    positions, _, _ = self.assert_positions(source, "abc")
                    self.assertEqual(positions[0], GlyphPosition(470, 0, 10, -20))

    def test_pair_formats_and_extensions(self):
        for extension in (False, True):
            for feature, format in [
                ("pos a <10 -20 -30 40> b <-5 6 -70 8>;", 1),
                ("pos [a b] <10 -20 -30 40> [c d] <-5 6 -70 8>;", 2),
            ]:
                with self.subTest(format=format, extension=extension):
                    source = build_font("feature kern {" + feature + "} kern;")
                    self.assertEqual(source["GPOS"].table.LookupList.Lookup[0].SubTable[0].Format, format)
                    if extension:
                        wrap_positioning(source)
                    text = "ab" if format == 1 else "ac"
                    positions, applications, origins = self.assert_positions(source, text)
                    self.assertEqual(positions, [
                        GlyphPosition(470, 0, 10, -20), GlyphPosition(430, 0, -5, 6),
                    ])
                    self.assertEqual(origins, [(10, -20), (465, 6)])
                    self.assertEqual(applications[0]["posses"], (0, 1))

    def test_multiple_lookups_accumulate(self):
        source = build_font(
            "lookup first {pos a <10 20 -30 40>;} first;"
            "lookup second {pos a <-5 -6 -7 -8>;} second;"
            "feature kern {lookup first; lookup second;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "ab")
        self.assertEqual(positions[0], GlyphPosition(463, 0, 5, 14))
        self.assertEqual(len(applications), 2)
        self.assertEqual(applications[0]["before_positionings"][0], GlyphPosition(500))
        self.assertEqual(applications[0]["positionings"][0], GlyphPosition(470, 0, 10, 20))
        self.assertEqual(applications[1]["before_positionings"], applications[0]["positionings"])

    def test_all_scalar_value_masks(self):
        fields = [(1, "XPlacement", 10), (2, "YPlacement", -20),
                  (4, "XAdvance", -30), (8, "YAdvance", 40)]
        for pair in (False, True):
            for mask in range(16):
                with self.subTest(pair=pair, mask=mask):
                    source = build_font(
                        "feature kern {"
                        + ("pos a <1 2 3 4> b <1 2 3 4>;" if pair else "pos a <1 2 3 4>;")
                        + "} kern;"
                    )
                    subtable = source["GPOS"].table.LookupList.Lookup[0].SubTable[0]
                    def value():
                        record = ValueRecord()
                        for bit, name, number in fields:
                            if mask & bit:
                                setattr(record, name, number)
                        return record
                    if pair:
                        subtable.ValueFormat1 = subtable.ValueFormat2 = mask
                        record = subtable.PairSet[0].PairValueRecord[0]
                        record.Value1 = value()
                        record.Value2 = value()
                    else:
                        subtable.ValueFormat = mask
                        subtable.Value = value()
                    self.assert_positions(source, "ab")

    def test_yadvance_is_retained_but_not_applied_in_horizontal_runs(self):
        source = build_font("feature kern {pos a <1 2 3 40>;} kern;")
        font = load_font(source)
        delta = font.GPOS_lookup_list[0].positionings[0].values["a"]
        self.assertEqual(delta.y_advance, 40)
        positions, _, _ = self.assert_positions(source, "ab")
        self.assertEqual(positions[0].y_advance, 0)

    def test_pair_with_no_second_value_allows_overlap(self):
        source = build_font("feature kern {pos a b -30; pos b c -40;} kern;")
        positions, applications, _ = self.assert_positions(source, "abc")
        self.assertEqual([p.x_advance for p in positions], [470, 460, 500])
        self.assertEqual(len(applications), 2)

    def test_pair_with_second_value_consumes_both_glyphs(self):
        source = build_font(
            "feature kern {pos a <0 0 -30 0> b <5 0 0 0>;"
            "pos b <0 0 -40 0> c <6 0 0 0>;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "abc")
        self.assertEqual([p.x_advance for p in positions], [470, 500, 500])
        self.assertEqual(len(applications), 1)

    def test_present_but_zero_second_value_still_consumes_both(self):
        source = build_font("feature kern {pos a b -30; pos b c -40;} kern;")
        subtable = source["GPOS"].table.LookupList.Lookup[0].SubTable[0]
        subtable.ValueFormat2 = 1
        for pair_set in subtable.PairSet:
            for pair in pair_set.PairValueRecord:
                pair.Value2 = ValueRecord()
                pair.Value2.XPlacement = 0
        positions, applications, _ = self.assert_positions(source, "abc")
        self.assertEqual([p.x_advance for p in positions], [470, 500, 500])
        self.assertEqual(len(applications), 1)

    def test_failed_pair_keeps_next_glyph_available(self):
        source = build_font("feature kern {pos a c -30; pos b c -40;} kern;")
        positions, _, _ = self.assert_positions(source, "abc")
        self.assertEqual([p.x_advance for p in positions], [500, 460, 500])

    def test_class_zero_in_second_position(self):
        source = build_font("feature kern {pos [a b] [c d] -40;} kern;")
        subtable = source["GPOS"].table.LookupList.Lookup[0].SubTable[0]
        subtable.Class1Record[0].Class2Record[0].Value1.XAdvance = -25
        positions, _, _ = self.assert_positions(source, "ax")
        self.assertEqual(positions[0].x_advance, 475)

    def test_class_zero_in_first_position_and_coverage_restriction(self):
        source = build_font("feature kern {pos [a b] [c d] -40;} kern;")
        subtable = source["GPOS"].table.LookupList.Lookup[0].SubTable[0]
        self.assertEqual(subtable.ClassDef1.classDefs.get("a", 0), 0)
        self.assert_positions(source, "ac")
        positions, applications, _ = self.assert_positions(source, "xc")
        self.assertEqual(positions, [GlyphPosition(500), GlyphPosition(500)])
        self.assertEqual(applications, [])

    def test_class_pair_zero_adjustment_stops_later_subtables(self):
        source = build_font("feature kern {pos [a b] [c d] -40;} kern;")
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        extra = build_font("feature kern {pos a x -90;} kern;")
        lookup.SubTable.extend(extra["GPOS"].table.LookupList.Lookup[0].SubTable)
        lookup.SubTableCount = len(lookup.SubTable)
        positions, applications, _ = self.assert_positions(source, "ax")
        self.assertEqual(positions[0].x_advance, 500)
        self.assertEqual(len(applications), 1)

    def test_single_subtable_precedence(self):
        source = build_font("feature kern {pos a 10;} kern;")
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        extra = build_font("feature kern {pos a 20;} kern;")
        lookup.SubTable.extend(extra["GPOS"].table.LookupList.Lookup[0].SubTable)
        lookup.SubTableCount = len(lookup.SubTable)
        positions, applications, _ = self.assert_positions(source, "a")
        self.assertEqual(positions[0].x_advance, 510)
        self.assertEqual(len(applications), 1)

    def test_explicit_pair_miss_tries_later_subtable(self):
        source = build_font("feature kern {pos a b -20;} kern;")
        lookup = source["GPOS"].table.LookupList.Lookup[0]
        extra = build_font("feature kern {pos a c -40;} kern;")
        lookup.SubTable.extend(extra["GPOS"].table.LookupList.Lookup[0].SubTable)
        lookup.SubTableCount = len(lookup.SubTable)
        positions, _, _ = self.assert_positions(source, "ac")
        self.assertEqual(positions[0].x_advance, 460)

    def test_pair_filters_and_advancement_across_ignored_marks(self):
        source = build_font(
            "@BASE=[a b c]; @MARK=[m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature kern {lookupflag IgnoreMarks; pos a b -30; pos b c -40;} kern;"
        )
        positions, applications, _ = self.assert_positions(source, "ambmc")
        self.assertEqual([p.x_advance for p in positions], [470, 0, 460, 0, 500])
        self.assertEqual([a["posses"] for a in applications], [(0, 2), (2, 4)])

    def test_mark_filtering_set_and_attachment_class(self):
        for flag in ("UseMarkFilteringSet [m]", "MarkAttachmentType [m]"):
            with self.subTest(flag=flag):
                source = build_font(
                    "@BASE=[a b]; @MARK=[m q];"
                    "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
                    "feature kern {lookupflag " + flag + "; pos a b -30;} kern;"
                )
                positions, applications, _ = self.assert_positions(source, "aqbamb")
                self.assertEqual(positions[0].x_advance, 470)
                self.assertEqual(positions[3].x_advance, 500)
                self.assertEqual(len(applications), 1)

    def test_single_filter_skips_current_marks(self):
        source = build_font(
            "@BASE=[a b]; @MARK=[m];"
            "table GDEF {GlyphClassDef @BASE, , @MARK, ;} GDEF;"
            "feature kern {lookupflag IgnoreMarks; pos m <10 20 30 40>;} kern;"
        )
        _, applications, _ = self.assert_positions(source, "amb")
        self.assertEqual(applications, [])

    def test_base_and_ligature_filters(self):
        for flag, classes in [
            ("IgnoreBaseGlyphs", "GlyphClassDef [m], , , ;"),
            ("IgnoreLigatures", "GlyphClassDef , [m], , ;"),
        ]:
            with self.subTest(flag=flag):
                source = build_font(
                    "table GDEF {" + classes + "} GDEF;"
                    "feature kern {lookupflag " + flag + "; pos a b -30;} kern;"
                )
                positions, _, _ = self.assert_positions(source, "amb")
                self.assertEqual(positions[0].x_advance, 470)

    def test_feature_suppression_and_zero_value(self):
        source = build_font("feature kern {pos a b -50;} kern;")
        self.assert_positions(source, "ab", values={"kern": 0})
        self.assert_positions(source, "ab", values={"kern": 1}, suppressed=["kern"])

    def test_shared_lookup_runs_once_if_any_feature_is_enabled(self):
        source = build_font(
            "lookup common {pos a b -50;} common;"
            "feature kern {lookup common;} kern; feature dist {lookup common;} dist;"
        )
        positions, applications, _ = self.assert_positions(
            source, "ab", values={"dist": 1}, suppressed=["kern"],
        )
        self.assertEqual(positions[0].x_advance, 450)
        self.assertEqual(len(applications), 1)
        self.assertEqual(applications[0]["feature"], "dist")

    def test_empty_and_single_glyph_pair_input(self):
        source = build_font("feature kern {pos a b -50;} kern;")
        self.assert_positions(source, "")
        self.assert_positions(source, "a")

    def test_combined_gsub_and_gpos_simulator_trace(self):
        source = build_font(
            "feature calt {sub a by x;} calt;"
            "feature kern {pos x b -50;} kern;"
        )
        simulator = Simulator(load_font(source))
        simulator.set_string("ab")
        self.assertEqual(simulator.tokens, ["x", "b"])
        self.assertEqual(simulator.places, [(0, 0), (450, 0)])
        self.assertIn("feature: calt", simulator.steps_str())
        self.assertIn("feature: kern", simulator.steps_str())
        self.assertEqual(simulator.applications[-1]["before_positionings"][0], GlyphPosition(500))

    def test_trace_snapshots_survive_later_result_changes(self):
        font = load_font(build_font("feature kern {pos a b -50;} kern;"))
        tokens = ["a", "b"]
        _, positions, applications = font.apply(tokens)
        application = applications[0]
        positions[0] = GlyphPosition(1)
        tokens[0] = "x"
        self.assertEqual(application["tokens"], ("a", "b"))
        self.assertEqual(application["before_positionings"][0], GlyphPosition(500))
        self.assertEqual(application["positionings"][0], GlyphPosition(450))

    def test_cursive_lookup_executes_and_can_be_suppressed(self):
        source = build_font(
            "feature curs {pos cursive a <anchor NULL> <anchor 100 200>;"
            "pos cursive b <anchor 10 20> <anchor NULL>;} curs;"
        )
        font = load_font(source)
        self.assertEqual(font.render(["a", "b"])[1], [
            GlyphPosition(100), GlyphPosition(490, 0, -10, 180),
        ])
        self.assertEqual(font.render(["a", "b"], suppressed=["curs"])[3], [(0, 0), (500, 0)])


class PositionParserErrorTests(unittest.TestCase):
    def parse_subtable(self, body, lookup_type=2):
        font = Font()
        read_GPOS(etree.fromstring(
            '<GPOS><LookupList><Lookup index="0">'
            f'<LookupType value="{lookup_type}"/><LookupFlag value="0"/>'
            + body + '</Lookup></LookupList></GPOS>'
        ), font)
        return font

    def parse_single(self, body):
        source = build_font("feature kern {pos a 10;} kern;")
        xml = io.BytesIO()
        source.saveXML(xml, tables=["GPOS"])
        root = etree.fromstring(xml.getvalue())
        subtable = root.find(".//SinglePos")
        subtable.getparent().replace(subtable, etree.fromstring(body))
        font = Font()
        read_GPOS(root.find("GPOS"), font)

    def test_unsupported_value_formats_and_device_tables_are_explicit_errors(self):
        for value in [
            '<ValueFormat value="16"/><Value><XPlaDevice/></Value>',
            '<ValueFormat value="256"/><Value/>',
            '<ValueFormat value="1"/><Value XPlacement="10"><XPlaDevice/></Value>',
        ]:
            with self.subTest(value=value):
                with self.assertRaises(NotImplementedError):
                    self.parse_single(
                        '<SinglePos index="0" Format="1">'
                        '<Coverage><Glyph value="a"/></Coverage>' + value + '</SinglePos>'
                    )

    def test_unsupported_single_format_is_an_explicit_error(self):
        with self.assertRaisesRegex(NotImplementedError, "SinglePos.*3"):
            self.parse_single('<SinglePos index="0" Format="3"/>')

    def test_single_value_count_mismatch_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "coverage"):
            self.parse_single(
                '<SinglePos index="0" Format="2"><Coverage>'
                '<Glyph value="a"/><Glyph value="b"/></Coverage>'
                '<ValueFormat value="4"/><Value index="0" XAdvance="10"/></SinglePos>'
            )

    def test_pair_value_formats_reject_device_and_variation_data(self):
        for format1, format2 in [(16, 0), (0, 128), (256, 0)]:
            with self.subTest(format1=format1, format2=format2):
                with self.assertRaises(NotImplementedError):
                    self.parse_subtable(
                        '<PairPos index="0" Format="1">'
                        '<Coverage><Glyph value="a"/></Coverage>'
                        f'<ValueFormat1 value="{format1}"/><ValueFormat2 value="{format2}"/>'
                        '<PairSet index="0"/></PairPos>'
                    )

    def test_pair_count_and_matrix_errors(self):
        for body in [
            '<PairPos index="0" Format="1"><Coverage><Glyph value="a"/></Coverage>'
            '<ValueFormat1 value="0"/><ValueFormat2 value="0"/></PairPos>',
            '<PairPos index="0" Format="2"><Coverage><Glyph value="a"/></Coverage>'
            '<ValueFormat1 value="0"/><ValueFormat2 value="0"/>'
            '<Class1Record index="0"/></PairPos>',
            '<PairPos index="0" Format="2"><Coverage><Glyph value="a"/></Coverage>'
            '<ValueFormat1 value="0"/><ValueFormat2 value="0"/>'
            '<ClassDef1><ClassDef glyph="a" class="2"/></ClassDef1>'
            '<Class1Record index="0"><Class2Record index="0"/></Class1Record></PairPos>',
        ]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.parse_subtable(body)

    def test_unsupported_pair_and_extension_formats(self):
        with self.assertRaisesRegex(NotImplementedError, "PairPos.*3"):
            self.parse_subtable('<PairPos index="0" Format="3"/>')
        with self.assertRaisesRegex(NotImplementedError, "ExtensionPos.*2"):
            self.parse_subtable(
                '<ExtensionPos index="0" Format="2"><ExtensionLookupType value="1"/>'
                '<SinglePos index="0" Format="1"/></ExtensionPos>', lookup_type=9,
            )

    def test_missing_value_fields_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Missing GPOS ValueRecord field"):
            self.parse_single(
                '<SinglePos index="0" Format="1"><Coverage><Glyph value="a"/></Coverage>'
                '<ValueFormat value="4"/><Value/></SinglePos>'
            )

    def test_inconsistent_extension_and_subtable_types(self):
        cases = [
            ('<SinglePos index="0" Format="1"/>', 2),
            ('<ExtensionPos index="0" Format="1"><ExtensionLookupType value="1"/>'
             '<SinglePos index="0" Format="1"/></ExtensionPos>', 1),
            ('<SinglePos index="0" Format="1"/>', 9),
            ('<ExtensionPos index="0" Format="1"><ExtensionLookupType value="1"/>'
             '<SinglePos index="0" Format="1"><Coverage><Glyph value="a"/></Coverage>'
             '<ValueFormat value="0"/><Value/></SinglePos></ExtensionPos>'
             '<ExtensionPos index="1" Format="1"><ExtensionLookupType value="2"/>'
             '<PairPos index="0" Format="1"/></ExtensionPos>', 9),
        ]
        for body, lookup_type in cases:
            with self.subTest(body=body, lookup_type=lookup_type):
                with self.assertRaises(ValueError):
                    self.parse_subtable(body, lookup_type)


if __name__ == "__main__":
    unittest.main()
