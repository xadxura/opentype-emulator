import os
from lxml import etree
from datetime import datetime

from gpos import (
	GlyphPosition, GlyphAttachment, SinglePositioning, PairPositioning, MarkPositioning,
	LigaturePositioning, ContextPositioning, positioning_application, resolve_origins, resolve_positions,
	CursivePositioning, apply_cursive_attachment, validate_direction,
)
from glyphrun import ContextRule, GlyphRun
from ttxtables import read_basic_properties, read_post

def equiv(elem1, elem2):
	if isinstance(elem1, list):
		return elem2 in elem1
	elif isinstance(elem2, list):
		return elem1 in elem2
	else:
		return elem1 == elem2

def equiv_list(l1, l2):
	return all([equiv(pair[0], pair[1]) for pair in zip(l1, l2)])

def is_suffix_of(l1, l2):
	return not l1 or (len(l1) <= len(l2) and equiv_list(l1, l2[-len(l1):]))
	
def is_prefix_of(l1, l2):
	return len(l1) <= len(l2) and equiv_list(l1, l2[:len(l1)])

def filter_glyph(glyph, font, lookup, *, glyph_class=None):
	glyph_class = font.glyph_to_class.get(glyph, 0) if glyph_class is None else glyph_class
	if lookup.ignore_base_glyphs and glyph_class == BASE_GLYPH:
		return False
	if lookup.ignore_ligatures and glyph_class == LIGATURE_GLYPH:
		return False
	if glyph_class == MARK_GLYPH:
		if lookup.ignore_marks:
			return False
		if lookup.filter_set is not None:
			return glyph in font.index_to_glyphs[lookup.filter_set]
		if lookup.mark_class != 0:
			return font.mark_to_class.get(glyph, 0) == lookup.mark_class
	return True

def filtered_positions(tokens, font, lookup, *, start=0, end=None, run=None):
	return [pos for pos in range(start, len(tokens) if end is None else end)
		if filter_glyph(tokens[pos], font, lookup,
			glyph_class=None if run is None else run.infos[pos].glyph_class)]

def filter_list(l, filter):
	return [token for token in l if filter(token)]

def first_filtered_left(l, filter):
	for i in range(len(l)):
		if filter(l[i]):
			return i
	return -1

def first_filtered_right(l, filter):
	for i in reversed(range(len(l))):
		if filter(l[i]):
			return i
	return -1

# Type 1
class SingleSubstitution1:
	def __init__(self, input, output):
		self.input = input
		self.output = output

	def length(self):
		return 1

	def recur(self, tokens, pos, font, lookup):
		return None

	def applicable(self, tokens, pos, font, lookup):
		return pos < len(tokens) and tokens[pos] == self.input

	def apply(self, tokens, pos, font, lookup):
		tokens = tokens.copy()
		tokens[pos] = self.output
		return tokens, 1, [pos]

	def __str__(self):
		return self.input + ' -> ' + self.output

# Type 2
class MultSubstitution:
	def __init__(self, input, outputs):
		self.input = input
		self.outputs = outputs

	def length(self):
		return 1

	def recur(self, tokens, pos, font, lookup):
		return None

	def applicable(self, tokens, pos, font, lookup):
		return pos < len(tokens) and tokens[pos] == self.input

	def apply(self, tokens, pos, font, lookup):
		tokens = tokens.copy()
		del tokens[pos]
		for token in reversed(self.outputs):
			tokens.insert(pos, token)
		return tokens, len(self.outputs), [pos]

	def __str__(self):
		return self.input + ' -> ' + ' '.join(self.outputs)

class AlternateSubstitution:
	def __init__(self, input, outputs):
		if not outputs:
			raise ValueError("An alternate substitution needs at least one alternate")
		self.input = input
		self.outputs = outputs

	def length(self):
		return 1

	def applicable(self, tokens, pos, font, lookup):
		return pos < len(tokens) and tokens[pos] == self.input

	def apply(self, tokens, pos, font, lookup, alternate_index=1):
		tokens = tokens.copy()
		tokens[pos] = self.outputs[alternate_index - 1]
		return tokens, 1, [pos]

	def __str__(self):
		return self.input + ' -> {' + ' '.join(self.outputs) + '}'

# Type 4
class LigSubstitution:
	def __init__(self, inputs, output):
		self.inputs = inputs
		self.output = output

	def length(self):
		return len(self.inputs)

	def recur(self, tokens, pos, font, lookup):
		return None

	def applicable(self, tokens, pos, font, lookup, run=None):
		if pos >= len(tokens) or tokens[pos] != self.inputs[0]:
			return False
		posses = [pos] + filtered_positions(tokens, font, lookup, start=pos + 1, run=run)[:len(self.inputs) - 1]
		return len(posses) == len(self.inputs) and \
			tuple(tokens[p] for p in posses) == tuple(self.inputs) and \
			(run is None or run.can_match(posses, lambda p: not filter_glyph(
				tokens[p], font, lookup, glyph_class=run.infos[p].glyph_class)))

	def apply(self, tokens, pos, font, lookup, run=None):
		posses = [pos] + filtered_positions(tokens, font, lookup, start=pos + 1, run=run)[:len(self.inputs) - 1]
		tokens = tokens.copy()
		for i in reversed(posses):
			del tokens[i]
		tokens.insert(pos, self.output)
		return tokens, 1, posses

	def __str__(self):
		return ' '.join(self.inputs) + ' -> ' + self.output

# Coverage-based representation shared by GSUB types 5 and 6, all formats.
class ChainSubstitution3(ContextRule):
	def recur(self, tokens, pos, font, lookup):
		posses = self.filtered_input_positions(tokens, pos, font, lookup)
		return [(posses[p], index) for (p, index) in self.refs]

	def applicable(self, tokens, pos, font, lookup, run=None):
		def eligible(p):
			return filter_glyph(tokens[p], font, lookup,
				glyph_class=None if run is None else run.infos[p].glyph_class)
		posses = self.match_positions(tokens, pos, eligible)
		return posses is not None and (run is None or run.can_match(posses, lambda p: not eligible(p)))

	def apply(self, tokens, pos, font, lookup):
		raise ValueError("Contextual substitutions must execute their referenced lookups")

	def filtered_input_positions(self, tokens, pos, font, lookup, run=None):
		return ([pos] + filtered_positions(tokens, font, lookup, start=pos + 1, run=run))[:len(self.inputs)]

class _InputSequence:
	def __init__(self, positions):
		self.positions = positions
		self.end = positions[-1] + 1

	def update(self, removed, output_count):
		anchor = removed[0]
		positions = []
		for p in self.positions:
			if p == anchor:
				positions.extend(range(anchor, anchor + output_count))
			elif p not in removed:
				positions.append(p - sum(r < p for r in removed) +
					(output_count if anchor < p else 0))
		# Ignored glyphs are not removed by ligatures; retain their exact positions.
		self.positions = positions
		self.end += (output_count if anchor < self.end else 0) - \
			sum(r < self.end for r in removed)

# Type 8
class ReverseSubstitution:
	def __init__(self, lefts, inputs, rights, outputs):
		self.lefts = lefts
		self.inputs = inputs
		self.rights = rights
		self.outputs = outputs
		if not inputs or len(inputs) != len(outputs):
			raise ValueError("Reverse substitution coverage and outputs must have equal nonzero lengths")

	def length(self):
		return len(self.lefts) + 1 + len(self.rights)

	def recur(self, tokens, pos, font, lookup):
		return None

	def applicable(self, tokens, pos, font, lookup, run=None):
		return pos < len(tokens) and tokens[pos] in self.inputs and \
			is_suffix_of(self.lefts, [tokens[p] for p in
				filtered_positions(tokens, font, lookup, end=pos, run=run)]) and \
			is_prefix_of(self.rights, [tokens[p] for p in
				filtered_positions(tokens, font, lookup, start=pos + 1, run=run)])

	def apply(self, tokens, pos, font, lookup):
		tokens = tokens.copy()
		tokens[pos] = self.outputs[self.inputs.index(tokens[pos])]
		return tokens, 1, [pos]

	def __str__(self):
		lefts = ' '.join('/'.join(g) for g in self.lefts)
		rights = ' '.join('/'.join(g) for g in self.rights)
		return lefts + '|' + '/'.join(self.inputs) + '|' + rights + \
			' -> ' + '/'.join(self.outputs)

class GSUB_Lookup:
	def __init__(self, index, typ):
		self.index = index
		self.typ = typ
		self.reverse = (typ == '7/8' or typ == '8')
		self.ignore_base_glyphs = False
		self.ignore_ligatures = False
		self.ignore_marks = False
		self.mark_class = 0
		self.filter_set: int | None = None
		self.substitutions = []

	def add(self, substitution):
		self.substitutions.append(substitution)

	# Normally one shouldn't use this. The textual order should be the order
	# in which rules are attempted.
	def reorder(self):
		self.substitutions = sorted(self.substitutions, key=lambda s : s.length())

	def apply(self, tokens, font, alternate_index=1, *, run=None):
		run = GlyphRun(tokens, font.glyph_to_class) if run is None else run
		pos = len(tokens) - 1 if self.reverse else 0
		direction = -1 if self.reverse else 1
		applications = []
		while 0 <= pos < len(tokens):
			if not filter_glyph(tokens[pos], font, self, glyph_class=run.infos[pos].glyph_class):
				pos += direction
				continue
			tokens, application, jump = self.apply_at(tokens, pos, font,
				_alternate_index=alternate_index, _run=run)
			if application is not None:
				applications.append(application)
				pos += -1 if self.reverse else jump
			else:
				pos += direction
		return tokens, applications

	def apply_at(self, tokens, pos, font, _sequences=(), _depth=0, _alternate_index=1, *, _run=None):
		if _depth >= 64:
			raise ValueError("Contextual lookup recursion exceeds 64 levels")
		if not 0 <= pos < len(tokens):
			return tokens, None, 0
		run = GlyphRun(tokens, font.glyph_to_class) if _run is None else _run
		for substitution in self.substitutions:
			if isinstance(substitution, (LigSubstitution, ChainSubstitution3, ReverseSubstitution)):
				applicable = substitution.applicable(tokens, pos, font, self, run)
			else:
				applicable = substitution.applicable(tokens, pos, font, self)
			if applicable:
				if isinstance(substitution, AlternateSubstitution) and \
						not 1 <= _alternate_index <= len(substitution.outputs):
					continue
				before = tokens
				before_infos = tuple(run.infos)
				children = []
				if isinstance(substitution, ChainSubstitution3):
					posses = substitution.filtered_input_positions(tokens, pos, font, self, run)
					sequence = _InputSequence(posses)
					for sequence_index, lookup_index in substitution.refs:
						if sequence_index >= len(sequence.positions):
							continue
						if lookup_index not in font.GSUB_lookups:
							raise ValueError("Unknown contextual lookup index: " + str(lookup_index))
						recur_lookup = font.GSUB_lookups[lookup_index]
						tokens, child, _ = recur_lookup.apply_at(
							tokens, sequence.positions[sequence_index], font,
							_sequences + (sequence,), _depth + 1, _alternate_index, _run=run)
						if child is not None:
							children.append(child)
					jump = sequence.end - pos
				else:
					if isinstance(substitution, AlternateSubstitution):
						tokens, jump, posses = substitution.apply(
							tokens, pos, font, self, _alternate_index)
					elif isinstance(substitution, LigSubstitution):
						tokens, jump, posses = substitution.apply(tokens, pos, font, self, run)
					else:
						tokens, jump, posses = substitution.apply(tokens, pos, font, self)
					output_count = len(tokens) - len(before) + len(posses)
					run.substitute(before, tokens, posses,
						multiple=isinstance(substitution, MultSubstitution),
						ligature=isinstance(substitution, LigSubstitution))
					for sequence in _sequences:
						sequence.update(posses, output_count)
				index = '/'.join([str(self.index)] + [child['index'] for child in children])
				application = {'index': index, 'posses': posses, 'rule': substitution,
					'before': before, 'tokens': tokens, 'children': children,
					'before_glyph_infos': before_infos, 'glyph_infos': tuple(run.infos)}
				return tokens, application, jump
		return tokens, None, 0

	def __str__(self):
		s = 'GSUB LOOKUP ' + str(self.index)
		if self.ignore_base_glyphs:
			s += 'ignore_base_glyphs '
		if self.ignore_ligatures:
			s += 'ignore_ligatures '
		if self.ignore_marks:
			s += 'ignore_marks '
		if self.mark_class != 0:
			s += str(self.mark_class) + ' '
		if self.filter_set:
			s += str(self.filter_set)
		s += '\n'
		for sub in self.substitutions:
			s += '\n' + sub
		return s

# Type 1
class SingleAdjustment:
	def __init__(self, form, adjustments):
		self.form = form
		self.adjustments = adjustments

	def length(self):
		return 1

	def recur(self):
		return None

	def applicable(self, tokens, pos, font, lookup):
		return tokens[pos] in [adj[0] for adj in self.adjustments]

	def apply(self, tokens, positionings, pos, font, lookup):
		positionings = positionings.copy()
		for adjs in self.adjustments:
			if adjs['glyph'] == tokens[pos]:
				placement = adjs['placement']
				if 'XPlacement' in placement:
					positionings['XPlacement'] = placement['XPlacement']
				if 'YPlacement' in placement:
					positionings['YPlacement'] = placement['YPlacement']
			return positionings

	def __str__(self):
		return ' '.join([str((g,a)) for (g,a) in self.adjustments])

class GPOS_Lookup:
	def __init__(self, index, typ):
		self.index = index
		self.typ = typ
		self.right_to_left = False
		self.ignore_base_glyphs = False
		self.ignore_ligatures = False
		self.ignore_marks = False
		self.mark_class = 0
		self.filter_set: int | None = None
		self.positionings = []

	def add_positioning(self, positioning):
		self.positionings.append(positioning)

	# Normally one shouldn't use this. The textual order should be the order
	# in which rules are attempted.
	def reorder(self):
		self.positionings = sorted(self.positionings, key=lambda s : s.length())

	def apply(self, tokens, positionings, font, *, attachments=None, glyph_infos=None, direction="ltr"):
		validate_direction(direction)
		self.ensure_supported()
		if len(tokens) != len(positionings):
			raise ValueError("Positioning needs one record per glyph")
		glyph_infos = GlyphRun(tokens, font.glyph_to_class).infos if glyph_infos is None else glyph_infos
		if len(glyph_infos) != len(tokens):
			raise ValueError("Positioning provenance needs one record per glyph")
		standalone = attachments is None
		attachments = {} if standalone else attachments
		applications = []
		pos = 0
		while pos < len(tokens):
			positionings, application, jump = self.apply_at(
				tokens, positionings, pos, font, attachments=attachments, glyph_infos=glyph_infos,
				direction=direction)
			if application is None:
				pos += 1
			else:
				applications.append(application)
				pos += jump
		return (resolve_positions(positionings, attachments, direction=direction) if standalone else positionings), applications

	def apply_at(self, tokens, positionings, pos, font, *, attachments=None, glyph_infos=None, _depth=0, direction="ltr"):
		validate_direction(direction)
		if _depth >= 64:
			raise ValueError("Contextual positioning recursion exceeds 64 levels")
		self.ensure_supported()
		if len(tokens) != len(positionings):
			raise ValueError("Positioning needs one record per glyph")
		glyph_infos = GlyphRun(tokens, font.glyph_to_class).infos if glyph_infos is None else glyph_infos
		if len(glyph_infos) != len(tokens):
			raise ValueError("Positioning provenance needs one record per glyph")
		def eligible(p):
			return filter_glyph(tokens[p], font, self, glyph_class=glyph_infos[p].glyph_class)
		# Nested lookup entry is fixed by SequenceIndex, not the child's scan flags.
		if not 0 <= pos < len(tokens) or (_depth == 0 and not eligible(pos)):
			return positionings, None, 0
		standalone = attachments is None
		attachments = {} if standalone else attachments
		second = None
		typ = self.typ.split('/')[-1]
		if typ == '2':
			second = next((p for p in range(pos + 1, len(tokens))
				if filter_glyph(tokens[p], font, self, glyph_class=glyph_infos[p].glyph_class)), None)
		elif typ == '3':
			second = next((p for p in range(pos - 1, -1, -1) if eligible(p)), None)
		elif typ == '6':
			for parent in range(pos - 1, -1, -1):
				# Base/ligature ignore flags must not bridge separate mark runs.
				if glyph_infos[parent].glyph_class != MARK_GLYPH:
					break
				if filter_glyph(tokens[parent], font, self, glyph_class=glyph_infos[parent].glyph_class):
					if glyph_infos[pos].compatible_mark(glyph_infos[parent]):
						second = parent
					break
		for positioning in self.positionings:
			if isinstance(positioning, ContextPositioning):
				posses = positioning.match_positions(tokens, pos, eligible)
				if posses is None or not GlyphRun.match_components(
						glyph_infos, posses, lambda p: not eligible(p), allow_ignored_parent=False):
					continue
				before = resolve_positions(positionings, attachments, direction=direction)
				before_attachments = attachments.copy()
				children = []
				for sequence_index, lookup_index in positioning.refs:
					if lookup_index not in font.GPOS_lookups:
						raise ValueError("Unknown contextual positioning lookup index: " + str(lookup_index))
					child_lookup = font.GPOS_lookups[lookup_index]
					positionings, child, _ = child_lookup.apply_at(
						tokens, positionings, posses[sequence_index], font,
						attachments=attachments, glyph_infos=glyph_infos, _depth=_depth + 1,
						direction=direction)
					if child is not None:
						children.append(child)
				application = positioning_application(
					index='/'.join([str(self.index)] + [child['index'] for child in children]),
					tokens=tokens, posses=posses, rule=positioning, before=before,
					after=resolve_positions(positionings, attachments, direction=direction), children=children,
					before_attachments=before_attachments, attachments=attachments, direction=direction)
				application['glyph_infos'] = tuple(glyph_infos)
				# Child cursor advancement never changes the outer matched input span.
				return (resolve_positions(positionings, attachments, direction=direction) if standalone else positionings), application, posses[-1] + 1 - pos
			if isinstance(positioning, CursivePositioning):
				anchors = positioning.match(tokens, pos, second)
				if anchors is None or second is None:
					continue
				before = resolve_positions(positionings, attachments, direction=direction)
				before_attachments = attachments.copy()
				positionings = apply_cursive_attachment(
					positionings, attachments, second, pos, *anchors,
					direction=direction, right_to_left=self.right_to_left)
				application = positioning_application(
					index=str(self.index), tokens=tokens, posses=(second, pos), rule=positioning,
					before=before, after=resolve_positions(positionings, attachments, direction=direction),
					before_attachments=before_attachments, attachments=attachments, direction=direction)
				application['glyph_infos'] = tuple(glyph_infos)
				return (resolve_positions(positionings, attachments, direction=direction) if standalone else positionings), application, 1
			if not isinstance(positioning, (SinglePositioning, PairPositioning, MarkPositioning, LigaturePositioning)):
				raise NotImplementedError("Unsupported GPOS positioning rule")
			if typ in ('4', '5'):
				if not isinstance(positioning, (MarkPositioning, LigaturePositioning)):
					raise ValueError("GPOS attachment lookup requires an attachment rule")
				second = None
				for parent in range(pos - 1, -1, -1):
					info = glyph_infos[parent]
					if info.glyph_class == MARK_GLYPH:
						continue
					previous = glyph_infos[parent - 1] if parent else None
					if info.accepts_attachment(previous, ligature=typ == '5') or tokens[parent] in positioning.parents:
						second = parent
						break
			if isinstance(positioning, LigaturePositioning):
				component = 0
				if second is not None and glyph_infos[second].ligature_id and \
						glyph_infos[second].ligature_id == glyph_infos[pos].ligature_id:
					component = glyph_infos[pos].component
				match = positioning.match(tokens, pos, second, component)
			else:
				match = positioning.match(tokens, pos, second)
			if match is None:
				continue
			before = resolve_positions(positionings, attachments, direction=direction)
			before_attachments = attachments.copy()
			positionings = positionings.copy()
			posses = [index for index, _ in match.adjustments]
			if match.attachment is not None:
				# Attachment replaces earlier placement, but not the mark's advance.
				position = positionings[pos]
				positionings[pos] = GlyphPosition(position.x_advance, position.y_advance)
				attachment = match.attachment
				# Horizontal mark attachment snapshots the cross-stream position.
				parent_y = resolve_origins(before, direction=direction)[attachment.parent][1]
				attachments[pos] = GlyphAttachment(
					attachment.parent, attachment.x_offset, attachment.y_offset, parent_y)
				posses.append(pos)
			for index, value in match.adjustments:
				# YAdvance is only used for vertical layout, which is not yet supported.
				positionings[index] = positionings[index].adjusted(
					x_advance=value.x_advance,
					x_offset=value.x_offset, y_offset=value.y_offset)
			application = positioning_application(
				index=str(self.index), tokens=tokens,
				posses=posses, rule=positioning, before=before,
				after=resolve_positions(positionings, attachments, direction=direction),
				before_attachments=before_attachments, attachments=attachments, direction=direction)
			application['glyph_infos'] = tuple(glyph_infos)
			if match.component is not None:
				application['ligature_component'] = match.component + 1
			return (resolve_positions(positionings, attachments, direction=direction) if standalone else positionings), application, match.next_pos - pos
		return positionings, None, 0

	def ensure_supported(self):
		if self.typ.split('/')[-1] not in ('1', '2', '3', '4', '5', '6', '7', '8'):
			raise NotImplementedError("GPOS lookup " + str(self.index) + " (type " +
				str(self.typ) + ") is unsupported")

	def __str__(self):
		s = ' GPOS LOOKUP ' + str(self.index)
		if self.filter_set:
			s += str(self.filter_set)
		for posit in self.positionings:
			s += '\n' + str(posit)
		return s

class Feature:
	def __init__(self, tag):
		self.tag = tag
		self.lookup_indexes = []

	def add_lookup_index(self, index):
		self.lookup_indexes.append(index)

	def __str__(self):
		return "FEATURE " + self.tag + ' ' + self.lookup_indexes

# Glyph classes (cf. glyph_to_class)
BASE_GLYPH = 1
LIGATURE_GLYPH = 2
MARK_GLYPH = 3
COMPONENT_GLYPH = 4

class Font:
	def __init__(self):
		self.properties = {}
		self.properties['head'] = {}
		self.properties['hhea'] = {}
		self.properties['vhea'] = {}
		self.properties['maxp'] = {}
		self.properties['OS_2'] = {}
		self.name = []
		self.palettes = []
		self.script = 'latn'

		self.cmap0 = {}
		self.cmap4 = {}
		self.charset_large = {}
		self.charset_small = {}
		self.charset_total = {}
		self.vs_to_name = {}

		self.glyphs = []
		self.width = {}
		self.lsb = {}
		self.height = {}
		self.tsb = {}
		self.xmin = {}
		self.ymin = {}
		self.xmax = {}
		self.ymax = {}
		self.contours = {}
		self.components = {}
		self.assemblies = {}
		self.post = {}
		self.extra_names = [] 
		self.color_layers = {}

		self.glyph_to_class = {}
		self.mark_to_class = {}
		self.index_to_glyphs = {}

		self.GSUB_features = []
		self.GSUB_lookup_list = []
		self.GSUB_lookups = {}
		self.GSUB_lookup_index_to_feature = {}
		self.GPOS_features = []
		self.GPOS_lookup_list = []
		self.GPOS_lookups = {}
		self.GPOS_lookup_index_to_feature = {}

	def set_property(self, section, prop, val):
		self.properties[section][prop] = str(val)

	def set_today(self):
		now = datetime.now().strftime('%a %b %d %H:%M:%S %Y')
		self.set_property('head', 'created', now)
		self.set_property('head', 'modified', now)

	def adjust_n_glyphs(self):
		n_glyphs = str(len(self.glyphs))
		self.set_property('hhea', 'numberOfHMetrics', n_glyphs)
		self.set_property('maxp', 'numGlyphs', n_glyphs)

	def add_glyph(self, name):
		self.glyphs.append(name)
		if name not in self.width:
			self.width[name] = 0
		if name not in self.lsb:
			self.lsb[name] = 0
		if name not in self.height:
			self.height[name] = 0
		if name not in self.tsb:
			self.tsb[name] = 0
		if name not in self.contours:
			self.contours[name] = []
		if name not in self.components:
			self.components[name] = []

	def complete_name(self, name, name_set):
		if name not in name_set:
			name_set.add(name)
			self.add_glyph(name)
		if name not in self.glyph_to_class:
			self.glyph_to_class[name] = MARK_GLYPH
		if name not in self.mark_to_class:
			self.mark_to_class[name] = 1

	def complete_glyph_list(self):
		name_set = set(self.glyphs)
		for name in self.charset_0.values():
			self.complete_name(name, name_set)
		for name in self.charset_large.values():
			self.complete_name(name, name_set)
		for name in self.charset_small.values():
			self.complete_name(name, name_set)
		for name in self.charset_total.values():
			self.complete_name(name, name_set)
		for name in self.extra_names:
			self.complete_name(name, name_set)

	def add_name_extra(self, name):
		self.add_glyph(name)
		self.extra_names.append(name)

	def add_GSUB_feature(self, feature):
		self.GSUB_features.append(feature)
		for lookup_index in feature.lookup_indexes:
			self.GSUB_lookup_index_to_feature[lookup_index] = feature

	def add_GSUB_lookup(self, index, lookup):
		self.GSUB_lookup_list.append(lookup)
		self.GSUB_lookups[index] = lookup

	def add_GPOS_feature(self, feature):
		self.GPOS_features.append(feature)
		for lookup_index in feature.lookup_indexes:
			self.GPOS_lookup_index_to_feature[lookup_index] = feature

	def add_GPOS_lookup(self, index, lookup):
		self.GPOS_lookup_list.append(lookup)
		self.GPOS_lookups[index] = lookup

	def string_to_tokens(self, s):
		return [self.charset_total[ord(c)] for c in s]

	def new_GSUB_lookup(self, t, feat=None):
		index = str(len(self.GSUB_lookup_list))
		lookup = GSUB_Lookup(index, t)
		self.add_GSUB_lookup(index, lookup)
		if feat is not None:
			feat.add_lookup_index(index)
		return lookup

	def apply(self, tokens, suppressed=[], *, feature_values=None, direction="ltr"):
		validate_direction(direction)
		feature_values = {} if feature_values is None else feature_values
		for tag, value in feature_values.items():
			if not isinstance(value, int) or value < 0:
				raise ValueError("Feature value for " + str(tag) + " must be a nonnegative integer")
		applications = []
		run = GlyphRun(tokens, self.glyph_to_class)
		for lookup in self.GSUB_lookup_list:
			tags = [feature.tag for feature in self.GSUB_features
				if lookup.index in feature.lookup_indexes and feature.tag not in suppressed
				and feature_values.get(feature.tag, 1) != 0]
			if tags:
				tokens, applications_lookup = lookup.apply(
					tokens, self, alternate_index=feature_values.get(tags[0], 1), run=run)
				for a in applications_lookup:
					a['feature'] = tags[0]
					applications.append(a)
		positionings = self.initial_positions(tokens)
		attachments = {}
		for lookup in self.GPOS_lookup_list:
			tags = [feature.tag for feature in self.GPOS_features
				if lookup.index in feature.lookup_indexes and feature.tag not in suppressed
				and feature_values.get(feature.tag, 1) != 0]
			if tags:
				positionings, applications_lookup = lookup.apply(
					tokens, positionings, self, attachments=attachments, glyph_infos=run.infos,
					direction=direction)
				for a in applications_lookup:
					a['feature'] = tags[0]
					applications.append(a)
		return tokens, resolve_positions(positionings, attachments, direction=direction), applications

	def initial_positions(self, tokens):
		for token in tokens:
			if token not in self.width:
				raise ValueError("Missing horizontal advance for glyph: " + token)
		return [GlyphPosition(x_advance=self.width[token]) for token in tokens]

	def shape(self, tokens, positionings, *, attachments=None, direction="ltr"):
		if len(tokens) != len(positionings):
			raise ValueError("Positioning needs one record per glyph")
		if any(not isinstance(position, GlyphPosition) for position in positionings):
			raise TypeError("Positioning records must be GlyphPosition instances, not legacy dictionaries")
		return resolve_origins(positionings, attachments, direction=direction)

	def render(self, tokens, suppressed=[], *, feature_values=None, direction="ltr"):
		tokens, positionings, applications = self.apply(
			tokens, suppressed=suppressed, feature_values=feature_values, direction=direction)
		return tokens, positionings, applications, self.shape(tokens, positionings, direction=direction)

	def __str__(self):
		s = ''
		for feature in self.GSUB_features:
			s += feature
		for lookup in self.GSUB_lookup_list:
			s += lookup
		for feature in self.GPOS_features:
			s += feature
		for lookup in self.GPOS_lookup_list:
			s += lookup
		return s

data_dir = 'data'

def starter_font():
	font = Font()
	font.add_glyph('.notdef')
	read_basic_properties(os.path.join(data_dir, 'standard_head.xml'), 'head', font)
	read_basic_properties(os.path.join(data_dir, 'standard_hhea.xml'), 'hhea', font)
	read_basic_properties(os.path.join(data_dir, 'standard_maxp.xml'), 'maxp', font)
	read_basic_properties(os.path.join(data_dir, 'standard_os2.xml'), 'OS_2', font)
	read_post(os.path.join(data_dir, 'standard_post.xml'), font)
	read_basic_properties(os.path.join(data_dir, 'standard_vhea.xml'), 'vhea', font)
	return font

class Simulator:
	def __init__(self, font):
		self.font = font
		self.suppressed = []
		self.feature_values = {}
		self.direction = "ltr"
		self.in_tokens = []
		self.tokens = []
		self.positionings = []
		self.applications = []
		self.places = []

	def set_tokens(self, tokens):
		self.in_tokens = tokens
		self.tokens, self.positionings, self.applications, self.places = \
			self.font.render(tokens, suppressed=self.suppressed, feature_values=self.feature_values,
				direction=self.direction)

	def set_string(self, string):
		self.in_tokens = self.font.string_to_tokens(string)
		self.set_tokens(self.in_tokens)

	def in_tokens_str(self):
		return ' '.join(self.in_tokens)
		
	def steps_str(self):
		s = ''
		for a in self.applications:
			s += 'feature: {}, lookup: {}, pos: {}'.format(a['feature'], a['index'], \
				','.join([str(p) for p in a['posses']])) + '\n'
			if 'positionings' not in a:
				s += str(a['rule']) + '\n'
				tokens_copy = a['tokens'].copy()
				tokens_copy.insert(a['posses'][0], '>')
				s += ' '.join(tokens_copy) + '\n'
			else:
				for index, (t, p) in enumerate(zip(a['tokens'], a['positionings'])):
					if index == a['posses'][0]:
						s += '> '
					s += t + '(' + str(p) + ')\n'
				s += '\n'
			s += '\n'
		return s

	def shaped_str(self):
		s = 'Shaped\n'
		for t, p in zip(self.tokens, self.places):
			s += t + str(p) + ' '
		return s
