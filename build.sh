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
  if ! python3 fsvgc.py "$src" -o "build/$name" $( ((ASMF)) && echo --asm ); then
    echo "FAIL $name: compile"; fail=1; continue
  fi

  # The program must print something, and printing must not be the only thing it does.
  if ! ./build/"$name" > "build/$name.svg"; then
    echo "FAIL $name: run"; fail=1; continue
  fi
  [[ -s "build/$name.svg" ]] || { echo "FAIL $name: empty output"; fail=1; continue; }

  # It must emit SVG, not just text.
  head -c 512 "build/$name.svg" | grep -q '<svg' || {
    echo "FAIL $name: output is not SVG"; fail=1; continue; }

  echo "  -> $(wc -c < "build/$name.svg") bytes of SVG"
  cp -f "build/$name.svg" "dist/$name.svg"
  cp -f "$src" "dist/$name.fsvg.svg"
done

cp -f fsvgc.py svg2png.py SPEC.md dist/
echo
(( fail )) && { echo "BUILD FAILED"; exit 1; }
echo "OK"