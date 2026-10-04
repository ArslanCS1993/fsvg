# FSVG

A flow diagram language. **A program is a flowchart.** Shapes are statements, edges are control flow, and
the text inside the shape is the code. An SVG file is not a picture here — it is
the source language.

    fsvgc hello.fsvg.svg -o hello    # flowchart  ->  ELF executable
    ./hello > out.svg                # program     ->  SVG

The compiler emits plain x86-64 assembly and links with `as`/`ld`. No runtime, no
dependencies, ~650 lines of Python.

## Why shapes

A box is a straight-line step, a four-point polygon is a test. Execution starts
at `data-entry`; every shape names its successors explicitly, so a box can be
moved anywhere on the canvas without changing the program.

## The number rule

A bare number is only ever a **memory address** (`@0x600000`, `@NAME[idx]`) or a
**size** (`FILL` count, `SHL`/`SHR` amount, `EXIT` status). Everything else gets
a name, and the compiler rejects the rest:

    fsvgc: init_i: bare number '7' in MOV. In this language a number is only a
    memory address (@0x...) or a size (FILL count, SHL/SHR amount, EXIT status).
    Give this one a name: data-const="MY_CONST=7".

This is what keeps a flowchart meaningful — a register can never hold a mystery
literal, so every value in a box is traceable to a name.

## Try it

    ./build.sh            # compile + verify every program
    python3 svg2png.py build/01-hello.svg   # rasterise (ImageMagick cannot)

## Layout

    fsvgc.py        compiler: SVG -> x86-64 assembly + runtime
    svg2png.py      rasteriser for the SVG subset FSVG emits
    programs/       source flowcharts
    build/          binaries and generated assembly (not versioned)
    dist/           release artefacts

See `SPEC.md` for the language.