from lxml import etree

from gpos import (
	GlyphPosition, SinglePositioning, PairPositioning, ClassPairPositioning,
	MarkRecord, MarkPositioning, LigaturePositioning, ContextPositioning, CursivePositioning,
)
from ttxfont import Font, Feature, \
	GSUB_Lookup, SingleSubstitution1, MultSubstitution, AlternateSubstitution, \
	LigSubstitution, ChainSubstitution3, ReverseSubstitution, \
	GPOS_Lookup

def read_properties(doc, prop_name, font):
	elem = doc.find(prop_name)
	if elem is None:
		return
	for sub_elem in elem.findall('*'):
		if sub_elem.tag is None:
			None
		elif len(sub_elem.findall('*')) > 0:
			sub_properties = {}
			for subsub_elem in sub_elem.findall('*'):
				sub_properties[subsub_elem.tag] = subsub_elem.get('value')
			font.properties[prop_name][sub_elem.tag] = sub_properties
		else:
			font.properties[prop_name][sub_elem.tag] = sub_elem.get('value')

def read_name(elem, font):
	for sub_elem in elem.findall('namerecord'):
		nameID = sub_elem.get('nameID')
		platformID = sub_elem.get('platformID')
		platEncID = sub_elem.get('platEncID')
		langID = sub_elem.get('langID')
		unicode = sub_elem.get('unicode')
		text = sub_elem.text
		if unicode is None:
			unicode = 'False'
		font.name.append({'nameID': nameID, 'platformID': platformID, \
				'platEncID': platEncID, 'langID': langID, 'unicode': unicode, \
				'text': text})

def read_CPAL(elem, font):
	if elem is not None:
		for sub_elem in elem.findall('palette'):
			t = sub_elem.get('type')
			colors = []
			for color_elem in sub_elem.findall('color'):
				value = color_elem.get('value')
				colors.append(value)
			font.palettes.append({'type': t, 'colors': colors})

def read_cmap(elem, charset):
	if elem is not None:
		for map_elem in elem.findall('map'):
			code = int(map_elem.get('code'), 0)
			name = map_elem.get('name')
			charset[code] = name

def read_cmap14(elem, font):
	if elem is not None:
		for map_elem in elem.findall('map'):
			uv = int(map_elem.get('uv'), 0)
			uvs = int(map_elem.get('uvs'), 0)
			name = map_elem.get('name')
			font.vs_to_name[(uv, uvs)] = name

def read_GlyphOrder(elem, font):
	for sub_elem in elem.findall('GlyphID'):
		name = sub_elem.get('name')
		font.glyphs.append(name)

def read_hmtx(elem, font):
	for sub_elem in elem.findall('mtx'):
		name = sub_elem.get('name')
		width = int(sub_elem.get('width'))
		lsb = int(sub_elem.get('lsb'))
		font.width[name] = width
		font.lsb[name] = lsb

def read_vmtx(elem, font):
	if elem is None:
		return
	for sub_elem in elem.findall('mtx'):
		name = sub_elem.get('name')
		height = int(sub_elem.get('height'))
		tsb = int(sub_elem.get('tsb'))
		font.height[name] = height
		font.tsb[name] = tsb

def read_post(elem, font):
	for sub_elem in elem.findall('*'):
		if sub_elem.tag is None:
			None
		elif sub_elem.tag == 'psNames':
			None
		elif sub_elem.tag == 'extraNames':
			for psname_elem in sub_elem.findall('psName'):
				font.extra_names.append(psname_elem.get('name'))
		else:
			font.post[sub_elem.tag] = sub_elem.get('value')

def read_glyf(elem, font):
	for glyph_elem in elem.findall('TTGlyph'):
		name = glyph_elem.get('name')
		if 'xMin' in glyph_elem.attrib:
			font.xmin[name] = int(glyph_elem.get('xMin'))
		if glyph_elem.get('yMin') is not None:
			font.ymin[name] = int(glyph_elem.get('yMin'))
		if glyph_elem.get('xMax') is not None:
			font.xmax[name] = int(glyph_elem.get('xMax'))
		if glyph_elem.get('yMax') is not None:
			font.ymax[name] = int(glyph_elem.get('yMax'))
		contours = []
		for contour_elem in glyph_elem.findall('contour'):
			contour = []
			for pt_elem in contour_elem:
				pt = (pt_elem.get('x'), pt_elem.get('y'), pt_elem.get('on'))
				contour.append(pt)
			contours.append(contour)
		font.contours[name] = contours
		components = []
		for component_elem in glyph_elem.findall('component'):
			glyphName = component_elem.get('glyphName')
			x = component_elem.get('x')
			y = component_elem.get('y')
			scale = component_elem.get('scale')
			scalex = component_elem.get('scalex')
			scaley = component_elem.get('scaley')
			scale01 = component_elem.get('scale01')
			scale10 = component_elem.get('scale10')
			flags = component_elem.get('flags')
			component = {'glyphName': glyphName, 'x': x, 'y': y, 'flags': flags}
			if scale is not None:
				component['scale'] = scale
			if scalex is not None:
				component['scalex'] = scalex
			if scaley is not None:
				component['scaley'] = scaley
			if scale01 is not None:
				component['scale01'] = scale01
			if scale10 is not None:
				component['scale10'] = scale10
			components.append(component)
		font.components[name] = components

def read_COLR(elem, font):
	if elem is not None:
		for sub_elem in elem.findall('ColorGlyph'):
			glyph_name = sub_elem.get('name')
			layers = []
			for layer_elem in sub_elem.findall('layer'):
				colorID = int(layer_elem.get('colorID'))
				name = layer_elem.get('name')
				layers.append((colorID, name))
			font.color_layers[glyph_name] = layers

def read_GlyphClassDef(elem, font):
	if elem is None:
		return
	for def_elem in elem.findall('ClassDef'):
		glyph = def_elem.get('glyph')
		cl = int(def_elem.get('class'))
		font.glyph_to_class[glyph] = cl
	
def read_MarkAttachClassDef(elem, font):
	if elem is None:
		return
	for def_elem in elem.findall('ClassDef') :
		glyph = def_elem.get('glyph')
		cl = int(def_elem.get('class'))
		font.mark_to_class[glyph] = cl

def read_MarkGlyphSetsDef(elem, font):
	if elem is None:
		return
	for coverage_elem in elem.findall('Coverage'):
		index = int(coverage_elem.get('index'))
		glyph_elems = coverage_elem.findall('Glyph')
		glyphs = [g.get('value') for g in glyph_elems]
		font.index_to_glyphs[index] = glyphs

def read_coverage(cov):
	tokens = []
	for glyph_elem in cov.findall('Glyph'):
		tokens.append(glyph_elem.get('value'))
	return tokens

def read_flag(elem, lookup):
	flag = int(elem.get('value'))
	# https://learn.microsoft.com/en-us/typography/opentype/otspec160/chapter2
	lookup.right_to_left = bool(flag & 1) # only for GPOS Type 3
	lookup.ignore_base_glyphs = bool(flag & 2) # skips over base glyphs
	lookup.ignore_ligatures = bool(flag & 4) # skips over ligatures
	lookup.ignore_marks = bool(flag & 8) # skips over combining marks
	mark_filtering_set = bool(flag & 16) 
		# layout engine skips mark glyphs not in following mark filtering set
	lookup.mark_class = flag // 256

def read_single_subst(single, lookup):
	# Type 1: Replace one glyph with one glyph
	for sub_elem in single.findall('Substitution'):
		sub = SingleSubstitution1(sub_elem.get('in'), sub_elem.get('out'))
		lookup.add(sub)

def read_mult_subst(mult, lookup):
	# Type 2: Replace one glyph with more than one glyph
	for sub_elem in mult.findall('Substitution'):
		output = sub_elem.attrib['out']
		sub = MultSubstitution(sub_elem.get('in'), output.split(',') if output else [])
		lookup.add(sub)

def read_ligature_subst(lig, lookup):
	# Type 4: Replace multiple glyphs with one glyph
	for child in lig.findall('*'):
		first = [child.get('glyph')]
		for l in child.findall('*'):
			comp_str = l.get('components')
			comps = [] if comp_str == '' else comp_str.split(',')
			inputs = first + comps
			output = l.get('glyph')
			sub = LigSubstitution(inputs, output)
			lookup.add(sub)

def read_lookup_records(elem, tag):
	result = []
	for record in elem.findall(tag):
		values = []
		for field in ('SequenceIndex', 'LookupListIndex'):
			value = record.find(field)
			if value is None or value.get('value') is None:
				raise ValueError("Missing contextual lookup record field: " + field)
			values.append(int(value.attrib['value']))
		result.append((values[0], values[1]))
	return result

def read_context_rules(elem, font, *, positioning=False):
	format = elem.get('Format')
	chained = elem.tag in ('ChainContextSubst', 'ChainContextPos')
	family = 'Pos' if positioning else 'Sub'
	record_tag = 'PosLookupRecord' if positioning else 'SubstLookupRecord'
	def required_value(elem, attribute='value'):
		value = elem.get(attribute)
		if value is None:
			raise ValueError("Missing contextual " + elem.tag + " " + attribute)
		return value
	def coverage(cov):
		if cov is None:
			raise ValueError("Missing contextual coverage")
		return [required_value(glyph) for glyph in cov.findall('Glyph')]
	def index(elem):
		value = int(required_value(elem, 'index'))
		if value < 0:
			raise ValueError("Contextual rule set index must be nonnegative")
		return value
	def class_index(value):
		result = int(value)
		if result < 0:
			raise ValueError("Contextual class index must be nonnegative")
		return result
	if format == '3':
		lefts = [coverage(cov) for cov in elem.findall('BacktrackCoverage')]
		input_tag = 'InputCoverage' if chained else 'Coverage'
		inputs = [coverage(cov) for cov in elem.findall(input_tag)]
		rights = [coverage(cov) for cov in elem.findall('LookAheadCoverage')]
		# Serialized backtrack arrays are nearest-first, not logical token order.
		yield lefts[::-1], inputs, rights, read_lookup_records(elem, record_tag)
		return
	if format not in ('1', '2'):
		raise NotImplementedError("Unsupported " + elem.tag + " format: " + str(format))
	glyphs = coverage(elem.find('Coverage'))
	if format == '1':
		rule_tag = ('Chain' if chained else '') + family + 'Rule'
		set_tag = rule_tag + 'Set'
		for rule_set in elem.findall(set_tag):
			first_index = index(rule_set)
			if first_index >= len(glyphs):
				raise ValueError("Contextual rule set index is outside coverage")
			first = glyphs[first_index]
			for rule in rule_set.findall(rule_tag):
				lefts = [required_value(g) for g in rule.findall('Backtrack')]
				inputs = [first] + [required_value(g) for g in rule.findall('Input')]
				rights = [required_value(g) for g in rule.findall('LookAhead')]
				yield lefts[::-1], inputs, rights, read_lookup_records(rule, record_tag)
	elif format == '2':
		def classes(tag):
			class_def = elem.find(tag)
			defs = {} if class_def is None else {
				required_value(g, 'glyph'): class_index(required_value(g, 'class'))
				for g in class_def.findall('ClassDef')}
			groups = {0: []}
			for glyph in font.glyphs:
				groups.setdefault(defs.get(glyph, 0), []).append(glyph)
			return groups
		input_classes = classes('InputClassDef' if chained else 'ClassDef')
		backtrack_classes = classes('BacktrackClassDef') if chained else {}
		lookahead_classes = classes('LookAheadClassDef') if chained else {}
		prefix = ('Chain' if chained else '') + family + 'Class'
		set_tag, rule_tag = prefix + 'Set', prefix + 'Rule'
		for rule_set in elem.findall(set_tag):
			first = [g for g in input_classes.get(index(rule_set), []) if g in glyphs]
			for rule in rule_set.findall(rule_tag):
				lefts = [backtrack_classes.get(class_index(required_value(g)), []) for g in rule.findall('Backtrack')]
				input_tag = 'Input' if chained else 'Class'
				inputs = [first] + [input_classes.get(class_index(required_value(g)), []) for g in rule.findall(input_tag)]
				rights = [lookahead_classes.get(class_index(required_value(g)), []) for g in rule.findall('LookAhead')]
				yield lefts[::-1], inputs, rights, read_lookup_records(rule, record_tag)

def read_context_subst(elem, lookup, font):
	for args in read_context_rules(elem, font):
		lookup.add(ChainSubstitution3(*args))

def read_context_pos(elem, lookup, font):
	for args in read_context_rules(elem, font, positioning=True):
		lookup.add_positioning(ContextPositioning(*args))

def read_reverse_subst(reverse, lookup):
	if reverse.get('Format') != '1':
		raise NotImplementedError("Unsupported ReverseChainSingleSubst format: " +
			str(reverse.get('Format')))
	lefts = [read_coverage(cov) for cov in reverse.findall('BacktrackCoverage')]
	inputs = read_coverage(reverse.find('Coverage'))
	rights = [read_coverage(cov) for cov in reverse.findall('LookAheadCoverage')]
	outputs = [glyph.get('value') for glyph in reverse.findall('Substitute')]
	lookup.add(ReverseSubstitution(lefts[::-1], inputs, rights, outputs))

def read_alternate_subst(elem, lookup):
	for alternate_set in elem.findall('AlternateSet'):
		lookup.add(AlternateSubstitution(alternate_set.attrib['glyph'],
			[glyph.attrib['glyph'] for glyph in alternate_set.findall('Alternate')]))

def read_single_pos(single, lookup):
	format = single.get('Format')
	if format not in ('1', '2'):
		raise NotImplementedError("Unsupported SinglePos format: " + str(format))
	glyphs = read_coverage(single.find('Coverage'))
	value_format = read_value_format(single.find('ValueFormat'))
	values = [read_position_value(value, value_format) for value in single.findall('Value')]
	if format == '1':
		if not values and value_format == 0:
			values = [GlyphPosition()]
		if len(values) != 1:
			raise ValueError("SinglePos format 1 needs exactly one ValueRecord")
		values = values * len(glyphs)
	if len(values) != len(glyphs):
		raise ValueError("SinglePos coverage and ValueRecord counts differ")
	lookup.add_positioning(SinglePositioning(dict(zip(glyphs, values))))

def read_value_format(elem):
	value = int(elem.attrib['value'], 0)
	if value < 0 or value & ~0xF:
		raise NotImplementedError("Unsupported GPOS ValueFormat device/variation or reserved fields: " + str(value))
	return value

def read_position_value(elem, value_format):
	if elem is None:
		if value_format != 0:
			raise ValueError("Missing GPOS ValueRecord")
		return GlyphPosition()
	if any(isinstance(child.tag, str) for child in elem):
		raise NotImplementedError("GPOS device/variation adjustments are not supported")
	fields = {'XPlacement': (1, 'x_offset'), 'YPlacement': (2, 'y_offset'),
		'XAdvance': (4, 'x_advance'), 'YAdvance': (8, 'y_advance')}
	unexpected = set(elem.attrib) - set(fields) - {'index'}
	if unexpected:
		raise NotImplementedError("Unsupported GPOS ValueRecord fields: " + ', '.join(sorted(unexpected)))
	values = {}
	for field, (bit, name) in fields.items():
		if field in elem.attrib and not value_format & bit:
			raise ValueError("GPOS ValueRecord field is absent from ValueFormat: " + field)
		if value_format & bit:
			if field not in elem.attrib:
				raise ValueError("Missing GPOS ValueRecord field: " + field)
			values[name] = int(elem.attrib[field])
	return GlyphPosition(**values)

def read_pair_pos(pair, lookup):
	format = pair.get('Format')
	if format not in ('1', '2'):
		raise NotImplementedError("Unsupported PairPos format: " + str(format))
	glyphs = read_coverage(pair.find('Coverage'))
	format1 = read_value_format(pair.find('ValueFormat1'))
	format2 = read_value_format(pair.find('ValueFormat2'))
	values = {}
	if format == '1':
		sets = pair.findall('PairSet')
		if len(sets) != len(glyphs):
			raise ValueError("PairPos coverage and PairSet counts differ")
		for first, pair_set in zip(glyphs, sets):
			for record in pair_set.findall('PairValueRecord'):
				second = record.find('SecondGlyph').attrib['value']
				key = (first, second)
				if key in values:
					raise ValueError("Duplicate PairPos glyph pair")
				values[key] = (read_position_value(record.find('Value1'), format1),
					read_position_value(record.find('Value2'), format2))
		lookup.add_positioning(PairPositioning(values, format2 != 0))
	else:
		def classes(tag):
			elem = pair.find(tag)
			return {} if elem is None else {g.attrib['glyph']: int(g.attrib['class'])
				for g in elem.findall('ClassDef')}
		class1, class2 = classes('ClassDef1'), classes('ClassDef2')
		rows = pair.findall('Class1Record')
		widths = {len(row.findall('Class2Record')) for row in rows}
		if not rows or len(widths) != 1 or 0 in widths:
			raise ValueError("PairPos needs a nonempty rectangular class matrix")
		width = next(iter(widths))
		if any(value < 0 or value >= len(rows) for value in class1.values()) or \
				any(value < 0 or value >= width for value in class2.values()):
			raise ValueError("PairPos class definition is outside the class matrix")
		for i, row in enumerate(rows):
			for j, record in enumerate(row.findall('Class2Record')):
				values[(i, j)] = (read_position_value(record.find('Value1'), format1),
					read_position_value(record.find('Value2'), format2))
		lookup.add_positioning(ClassPairPositioning(glyphs, class1, class2, values, format2 != 0))

def read_anchor(elem):
	if elem is None or elem.get('empty') == '1':
		return None
	format = elem.get('Format')
	if format not in ('1', '2', '3'):
		raise NotImplementedError("Unsupported GPOS anchor format: " + str(format))
	allowed = {'XCoordinate', 'YCoordinate'}
	if format == '2':
		allowed.add('AnchorPoint')
	for child in elem:
		if not isinstance(child.tag, str):
			continue
		if format == '3' and child.tag in ('XDeviceTable', 'YDeviceTable') and child.get('empty') == '1':
			continue
		if child.tag not in allowed:
			raise NotImplementedError("GPOS anchor device/variation or unsupported field: " + child.tag)
	values = {}
	for field in allowed:
		value = elem.find(field)
		if value is None or value.get('value') is None:
			raise ValueError("Missing GPOS anchor field: " + field)
		values[field] = int(value.get('value'))
	if format == '2' and values['AnchorPoint'] < 0:
		raise ValueError("Invalid GPOS anchor point index")
	# Without hinting/ppem, format 2 uses its stored coordinates, not outline points.
	return values['XCoordinate'], values['YCoordinate']

def read_cursive_pos(cursive, lookup):
	if cursive.get('Format') != '1':
		raise NotImplementedError("Unsupported CursivePos format: " + str(cursive.get('Format')))
	coverage = cursive.find('Coverage')
	if coverage is None:
		raise ValueError("Missing GPOS cursive coverage")
	glyphs = read_coverage(coverage)
	if any(glyph is None for glyph in glyphs) or len(set(glyphs)) != len(glyphs):
		raise ValueError("Invalid or duplicate GPOS cursive coverage glyph")
	records = cursive.findall('EntryExitRecord')
	if len(records) != len(glyphs):
		raise ValueError("GPOS cursive coverage and EntryExitRecord counts differ")
	anchors = {}
	for index, (glyph, record) in enumerate(zip(glyphs, records)):
		if int(record.get('index', str(index))) != index:
			raise ValueError("Invalid GPOS EntryExitRecord index")
		anchors[glyph] = (read_anchor(record.find('EntryAnchor')), read_anchor(record.find('ExitAnchor')))
	lookup.add_positioning(CursivePositioning(anchors))

def read_mark_pos(mark_pos, lookup):
	if mark_pos.get('Format') != '1':
		raise NotImplementedError("Unsupported " + mark_pos.tag + " format: " + str(mark_pos.get('Format')))
	to_mark = mark_pos.tag == 'MarkMarkPos'
	to_ligature = mark_pos.tag == 'MarkLigPos'
	mark_prefix = 'Mark1' if to_mark else 'Mark'
	parent_prefix = 'Ligature' if to_ligature else 'Mark2' if to_mark else 'Base'
	def coverage(prefix):
		elem = mark_pos.find(prefix + 'Coverage')
		if elem is None:
			raise ValueError("Missing GPOS " + prefix + "Coverage")
		glyphs = read_coverage(elem)
		if len(set(glyphs)) != len(glyphs):
			raise ValueError("Duplicate GPOS mark coverage glyph")
		return glyphs

	def records(elem, tag, count=None):
		if elem is None:
			raise ValueError("Missing GPOS mark array")
		result = elem.findall(tag)
		if count is not None and len(result) != count:
			raise ValueError("GPOS mark coverage/class and " + tag + " counts differ")
		for index, record in enumerate(result):
			if int(record.get('index', str(index))) != index:
				raise ValueError("Invalid GPOS " + tag + " index")
		return result

	mark_glyphs, parent_glyphs = coverage(mark_prefix), coverage(parent_prefix)
	mark_records = records(mark_pos.find(mark_prefix + 'Array'), 'MarkRecord', len(mark_glyphs))
	parent_records = records(mark_pos.find(parent_prefix + 'Array'),
		'LigatureAttach' if to_ligature else parent_prefix + 'Record', len(parent_glyphs))
	parent_rows = []
	for record in parent_records:
		if to_ligature:
			if record.get('empty') == '1':
				raise ValueError("Missing GPOS ligature attachment")
			rows = records(record, 'ComponentRecord')
			count_elem = record.find('ComponentCount')
			if count_elem is not None and int(count_elem.attrib['value']) != len(rows):
				raise ValueError("GPOS ligature component counts differ")
			parent_rows.append(rows)
		else:
			parent_rows.append([record])
	# TTX writes ClassCount as a comment; the anchor matrix preserves its width.
	widths = {len(row.findall(parent_prefix + 'Anchor')) for rows in parent_rows for row in rows}
	if len(widths) > 1:
		raise ValueError("GPOS mark anchor class counts differ")
	class_count = next(iter(widths), None)
	class_elem = mark_pos.find('ClassCount')
	if class_elem is not None:
		declared_count = int(class_elem.attrib['value'])
		if declared_count < 0 or (class_count is not None and declared_count != class_count):
			raise ValueError("GPOS mark ClassCount differs from anchor counts")
		class_count = declared_count
	marks = {}
	for glyph, record in zip(mark_glyphs, mark_records):
		class_elem = record.find('Class')
		if class_elem is None or class_elem.get('value') is None:
			raise ValueError("Missing GPOS mark class")
		mark_class = int(class_elem.get('value'))
		if mark_class < 0 or (class_count is not None and mark_class >= class_count):
			raise ValueError("GPOS mark class is outside ClassCount")
		anchor = read_anchor(record.find('MarkAnchor'))
		if anchor is None:
			raise ValueError("Missing GPOS mark anchor")
		marks[glyph] = MarkRecord(mark_class, anchor)
	parents = {}
	for glyph, rows in zip(parent_glyphs, parent_rows):
		components = []
		for row in rows:
			anchors = records(row, parent_prefix + 'Anchor', class_count)
			values = {}
			for mark_class, elem in enumerate(anchors):
				anchor = read_anchor(elem)
				if anchor is not None:
					values[mark_class] = anchor
			components.append(values)
		parents[glyph] = tuple(components) if to_ligature else components[0]
	if to_ligature:
		lookup.add_positioning(LigaturePositioning(marks, parents))
	else:
		lookup.add_positioning(MarkPositioning(marks, parents, to_mark))

def read_subst_subtable(child, lookup, font):
	if child.tag == 'SingleSubst':
		read_single_subst(child, lookup)
	elif child.tag == 'MultipleSubst':
		read_mult_subst(child, lookup)
	elif child.tag == 'AlternateSubst':
		read_alternate_subst(child, lookup)
	elif child.tag == 'LigatureSubst':
		read_ligature_subst(child, lookup)
	elif child.tag in ('ChainContextSubst', 'ContextSubst'):
		read_context_subst(child, lookup, font)
	elif child.tag == 'ReverseChainSingleSubst':
		read_reverse_subst(child, lookup)
	else:
		raise NotImplementedError("Unsupported GSUB subtable: " + str(child.tag))

def read_ext_subst(ext, lookup, font):
	for child in ext.findall('*'):
		if isinstance(child.tag, str) and child.tag != 'ExtensionLookupType':
			read_subst_subtable(child, lookup, font)

def read_GSUB_lookup(lookup_elem, font):
	index = int(lookup_elem.get('index'))
	typ = lookup_elem.find('LookupType').get('value')
	if lookup_elem.find('.//ExtensionLookupType') is not None:
		typ = '7/' + lookup_elem.find('.//ExtensionLookupType').get('value')
	if lookup_elem.find('.//ChainContextSubst') is not None:
		typ += '.' + lookup_elem.find('.//ChainContextSubst').get('Format')
	lookup = GSUB_Lookup(index, typ)
	for child in lookup_elem.findall('*'):
		if not isinstance(child.tag, str) or child.tag == 'LookupType':
			continue
		elif child.tag == 'LookupFlag':
			read_flag(child, lookup)
		elif child.tag == 'ExtensionSubst':
			read_ext_subst(child, lookup, font)
		elif child.tag == 'MarkFilteringSet':
			lookup.filter_set = int(child.get('value'))
		else:
			read_subst_subtable(child, lookup, font)
	font.add_GSUB_lookup(index, lookup)

def read_GSUB(table, font):
	if table is None:
		return
	scripttag_elem = table.find('.//ScriptTag')
	if scripttag_elem is not None:
		font.script = scripttag_elem.get('value')
	for f_record_elem in table.findall('FeatureList/FeatureRecord'):
		f_tag = f_record_elem.find('FeatureTag').get('value')
		feature = Feature(f_tag)
		lookup_elems = f_record_elem.findall('Feature/LookupListIndex')
		for lookup_elem in lookup_elems:
			index = int(lookup_elem.get('value'))
			feature.add_lookup_index(index)
		font.add_GSUB_feature(feature)
	lookup_elems = table.findall('.//Lookup')
	for lookup_elem in lookup_elems:
		read_GSUB_lookup(lookup_elem, font)

def read_ext_pos(ext, lookup, font):
	if ext.get('Format') != '1':
		raise NotImplementedError("Unsupported ExtensionPos format: " + str(ext.get('Format')))
	for child in ext.findall('*'):
		if isinstance(child.tag, str) and child.tag != 'ExtensionLookupType':
			read_pos_subtable(child, lookup, font)

def read_pos_subtable(elem, lookup, font):
	expected = {'1': 'SinglePos', '2': 'PairPos', '3': 'CursivePos', '4': 'MarkBasePos', '5': 'MarkLigPos',
		'6': 'MarkMarkPos', '7': 'ContextPos', '8': 'ChainContextPos'}[lookup.typ.split('/')[-1]]
	if elem.tag != expected:
		raise ValueError("GPOS subtable does not match lookup type: " + str(elem.tag))
	if elem.tag == 'SinglePos':
		read_single_pos(elem, lookup)
	elif elem.tag == 'PairPos':
		read_pair_pos(elem, lookup)
	elif elem.tag == 'CursivePos':
		read_cursive_pos(elem, lookup)
	elif elem.tag in ('MarkBasePos', 'MarkLigPos', 'MarkMarkPos'):
		read_mark_pos(elem, lookup)
	elif elem.tag in ('ContextPos', 'ChainContextPos'):
		read_context_pos(elem, lookup, font)
	else:
		raise NotImplementedError("Unsupported GPOS subtable: " + str(elem.tag))

def read_GPOS_lookup(lookup_elem, font):
	index = int(lookup_elem.get('index'))
	typ = lookup_elem.find('LookupType').get('value')
	extension = lookup_elem.find('.//ExtensionLookupType')
	if (typ == '9') != (extension is not None):
		raise ValueError("GPOS extension lookup type and subtables do not match")
	if extension is not None:
		typ += '/' + extension.get('value')
	lookup = GPOS_Lookup(index, typ)
	flag = lookup_elem.find('LookupFlag')
	if flag is not None:
		read_flag(flag, lookup)
	filter_set = lookup_elem.find('MarkFilteringSet')
	if filter_set is not None:
		lookup.filter_set = int(filter_set.get('value'))
	if typ.split('/')[-1] in ('1', '2', '3', '4', '5', '6', '7', '8'):
		for child in lookup_elem:
			if not isinstance(child.tag, str) or child.tag in ('LookupType', 'LookupFlag', 'MarkFilteringSet'):
				continue
			if child.tag == 'ExtensionPos':
				if child.find('ExtensionLookupType').get('value') != typ.split('/')[-1]:
					raise ValueError("GPOS extension subtables must have the same lookup type")
				read_ext_pos(child, lookup, font)
			else:
				if typ.startswith('9/'):
					raise ValueError("GPOS extension lookup requires ExtensionPos subtables")
				read_pos_subtable(child, lookup, font)
	font.add_GPOS_lookup(index, lookup)

def read_GPOS(table, font):
	for f_record_elem in table.findall('FeatureList/FeatureRecord'):
		f_tag = f_record_elem.find('FeatureTag').get('value')
		feature = Feature(f_tag)
		lookup_elems = f_record_elem.findall('Feature/LookupListIndex')
		for lookup_elem in lookup_elems:
			index = int(lookup_elem.get('value'))
			feature.add_lookup_index(index)
		font.add_GPOS_feature(feature)
	lookup_elems = table.findall('.//Lookup')
	for lookup_elem in lookup_elems:
		read_GPOS_lookup(lookup_elem, font)

def read_ttx(filename, *, positioning=True):
	font = Font()
	doc = etree.parse(filename)
	read_properties(doc, 'head', font)
	read_properties(doc, 'hhea', font)
	read_properties(doc, 'vhea', font)
	read_properties(doc, 'maxp', font)
	read_properties(doc, 'OS_2', font)
	read_name(doc.find('name'), font)
	read_CPAL(doc.find('CPAL'), font)

	read_cmap(doc.find('cmap/cmap_format_4[@platformID="0"]'), font.charset_large)
	read_cmap(doc.find('cmap/cmap_format_6'), font.charset_small)
	read_cmap(doc.find('cmap/cmap_format_12[@platformID="0"]'), font.charset_total)
	for format, charset in ((4, font.charset_large), (6, font.charset_small),
			(12, font.charset_total)):
		for cmap in doc.findall('cmap/cmap_format_' + str(format)):
			if cmap.get('platformID') == '0' or \
					(cmap.get('platformID') == '3' and cmap.get('platEncID') in ('1', '10')):
				read_cmap(cmap, charset)
	font.charset_total = {**font.charset_large, **font.charset_small, **font.charset_total}
	read_cmap14(doc.find('cmap/cmap_format_14'), font)

	read_GlyphOrder(doc.find('GlyphOrder'), font)
	read_hmtx(doc.find('hmtx'), font)
	read_vmtx(doc.find('vmtx'), font)
	read_post(doc.find('post'), font)
	read_glyf(doc.find('glyf'), font)
	read_COLR(doc.find('COLR'), font)

	read_GlyphClassDef(doc.find('GDEF/GlyphClassDef'), font)
	read_MarkAttachClassDef(doc.find('GDEF/MarkAttachClassDef'), font)
	read_MarkGlyphSetsDef(doc.find('GDEF/MarkGlyphSetsDef'), font)
	read_GSUB(doc.find('GSUB'), font)
	if positioning and doc.find('GPOS') is not None:
		read_GPOS(doc.find('GPOS'), font)
	return font
