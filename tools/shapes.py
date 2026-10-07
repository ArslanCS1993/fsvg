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
            # Cubic Bezier, sampled. The instruction icons use curves (the call
            # handset, the sysret rocket) while the register outlines are
            # polygons, so without this the op icons simply could not be
            # compared -- and refusing was the right default until something
            # needed them. Sampled rather than refused now because the check
            # "does movsxd's icon read as mov's?" has to be answerable.
            #
            # One pair of control points per command; the smooth variants (S/T)
            # and quadratics (Q) are not used and still raise below, so this
            # cannot silently mis-sample a curve shape it was not written for.
            if cmd not in 'Cc':
                raise ValueError('command %r not sampled (%r)' % (cmd, d))
            p0 = cur
            x1, y1, x2, y2, x, y = (num() for _ in range(6))
            if cmd == 'c':
                x1, y1 = p0[0] + x1, p0[1] + y1
                x2, y2 = p0[0] + x2, p0[1] + y2
                x, y = p0[0] + x, p0[1] + y
            for k in range(1, STEPS_PER_COMMAND + 1):
                t = k / float(STEPS_PER_COMMAND)
                u = 1.0 - t
                pts.append((u*u*u*p0[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                            u*u*u*p0[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y))
            cur = (x, y)
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


def compare_all(table, key='out'):
    """Every colliding pair in a name -> spec dict. Returns [(a, b), ...].

    Only meaningful for CLOSED outlines. The metric asks "would these two
    silhouettes read as the same figure", which a closed shape has an answer to
    and an open stroke does not: `nop`'s icon is a single straight line, so it
    has two points and no silhouette at all. Callers comparing open drawings
    want `identical_or_nested` instead -- forcing the radial profile onto a line
    would report two different lines as identical, which is worse than not
    checking.
    """
    names = list(table)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if not _closed(table[a][key]):
                raise ValueError(
                    '%s is an open path, so its silhouette is undefined; '
                    'compare_all is for closed outlines only' % a)
            try:
                if too_similar(table[a][key], table[b][key]):
                    out.append((a, b))
            except ValueError as e:
                raise ValueError('%s vs %s: %s' % (a, b, e))
    return out


def _closed(d):
    """Does this path contain a closed contour at all?

    Deliberately not "does it end with z". A register outline may close its main
    figure and then add an open detail stroke -- rdx is a closed diamond with a
    cross drawn across it, and its path ends `...v8`, not `z`. Testing the last
    command called rdx open and refused to compare the register table, which is
    exactly backwards: rdx is the shape the cross keeps out of r15's diamond.
    """
    return bool(re.search(r'[zZ]', d))


def identical_or_nested(table, key):
    """Copy-paste and lazy-variation check, valid for ANY path.

    Weaker than the silhouette test but it applies to open strokes too, and it
    catches the two failures that actually happen: an icon duplicated outright
    (r8 and r13 were the same cross) and one icon being a prefix of another (the
    "filled-in twin"). It makes no claim about depiction -- two arrows pointing
    opposite ways are correctly left alone here.
    """
    def norm(d):
        return re.sub(r'\s+', '', d)

    out = []
    names = list(table)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            da, db = norm(table[a][key]), norm(table[b][key])
            if da == db:
                out.append(('SAME PATH', a, b))
                continue
            s, l = (da, db) if len(da) < len(db) else (db, da)
            n = (a, b) if len(da) < len(db) else (b, a)
            if s in l:
                out.append(('NESTED', n[0], n[1]))
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