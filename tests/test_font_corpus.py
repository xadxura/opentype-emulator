from copy import deepcopy
import io
import os
from pathlib import Path
import unittest

from fontTools.ttLib import TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.otlLib.builder import buildLigatureSubstSubtable, buildLookup
from fontTools.pens.ttGlyphPen import TTGlyphPen
import uharfbuzz as hb
import yaml

from test_chained_substitution import build_font, read_substitutions, read_ttx, Simulator
from test_gpos_adjustments import reference_positions, wrap_positioning
from test_gpos_foundations import load_font


CORPUS = os.environ.get("OPENTYPE_TEST_FONTS")


@unittest.skipUnless(CORPUS, "Set OPENTYPE_TEST_FONTS to the ms-volt-src tests directory")
class FontCorpusTests(unittest.TestCase):
    def isolate_lookup(self, original, lookup, glyphs):
        builder = FontBuilder(original["head"].unitsPerEm, isTTF=True)
        order = original.getGlyphOrder()
        builder.setupGlyphOrder(order)
        builder.setupCharacterMap({0xE000 + i: glyph for i, glyph in enumerate(glyphs)})
        builder.setupGlyf({glyph: TTGlyphPen(None).glyph() for glyph in order})
        metrics = original["hmtx"].metrics.copy()
        if "GDEF" in original:
            builder.font["GDEF"] = deepcopy(original["GDEF"])
            classes = original["GDEF"].table.GlyphClassDef
            for glyph, cls in (classes.classDefs if classes is not None else {}).items():
                if cls == 3:
                    metrics[glyph] = (0, metrics[glyph][1])
        builder.setupHorizontalMetrics(metrics)
        builder.setupHorizontalHeader(ascent=800, descent=-200)
        builder.setupNameTable({"familyName": "Isolated real GPOS", "styleName": "Regular"})
        builder.setupOS2()
        builder.setupPost()
        builder.setupMaxp()
        template = build_font("feature kern {pos a 1;} kern;")
        builder.font["GPOS"] = deepcopy(template["GPOS"])
        builder.font["GPOS"].table.LookupList.Lookup = [deepcopy(lookup)]
        return builder.font

    def test_isolated_real_single_and_pair_lookups(self):
        # Private lookups are invoked through contextual GPOS in the
        # original font. Isolate them in a scalar-only test font, in memory.
        with TTFont(Path(CORPUS) / "seg_src.ttf") as original:
            table = original["GPOS"].table
            for tag in ("zz03", "zz19"):
                with self.subTest(feature=tag):
                    feature = next(f for f in table.FeatureList.FeatureRecord if f.FeatureTag == tag)
                    lookup = table.LookupList.Lookup[feature.Feature.LookupListIndex[0]]
                    subtable = lookup.SubTable[0]
                    first = subtable.Coverage.glyphs[0]
                    glyphs = [first]
                    if lookup.LookupType == 2:
                        glyphs.append(subtable.PairSet[0].PairValueRecord[0].SecondGlyph)
                    isolated = self.isolate_lookup(original, lookup, glyphs)
                    text = "".join(chr(0xE000 + i) for i in range(len(glyphs)))
                    expected_tokens, expected_positions, expected_origins = reference_positions(
                        isolated, text,
                    )
                    font = load_font(isolated)
                    tokens, positions, applications, origins = font.render(glyphs)
                    self.assertEqual(tokens, expected_tokens)
                    self.assertEqual(positions, expected_positions)
                    self.assertEqual(origins, expected_origins)
                    self.assertEqual(len(applications), 1)
                    self.assertNotEqual(positions, font.initial_positions(glyphs))

    def test_isolated_real_mark_lookups_and_classes(self):
        total = 0
        with TTFont(Path(CORPUS) / "seg_src.ttf") as original:
            gdef = original["GDEF"].table
            classes = gdef.GlyphClassDef.classDefs
            for index, lookup in enumerate(original["GPOS"].table.LookupList.Lookup):
                if lookup.LookupType not in (4, 6):
                    continue
                to_mark = lookup.LookupType == 6
                subtable = lookup.SubTable[0]
                marks = subtable.Mark1Coverage.glyphs if to_mark else subtable.MarkCoverage.glyphs
                mark_array = subtable.Mark1Array if to_mark else subtable.MarkArray
                parents = subtable.Mark2Coverage.glyphs if to_mark else subtable.BaseCoverage.glyphs
                parent_records = (
                    subtable.Mark2Array.Mark2Record if to_mark else subtable.BaseArray.BaseRecord
                )
                allowed = (
                    set(gdef.MarkGlyphSetsDef.Coverage[lookup.MarkFilteringSet].glyphs)
                    if lookup.LookupFlag & 16 else None
                )
                cases = {}
                for mark, record in zip(marks, mark_array.MarkRecord):
                    if allowed is not None and mark not in allowed:
                        continue
                    for parent, parent_record in zip(parents, parent_records):
                        anchors = parent_record.Mark2Anchor if to_mark else parent_record.BaseAnchor
                        if anchors[record.Class] is None or (classes.get(parent, 0) == 3) != to_mark:
                            continue
                        if to_mark and allowed is not None and parent not in allowed:
                            continue
                        cases.setdefault(record.Class, (parent, mark))
                        break
                self.assertTrue(cases, f"No attachable glyphs in real lookup {index}")
                glyphs = list(dict.fromkeys(glyph for pair in cases.values() for glyph in pair))
                isolated = self.isolate_lookup(original, lookup, glyphs)
                if index % 2:
                    wrap_positioning(isolated)
                font = load_font(isolated)
                characters = {glyph: chr(0xE000 + i) for i, glyph in enumerate(glyphs)}
                for mark_class, pair in cases.items():
                    with self.subTest(lookup=index, mark_class=mark_class):
                        expected = reference_positions(
                            isolated, "".join(characters[glyph] for glyph in pair),
                        )
                        tokens, positions, applications, origins = font.render(list(pair))
                        self.assertEqual((tokens, positions, origins), expected)
                        self.assertEqual(len(applications), 1)
                        total += 1
        self.assertGreaterEqual(total, 29)
        print(f"\nCompared {total} real-font mark/class cases across 29 lookups with HarfBuzz.")

    def test_isolated_real_contexts_with_referenced_lookups(self):
        total = applied = nested = 0
        for filename in ("seg_src.ttf", "eot_volt.ttf"):
            with TTFont(Path(CORPUS) / filename) as original:
                lookups = original["GPOS"].table.LookupList
                gdef = original["GDEF"].table
                classes = gdef.GlyphClassDef.classDefs
                attach_classes = (
                    gdef.MarkAttachClassDef.classDefs if gdef.MarkAttachClassDef else {}
                )
                for index, lookup in enumerate(lookups.Lookup):
                    subtables = [
                        s.ExtSubTable if lookup.LookupType == 9 else s for s in lookup.SubTable
                    ]
                    typ = (
                        lookup.SubTable[0].ExtensionLookupType
                        if lookup.LookupType == 9 else lookup.LookupType
                    )
                    if typ != 8:
                        continue

                    def eligible(glyph):
                        cls = classes.get(glyph, 0)
                        if cls in (1, 2, 3) and lookup.LookupFlag & (1 << cls):
                            return False
                        if cls == 3:
                            if lookup.LookupFlag & 16:
                                return glyph in gdef.MarkGlyphSetsDef.Coverage[
                                    lookup.MarkFilteringSet
                                ].glyphs
                            if lookup.LookupFlag >> 8:
                                return attach_classes.get(glyph, 0) == lookup.LookupFlag >> 8
                        return True

                    cases = []
                    for subtable in subtables[:2]:
                        self.assertEqual(subtable.Format, 3)
                        coverages = (
                            list(reversed(subtable.BacktrackCoverage))
                            + subtable.InputCoverage + subtable.LookAheadCoverage
                        )
                        choices = [[g for g in c.glyphs if eligible(g)] for c in coverages]
                        if all(choices):
                            cases.extend(tuple(c[pick] for c in choices) for pick in (0, -1))
                    cases = list(dict.fromkeys(cases))
                    if not cases:
                        continue
                    glyphs = list(dict.fromkeys(glyph for case in cases for glyph in case))
                    isolated = self.isolate_lookup(original, lookup, glyphs)
                    # Preserve original indexes and the entire referenced lookup closure,
                    # but enable only the outer context as a top-level feature lookup.
                    isolated["GPOS"].table.LookupList = deepcopy(lookups)
                    isolated["GPOS"].table.FeatureList.FeatureRecord[0].Feature.LookupListIndex = [index]
                    font = load_font(isolated)
                    characters = {glyph: chr(0xE000 + i) for i, glyph in enumerate(glyphs)}
                    for case in cases:
                        with self.subTest(font=filename, lookup=index, glyphs=case):
                            expected = reference_positions(
                                isolated, "".join(characters[glyph] for glyph in case),
                                script="deva",
                            )
                            tokens, positions, applications, origins = font.render(list(case))
                            self.assertEqual((tokens, positions, origins), expected)
                            total += 1
                            applied += bool(applications)
                            nested += any(a["children"] for a in applications)
        self.assertGreaterEqual(total, 40)
        self.assertGreaterEqual(applied, 30)
        self.assertGreaterEqual(nested, 20)
        print(f"\nCompared {total} real-font contextual cases with HarfBuzz; "
              f"{applied} matched contexts, {nested} executed nested actions.")

    def test_isolated_real_ligature_anchors_with_component_provenance(self):
        total = 0
        with TTFont(Path(CORPUS) / "seg_src.ttf") as original:
            lookup = next(l for l in original["GPOS"].table.LookupList.Lookup if l.LookupType == 5)
            subtable = lookup.SubTable[0]
            parts = [
                glyph for glyph, cls in original["GDEF"].table.GlyphClassDef.classDefs.items()
                if cls == 1 and original["hmtx"].metrics[glyph][0] > 0
                and glyph not in subtable.LigatureCoverage.glyphs
            ][:2]
            self.assertEqual(len(parts), 2)
            marks = {}
            for glyph, record in zip(subtable.MarkCoverage.glyphs, subtable.MarkArray.MarkRecord):
                marks.setdefault(record.Class, glyph)
            template = build_font("feature calt {sub a b by x;} calt;")
            for parent, attach in zip(
                subtable.LigatureCoverage.glyphs, subtable.LigatureArray.LigatureAttach,
            ):
                self.assertEqual(len(attach.ComponentRecord), 2)
                glyphs = list(dict.fromkeys(parts + [parent] + list(marks.values())))
                for extension in (False, True):
                    isolated = self.isolate_lookup(original, lookup, glyphs)
                    isolated["GSUB"] = deepcopy(template["GSUB"])
                    isolated["GSUB"].table.LookupList.Lookup = [buildLookup([
                        buildLigatureSubstSubtable({tuple(parts): parent}),
                    ], flags=8)]
                    if extension:
                        wrap_positioning(isolated)
                    font = load_font(isolated)
                    characters = {glyph: chr(0xE000 + i) for i, glyph in enumerate(glyphs)}
                    for component, record in enumerate(attach.ComponentRecord):
                        for mark_class, anchor in enumerate(record.LigatureAnchor):
                            if anchor is None:
                                continue
                            mark = marks[mark_class]
                            tokens = parts[:component + 1] + [mark] + parts[component + 1:]
                            text = "".join(characters[glyph] for glyph in tokens)
                            with self.subTest(parent=parent, component=component, mark_class=mark_class, extension=extension):
                                expected = reference_positions(isolated, text, {"calt": True, "kern": True})
                                actual, positions, applications, origins = font.render(tokens)
                                self.assertEqual((actual, positions, origins), expected)
                                positioning = applications[-1]
                                self.assertEqual(positioning["ligature_component"], component + 1)
                                self.assertEqual(positioning["glyph_infos"][0].ligature_components, 2)
                                total += 1
        self.assertGreater(total, 0)
        print(f"\nCompared {total} real-font ligature/component/class cases with HarfBuzz.")

    def test_egyptian_sequences(self):
        directory = Path(CORPUS)
        total = 0
        for font_name, yaml_name in [
            ("eot_volt.ttf", "eot_tests.yaml"),
            ("eot_new_otl.ttf", "eot_tests.yaml"),
            ("mot_src.ttf", "mot_tests.yaml"),
        ]:
            with TTFont(directory / font_name) as source:
                font = read_substitutions(source)
                cmap = source.getBestCmap()
                reverse_cmap = {glyph: cp for cp, glyph in cmap.items()}
                order = source.getGlyphOrder()
                reference = hb.Font(hb.Face((directory / font_name).read_bytes()))
                features = {feature.tag: True for feature in font.GSUB_features}
                with (directory / yaml_name).open(encoding="utf-8") as handle:
                    cases = yaml.safe_load(handle)
                self.assertIsInstance(cases, list)
                self.assertGreater(len(cases), 0)
                for index, case in enumerate(cases):
                    label = case.get("label", "").split()
                    text = (
                        "".join(chr(reverse_cmap[glyph]) for glyph in label)
                        if label and all(glyph in reverse_cmap for glyph in label)
                        else case["text"]
                    )
                    with self.subTest(font=font_name, case=case.get("number", index)):
                        buffer = hb.Buffer()
                        buffer.add_str(text)
                        buffer.direction = "ltr"
                        buffer.script = "Egyp"
                        buffer.flags = hb.BufferFlags.PRESERVE_DEFAULT_IGNORABLES
                        hb.shape(reference, buffer, features)
                        expected = [order[info.codepoint] for info in buffer.glyph_infos]
                        tokens = [cmap.get(ord(char), ".notdef") for char in text]
                        actual = font.apply(tokens)[0]
                        self.assertEqual(actual, expected)
                        total += 1
        self.assertGreater(total, 0)
        print(f"\nCompared {total} real-font sequences against HarfBuzz.")

    def test_minimal_chained_format_one(self):
        with TTFont(Path(CORPUS) / "min_ship.ttf") as source:
            font = read_substitutions(source)
            context = next(
                lookup for lookup in font.GSUB_lookup_list
                if lookup.typ.split("/")[-1] == "6.1"
            )
            rule = next(rule for rule in context.substitutions if rule.refs)
            tokens = list(rule.lefts + rule.inputs + rule.rights)
            self.assertTrue(rule.applicable(tokens, len(rule.lefts), font, context))
            actual, applications = context.apply(tokens, font)
            self.assertTrue(applications)
            self.assertNotEqual(actual, tokens)
            xml = io.BytesIO()
            source.saveXML(xml)
            xml.seek(0)
            simulator = Simulator(read_ttx(xml, positioning=False))
            simulator.set_tokens(tokens)
            self.assertEqual(simulator.tokens, actual)
            self.assertTrue(simulator.steps_str())


if __name__ == "__main__":
    unittest.main()
