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
  case "$name" in 03-*) continue;; esac

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
echo "=== site"
python3 tools/mkdocs.py

echo
(( fail )) && { echo "BUILD FAILED"; exit 1; }
echo "OK"