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

if fails:
    print("FAIL (%d)" % len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("ok: %d ops, %d registers, %d unique glyphs"
      % (len(icons.OPS), len(icons.REGS), len(seen)))