#!/usr/bin/env python3
"""
test-icons.py -- invariants the glyph vocabulary must never break.

The emoji are the compiler's key: `icons.op_for(emoji)` looks a codepoint up in
a dict, so two glyphs sharing one would make the compiler silently resolve the
wrong instruction. That is the worst possible failure for a language, so it is
checked here rather than trusted.

    python3 test-icons.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icons

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# 1. every glyph is unique across the WHOLE vocabulary, ops and registers alike
seen = {}
for tbl, kind in ((icons.OPS, 'op'), (icons.REGS, 'reg')):
    for name, spec in tbl.items():
        e = spec['emoji']
        if e in seen:
            check(False, "emoji %r used twice: %s %s and %s %s"
                  % (e, seen[e][0], seen[e][1], kind, name))
        else:
            seen[e] = (kind, name)

# 2. round trip: every glyph resolves back to itself
for tbl, fn in ((icons.OPS, icons.op_for), (icons.REGS, icons.reg_for)):
    for name, spec in tbl.items():
        check(fn(spec['emoji']) == name,
              "%r does not resolve back to %s" % (spec['emoji'], name))

# 3. an unknown glyph resolves to nothing rather than to something plausible
check(icons.op_for('\U0001F600') is None, "unknown emoji must not resolve")
check(icons.reg_for('\U0001F600') is None, "unknown emoji must not resolve")

# 4. shapes carry drawable geometry, on a sane grid
for name, spec in icons.OPS.items():
    d = spec.get('d', '')
    check(bool(d), "op %s has no path data" % name)
    check(d.count('M') >= 1, "op %s path has no move" % name)
    check(spec['arity'] >= 0, "op %s negative arity" % name)
    check(0 <= spec['io'] <= spec['arity'],
          "op %s writes %d of %d operands" % (name, spec['io'], spec['arity']))
for name, spec in icons.REGS.items():
    check(bool(spec.get('out')), "reg %s has no outline" % name)
    check(spec.get('hue') in icons.HUES, "reg %s has unknown hue %r"
          % (name, spec.get('hue')))

# 5. every opcode in the table is one the compiler can actually emit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fsvgc
for name in icons.OPS:
    check(name.upper() in fsvgc.ARITY,
          "icons.py has %r but fsvgc has no ARITY entry for it" % name)

# 6. no glyph may name a register the compiler has reserved or does not have
for name in icons.REGS:
    check(name.upper() in fsvgc.REGS,
          "icons.py defines register %r that fsvgc does not have" % name)

# 7. NO TWO REGISTERS MAY DRAW THE SAME OUTLINE.
#
# This is not a style rule, it is the whole basis of the register table. The
# claim in the docs is that a register is identified by its outline and hue is
# only a secondary cue -- so two identical outlines make that claim false for
# that pair and leave hue carrying the identity alone, which is exactly what
# fails on a monochrome printout or for a colour-blind reader.
#
# r8 and r13 were both a cross for a while, and the emoji test above could not
# see it: the codepoints were distinct, so the compiler was perfectly correct
# while the drawing was useless. Uniqueness of the KEY and distinctness of the
# DRAWING are different invariants and need different checks.
import re as _re
import itertools as _it


def _sig(d):
    return _re.sub(r'\s+', '', d)


for a, b in _it.combinations(icons.REGS, 2):
    check(_sig(icons.REGS[a]['out']) != _sig(icons.REGS[b]['out']),
          "registers %s and %s draw the SAME outline" % (a, b))

# 7b. Nor may one outline be a strict prefix of another -- that is the
# "filled-in twin" case, where two shapes read as the same figure at a glance.
for a, b in _it.combinations(icons.REGS, 2):
    ta, tb = _sig(icons.REGS[a]['out']), _sig(icons.REGS[b]['out'])
    if ta != tb:
        short, long, names = ((ta, tb, (a, b)) if len(ta) < len(tb)
                              else (tb, ta, (b, a)))
        check(short not in long,
              "outline of %s is contained in %s -- they will read as one shape"
              % (names[0], names[1]))

# 7c. And no two may READ as the same shape, which the two checks above cannot
# establish. Comparing path strings proves the paths differ; it says nothing
# about whether a person can tell the figures apart. rax and r10 were both
# hexagons of slightly different size -- unequal as strings, identical to the
# eye -- and every check in this file passed them.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools'))
import shapes
for a, b in shapes.compare_all(icons.REGS):
    check(False, "registers %s and %s are not visually distinguishable" % (a, b))

# 8. Instruction icons get the WEAKER check, deliberately.
#
# They are mostly open strokes -- `nop` is a single straight line, two points
# and no silhouette at all -- so the radial-profile metric that registers get
# does not apply to them: it has nothing to measure, and forcing it would call
# two different arrows identical. So instead of a test that cannot judge them,
# they get the one that can, and the limitation is written down rather than
# papered over with a metric that produces a number regardless.
#
# What that check still catches is what actually goes wrong in practice: an icon
# duplicated outright (r8/r13 were), or one icon built by extending another
# (rdx sat inside r15). It makes no claim about whether two distinct drawings
# depict their instructions well -- that is a judgement no test here makes.
for kind, a, b in shapes.identical_or_nested(icons.OPS, 'd'):
    check(False, "instruction icons %s and %s are %s" % (a, b, kind.lower()))

# 8b. The load-bearing check for instructions is the EMOJI, not the drawing:
# the codepoint is what the compiler resolves, so a collision silently compiles
# to the wrong opcode. Test 1 covers the whole vocabulary; this states why it
# matters most here.
for name, spec in icons.OPS.items():
    check(icons.op_for(spec['emoji']) == name,
          "instruction %s does not resolve from its own emoji" % name)

if fails:
    print("FAIL (%d)" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ok: %d ops, %d registers, %d unique glyphs"
      % (len(icons.OPS), len(icons.REGS), len(seen)))