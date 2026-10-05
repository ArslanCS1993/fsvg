#!/usr/bin/env python3
"""
shapes.py -- can a human tell these two outlines apart?

The register table claims that a register is identified by its OUTLINE, with hue
only as a secondary cue. A string comparison cannot check that claim: it proves
two paths differ, which is not the same as proving two shapes look different.

`rax` and `r10` are the case in point. They are not equal as strings -- rax is a
hexagon of radius 8, r10 a hexagon of radius 10 -- so every "are these paths
identical" test passes. Rendered, they are both hexagons, and a reader holding a
diagram in black and white cannot tell them apart. The test was green and the
claim was still false.

So this module compares the thing a reader actually sees: a signature sampled
from the drawn silhouette, invariant to size and rotation-free. Two outlines
whose signatures agree within a tolerance will look alike on paper.

    from shapes import signature, too_similar
    too_similar('rax', 'r10')   # -> True

Deliberately not a general SVG parser. It handles the command subset the
register table uses (M, L/l, H/h, V/v, Z, and the A/a arc in rdi) and refuses
anything else loudly rather than guessing at geometry it did not understand --
a signature computed from a half-parsed path would be confidently wrong.
"""

import math
import re

# ------------------------------------------------------------------ sampling

# How many directions around the silhouette we probe, and how finely we step
# along each command. A silhouette sampled at 24 angles has enough resolution to
# distinguish the shapes in this table -- two hexagons differ in their radius
# profile by far more than the tolerance -- while staying cheap.
ANGLES = 24
STEPS_PER_COMMAND = 12

# The tolerance is a measured number, not a guess. Two figures are confusable
# when the worst angular disagreement between their profiles is below this.
#
#   identical outlines (the r8/r13 cross) scored 0.000
#   concentric diamond vs concentric square (r12/r14) scored 0.008
#   after the redesign, the CLOSEST pair in the table is 0.072
#       (rbx, a plain square, vs r14, a square with an inner square)
#
# 0.06 sits under that 0.072 with room to spare, while still being two orders of
# magnitude away from 0.000. So it passes the current table honestly and would
# still fail on a real duplicate. Loosening it to 0.08 to admit two hexagons
# (rax/r10, which read as "a hexagon" to anyone looking at them) would have been
# the wrong trade: two registers must not be confusable, so the shapes get
# redesigned instead of the threshold.
TOL = 0.06


def _flatten(d):
    """Path data -> a list of (x, y) points along the outline.

    Only the commands this project actually uses are handled. Anything else
    raises, because a signature built from a path we only half understood is
    worse than no signature at all.
    """
    toks = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:e-?\d+)?', d)
    pts, cur, start = [], (0.0, 0.0), (0.0, 0.0)
    i, cmd = 0, None
    while i < len(toks):
        t = toks[i]
        if re.match(r'[A-Za-z]', t):
            cmd, i = t, i + 1
            if cmd in 'Zz':
                cur = start
                pts.append(cur)
                continue
        elif cmd is None:
            raise ValueError('path does not start with a command: %r' % d)
        elif cmd in 'Mm':
            cmd = 'L' if cmd == 'M' else 'l'   # subsequent pairs are implicit L

        def num():
            nonlocal i
            v = float(toks[i])
            i += 1
            return v

        if cmd in 'MmLl':
            x, y = num(), num()
            if cmd in 'Ll':
                x, y = cur[0] + x, cur[1] + y
            cur = (x, y)
            if cmd in 'Mm':
                start = cur
            pts.append(cur)
        elif cmd in 'Hh':
            x = num()
            cur = (cur[0] + x if cmd == 'h' else x, cur[1])
            pts.append(cur)
        elif cmd in 'Vv':
            y = num()
            cur = (cur[0], cur[1] + y if cmd == 'v' else y)
            pts.append(cur)
        elif cmd in 'CcSsQqTt':
            raise ValueError('command %r not used by this table (%r)' % (cmd, d))
        elif cmd in 'Aa':
            rx, ry, rot, laf, sf, x, y = (num() for _ in range(7))
            if cmd == 'a':
                x, y = cur[0] + x, cur[1] + y
            # An arc is sampled as its bounding ellipse. For the one arc in the
            # table (rdi's key bow) that is exact enough to separate it from
            # every other outline, and it is stated here rather than hidden.
            cx, cy = cur[0] + (x - cur[0]) / 2, cur[1] + (y - cur[1]) / 2
            for k in range(1, STEPS_PER_COMMAND + 1):
                th = math.pi * k / STEPS_PER_COMMAND
                pts.append((cx + rx * math.cos(th), cy + ry * math.sin(th)))
            cur = (x, y)
            pts.append(cur)
        else:
            raise ValueError('unhandled command %r in %r' % (cmd, d))
    return pts


def signature(d, angles=ANGLES):
    """A size-normalised radial profile of the outline.

    For each of `angles` evenly spaced directions, the greatest distance from
    the centroid reached by the outline in that direction, divided by the
    largest such distance overall. Scale therefore cancels out, which is what
    lets a small hexagon and a big one be recognised as the same shape.

    A direction with nothing in it inherits its neighbours' value rather than
    reading 0.0 -- a concave outline genuinely has no material along some
    directions, and treating that as "radius zero" would make a star and a
    pentagon look alike.
    """
    pts = _flatten(d)
    if len(pts) < 3:
        raise ValueError('outline has too few points: %r' % d)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)

    prof = [0.0] * angles
    for x, y in pts:
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        if r <= 0:
            continue
        k = int(round((math.atan2(dy, dx) % (2 * math.pi)) / (2 * math.pi) * angles))
        k %= angles
        if r > prof[k]:
            prof[k] = r

    top = max(prof)
    if top <= 0:
        raise ValueError('degenerate outline: %r' % d)
    prof = [p / top for p in prof]

    # Fill empty directions from the nearest non-empty neighbour.
    if 0.0 in prof:
        for k in range(angles):
            if prof[k] != 0.0:
                continue
            for step in range(1, angles):
                a, b = prof[(k - step) % angles], prof[(k + step) % angles]
                if a or b:
                    prof[k] = a or b
                    break
    return prof


def too_similar(d1, d2, tol=TOL):
    """True when two outlines would read as the same figure on paper."""
    s1, s2 = signature(d1), signature(d2)
    # The signature is a radial profile, so the shape may be presented rotated.
    # Try every rotation offset and accept if ANY alignment matches: a reader
    # will turn the page, and rotating one outline must not hide a collision.
    n = len(s1)
    for off in range(n):
        if all(abs(s1[i] - s2[(i + off) % n]) <= tol for i in range(n)):
            return True
    return False


def compare_all(table):
    """Every colliding pair in a name -> spec dict. Returns [(a, b), ...]."""
    names = list(table)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            try:
                if too_similar(table[a]['out'], table[b]['out']):
                    out.append((a, b))
            except ValueError as e:
                raise ValueError('%s vs %s: %s' % (a, b, e))
    return out


if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import icons
    collisions = compare_all(icons.REGS)
    if collisions:
        print('FAIL: these outlines read as the same shape:')
        for a, b in collisions:
            print('   %s ~ %s' % (a, b))
        sys.exit(1)
    print('ok: %d register outlines, none confusable' % len(icons.REGS))