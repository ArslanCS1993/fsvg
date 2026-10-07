#!/usr/bin/env bash
# Build every program in programs/, verify it, and stage release artefacts in dist/.
#
#   ./build.sh          compile + verify
#   ./build.sh --asm    keep the generated assembly next to the binaries
set -euo pipefail
cd "$(dirname "$0")"

ASMF=0; [[ "${1:-}" == "--asm" ]] && ASMF=1
mkdir -p build dist
fail=0

# The glyph vocabulary comes first: everything below compiles against it, and a
# duplicated emoji or two registers that draw the same outline is a language
# defect, not a program defect. Checked before compiling so the error names the
# vocabulary rather than surfacing as a mysterious wrong instruction.
echo "=== glyphs"
python3 test-icons.py || { echo "FAIL: glyph vocabulary"; fail=1; }

for src in programs/*.fsvg.svg; do
  name=$(basename "$src" .fsvg.svg)
  echo "=== $name"
  # Per-program compiler flags. 03 is kernel entry code: the SYSCALL instruction
  # leaves RFLAGS in R11, so that program owns R11 and the compiler borrows RDI
  # as scratch instead.
  flags=""
  case "$name" in 03-*) flags="--use-r11";; esac
  if ! python3 fsvgc.py "$src" -o "build/$name" $flags $( ((ASMF)) && echo --asm ); then
    echo "FAIL $name: compile"; fail=1; continue
  fi

  # Some programs are kernel code: they assemble and link, but cannot RUN as a
  # userspace process. `swapgs` is privileged and faults immediately in ring 3,
  # so those are checked by comparing against the real instruction trace
  # instead (tools/verify-trace.py). Running them would prove nothing.
  case "$name" in
    03-*)
      if ! python3 tools/verify-trace.py --binary "build/$name"; then
        echo "FAIL $name: does not match the kernel instruction trace"
        fail=1
      fi
      continue;;
  esac

  # The program must print something, and printing must not be the only thing it does.
  if ! ./build/"$name" > "build/$name.svg"; then
    echo "FAIL $name: run"; fail=1; continue
  fi
  [[ -s "build/$name.svg" ]] || { echo "FAIL $name: empty output"; fail=1; continue; }

  # An XML comment may not contain '--'. That bit us twice: "--use-r11" and
  # "not a detail -- a frame". Catch it here rather than as a parse error.
  python3 - "$src" <<'PYEOF' || exit 1
import re, sys, xml.etree.ElementTree as ET
p = sys.argv[1]
ET.parse(p)                       # a hard parse error is a build failure
bad = [b for b in re.findall(r'<!--(.*?)-->', open(p, encoding='utf-8').read(), re.S)
       if '--' in b]
if bad:
    sys.exit("XML comment contains '--' (illegal): %r" % bad[0][:60])
PYEOF

  # It must emit SVG, not just text.
  head -c 512 "build/$name.svg" | grep -q '<svg' || {
    echo "FAIL $name: output is not SVG"; fail=1; continue; }

  echo "  -> $(wc -c < "build/$name.svg") bytes of SVG"
  cp -f "build/$name.svg" "dist/$name.svg"
  cp -f "$src" "dist/$name.fsvg.svg"
done

cp -f fsvgc.py svg2png.py SPEC.md dist/

# The glyph reference page. Generated from icons.py -- the same table the
# compiler resolves against -- so an opcode added to the language cannot fail to
# appear on the page. It also embeds each program's real assembly, which means
# it must be regenerated whenever the programs change; putting it here makes
# that automatic rather than a thing to remember.
#
# Only on a green build. Regenerating it after a failure would publish a page
# built from output that just failed verification -- the failure would be real
# and the page would still be new, which is the worst of both.
if (( fail )); then
  echo
  echo "BUILD FAILED (site not regenerated)"
  exit 1
fi
echo "=== site"
python3 tools/mkdocs.py

# Pages serves the BRANCH ROOT, and it serves only what git TRACKS. Two ways to
# get a 404 that the build cannot otherwise see, both of which happened:
#
#   * index.html sat in site/ -- the build was green, the page rendered locally,
#     and the live site was empty, because the repo root had no index.html.
#   * index.html could be .gitignore'd -- it builds fine on disk and is simply
#     absent from the deployed tree.
#
# So assert what git will actually publish. Note that checking `[[ -f index.html ]]`
# here would be worthless: mkdocs.py has just written it, so it can never be
# missing. A check that cannot fail is not a check.
if ! git ls-files | grep -qx 'index.html'; then
  echo "FAIL: index.html is not tracked by git -- Pages would not serve it"
  exit 1
fi
if git ls-files | grep -qx 'site/index.html'; then
  echo "FAIL: site/index.html is tracked; Pages serves the root, not site/"
  exit 1
fi
echo
echo "OK"