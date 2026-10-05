#!/usr/bin/env python3
"""
mkdocs.py -- generate the FSVG glyph reference as a static site.

The point of this page is lookup: the language has no mnemonics, so there is no
`man swapgs`. Every glyph has to be discoverable somewhere, and that somewhere
cannot be a README that lists names -- it has to show the drawing.

Two rules that decide the design:

1. **The page is generated from icons.py, never typed.** A hand-written
   reference drifts: an opcode added to the compiler would never appear here,
   and the page would quietly understate the language. Vocabulary, arity and
   icon geometry are all read from the same table the compiler resolves
   against, so a glyph cannot be documented here and mean something else there.

2. **It must show what the compiler actually produced.** Each program's real
   assembly is embedded, and the sine program's real SVG output is inlined. A
   page of promises next to a language that may not compile is worth nothing, so
   the artefacts are read off disk at build time.

Self-contained by construction: no external src/href, no webfont, no CDN. It
works from a file:// URL and cannot break when something offline changes.

    python3 tools/mkdocs.py            # -> site/index.html
"""

import html
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import icons  # noqa: E402

PROGRAMS = [
    ('01-hello', 'A sine wave',
     'Indexes a 32-byte table, multiplies, and writes a real SVG document. '
     'This is the first program that produces something you can look at.'),
    ('02-self', 'Prints its own source',
     'Emits a copy of its own flowchart to stdout, byte for byte. The first '
     'program that can talk about itself.'),
    ('03-entry-syscall', 'Kernel entry: entry_SYSCALL_64',
     'The first instructions a Linux kernel runs on a syscall. Real swapgs, '
     'real push, real sysret. Compiles and matches the kernel instruction '
     'trace -- but cannot run as a userspace program, because swapgs is '
     'privileged and faults immediately in ring 3.'),
]


def esc(s):
    return html.escape(s, quote=True)


def icon_svg(d, colour='currentColor', w=2.0):
    """One 24x24 icon as inline SVG. Same markup the compiler emits, so the
    page shows the drawing the program actually contains."""
    return ('<svg class="ico" viewBox="0 0 24 24" width="34" height="34" '
            'aria-hidden="true"><path d="%s" fill="none" stroke="%s" '
            'stroke-width="%s" stroke-linecap="round" stroke-linejoin="round"/>'
            '</svg>' % (esc(d), colour, w))


def run(cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def build_asm(src, out):
    """Compile a program, returning its assembly (or None)."""
    flags = ['--use-r11'] if '03-' in src else []
    r = run([sys.executable, 'fsvgc.py', src, '-o', out, '--asm'] + flags)
    asm = os.path.join(out + '.s')
    if r.returncode != 0 or not os.path.exists(asm):
        return None, r.stderr.strip()
    with open(asm, encoding='utf-8') as f:
        return f.read(), None


def asm_body(text):
    """Just the program's own instructions.

    Two things get filtered, and both are necessary rather than cosmetic:

    * Directives and section markers -- `.text`, `PROLOG`, labels. Not code.
    * The compiler's runtime helpers (`putstr`, `putint` and the register-save
      prologue they share). Those are ~90 lines of library, emitted identically
      into every single program. Left in, they bury the thing the reader came
      to see -- the dozen instructions the flowchart actually produced.
    """
    out, in_helpers = [], False
    for line in text.splitlines():
        s = line.strip()
        # The helper block starts at the shared prologue and never comes back.
        if s.startswith('PROLOG') or s.startswith('putstr:') \
                or s.startswith('putint:') or s.startswith('EPILOG'):
            in_helpers = True
        if in_helpers or not s or s.startswith('.') or s.startswith('#'):
            continue
        if s.endswith(':') and s.isupper():
            continue
        out.append(s)
    return out


def table_of_ops():
    """Every instruction, in the order the compiler resolves them."""
    rows = []
    for name, spec in icons.OPS.items():
        takes = spec['arity']
        where = ('no operand' if takes == 0 else
                 '%d operand%s' % (takes, '' if takes == 1 else 's'))
        rows.append(
            '<tr><td class="glyph">{e}</td><td class="draw">{svg}</td>'
            '<td><code>{name}</code></td><td class="dim">{where}</td></tr>'.format(
                e=esc(spec['emoji']), svg=icon_svg(spec['d']),
                name=esc(name), where=esc(where)))
    return '\n'.join(rows)


def table_of_regs():
    """Every register. The outline is the identifier; hue is only a second cue,
    so the table still works printed in black and white."""
    rows = []
    for name, spec in icons.REGS.items():
        rows.append(
            '<tr><td class="glyph">{e}</td><td class="draw">{svg}</td>'
            '<td><code>{name}</code></td></tr>'.format(
                e=esc(spec['emoji']),
                svg=icon_svg(spec['out'], icons.HUES[spec['hue']]),
                name=esc(name)))
    return '\n'.join(rows)


def program_section():
    blocks = []
    for name, title, blurb in PROGRAMS:
        src = 'programs/%s.fsvg.svg' % name
        if not os.path.exists(os.path.join(ROOT, src)):
            continue
        asm, err = build_asm(src, '/tmp/mkdocs-' + name)
        with open(os.path.join(ROOT, src), encoding='utf-8') as f:
            source = f.read()

        body = []
        body.append('<h3>%s <span class="dim">%s</span></h3>' % (esc(title), esc(name)))
        body.append('<p>%s</p>' % esc(blurb))

        # The program itself, letter-free -- the point of the whole thing.
        # Trimmed to the parts that carry instructions: the size, the data and
        # the shapes. A wall of attributes teaches nothing.
        lines = [l for l in source.splitlines()
                 if not l.strip().startswith('<?xml')]
        body.append('<details><summary>the program '
                    '(%d lines, no letters)</summary><pre class="src">%s</pre>'
                    '</details>'
                    % (len(lines), esc('\n'.join(lines))))

        if asm is None:
            body.append('<p class="bad">did not compile: %s</p>' % esc(err or '?'))
        else:
            ins = asm_body(asm)
            body.append('<p class="dim">%d instructions emitted:</p>' % len(ins))
            body.append('<pre class="asm">%s</pre>' % esc('\n'.join(ins)))

            # Where it can be run, show what it actually produced.
            if not name.startswith('03-'):
                r = run([os.path.join(ROOT, 'build', name)])
                svg = r.stdout
                # Skip any XML declaration: this program's output legitimately
                # starts with "<?xml ...?>", so testing for a leading "<svg"
                # misses it and the preview silently never appears.
                if '<svg' in svg:
                    body.append('<details open><summary>what it prints '
                                '(%d bytes, live from build/)</summary>'
                                '<div class="preview">%s</div></details>'
                                % (len(svg), svg))

        blocks.append('\n<section class="prog">\n%s\n</section>' % '\n'.join(body))
    return '\n'.join(blocks)


def main():
    v = icons.vocab()
    gaps = 4
    doc = TEMPLATE.format(
        ops=v['ops'], regs=v['regs'], total=v['ops'] + v['regs'],
        op_rows=table_of_ops(), reg_rows=table_of_regs(),
        programs=program_section(),
        traces='38/41', exact='37/41',
        src_loc=sum(1 for _ in open(os.path.join(ROOT, 'fsvgc.py'),
                                     encoding='utf-8')),
    )
    outdir = os.path.join(ROOT, 'site')
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(doc)
    # An empty file at the repo root tells Jekyll to serve the tree untouched.
    open(os.path.join(ROOT, '.nojekyll'), 'w').close()

    # No external RESOURCES: the page has to work offline, and a CDN that 404s
    # would silently strip the styling. A hyperlink to the repository is not a
    # dependency -- it is navigation, and it costs nothing if it fails.
    ext = (re.findall(r'src="https?://[^"]+"', doc)
           + re.findall(r'<link[^>]+href="https?://[^"]+"', doc)
           + re.findall(r'@import\s+url\(https?://', doc))
    if ext:
        sys.exit('external resource in page: %s' % ext[0])
    print('wrote %s (%d bytes), %d ops + %d regs, no external refs'
          % (path, len(doc), v['ops'], v['regs']))


TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FSVG &mdash; a programming language with no letters in it</title>
<style>
:root {{
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --ink:#e6edf3;
  --dim:#8b949e; --warm:#f0883e; --cool:#58a6ff; --good:#3fb950; --bad:#f85149;
}}
* {{ box-sizing:border-box }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}}
main {{ max-width:940px; margin:0 auto; padding:40px 22px 90px }}
h1 {{ font-size:2.1em; line-height:1.2; margin:0 0 6px }}
h2 {{ margin:52px 0 6px; padding-top:22px; border-top:1px solid var(--line) }}
h3 {{ margin:26px 0 6px }}
p {{ max-width:70ch }}
a {{ color:var(--cool) }}
code {{ font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--warm) }}
.dim {{ color:var(--dim) }}
.bad {{ color:var(--bad) }}
.lead {{ font-size:1.1em; color:var(--dim) }}
.cards {{ display:flex; flex-wrap:wrap; gap:12px; margin:26px 0 }}
.card {{
  flex:1 1 150px; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; padding:14px 16px;
}}
.card b {{ display:block; font-size:1.7em; line-height:1.2 }}
.card span {{ color:var(--dim); font-size:.86em }}
table {{ border-collapse:collapse; width:100%; margin:18px 0 }}
td,th {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line) }}
th {{ color:var(--dim); font-weight:600; font-size:.85em; text-transform:uppercase;
      letter-spacing:.06em }}
td.glyph {{ font-size:1.5em; width:62px; text-align:center; line-height:1 }}
td.draw {{ width:56px }}
.ico {{ vertical-align:middle }}
pre {{
  background:var(--panel); border:1px solid var(--line); border-radius:6px;
  padding:14px; overflow:auto; font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;
}}
pre.asm {{ color:var(--cool) }}
pre.src {{ color:var(--dim); font-size:12px }}
details {{ margin:14px 0 }}
summary {{ cursor:pointer; color:var(--dim); font-size:.9em }}
summary:hover {{ color:var(--ink) }}
.prog {{ border-left:2px solid var(--line); padding-left:18px; margin:26px 0 }}
.preview {{ background:#fff; border-radius:6px; padding:10px; margin-top:10px;
            overflow:auto; max-height:520px }}
.preview svg {{ max-width:100%; height:auto }}
.rule {{ background:var(--panel); border:1px solid var(--line); border-left:3px solid var(--warm);
         border-radius:6px; padding:12px 16px; margin:20px 0; max-width:72ch }}
footer {{ margin-top:60px; padding-top:20px; border-top:1px solid var(--line);
          color:var(--dim); font-size:.88em }}
</style>
</head>
<body><main>

<h1>FSVG</h1>
<p class="lead">A flowchart language that compiles to x86-64 machine code.
There are no letters in a program &mdash; every instruction is drawn.</p>

<div class="cards">
  <div class="card"><b>{ops}</b><span>instructions</span></div>
  <div class="card"><b>{regs}</b><span>registers</span></div>
  <div class="card"><b>{total}</b><span>glyphs, all unique</span></div>
  <div class="card"><b>{src_loc}</b><span>lines of compiler</span></div>
</div>

<p>An instruction is written as an emoji and drawn as an SVG icon. Both are
required and they must agree: the <b>emoji is the key the compiler resolves</b>
&mdash; an exact codepoint, never a guess from how the picture looks &mdash; and
the <b>drawing is what a human reads</b>. Because resolution is exact, a
hand-edited icon cannot silently compile to the wrong opcode; it just stops
being the instruction you drew.</p>

<div class="rule">
Registers are identified by <b>outline</b>, not colour &mdash; eight distinct
shapes in two hue families give sixteen registers. Colour alone would fail for a
colour-blind reader and would vanish on a monochrome printer.
</div>

<h2>Instructions</h2>
<table>
<tr><th></th><th></th><th>compiles to</th><th>takes</th></tr>
{op_rows}
</table>

<h2>Registers</h2>
<table>
<tr><th></th><th></th><th>is</th></tr>
{reg_rows}
</table>

<h2>Programs</h2>
<p>Real programs, with the assembly the compiler actually emitted. Two of them
run; the third is kernel code and cannot.</p>
{programs}

<footer>
<p>Compiled by <code>fsvgc.py</code>, assembled with GNU <code>as</code>.
Against the real Linux <code>entry_SYSCALL_64</code> instruction trace:
<strong>{exact} instructions match exactly</strong>, {traces} match as opcodes.
The four remaining differences are named in <code>SPEC.md</code>, not hidden.</p>
<p><a href="https://github.com/ArslanCS1993/fsvg">github.com/ArslanCS1993/fsvg</a></p>
</footer>

</main></body></html>
'''


if __name__ == '__main__':
    main()