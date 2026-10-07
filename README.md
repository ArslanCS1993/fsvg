# FSVG

A flow diagram language. **A program is a flowchart.** Shapes are statements, edges are control flow, and
the text inside the shape is the code. An SVG file is not a picture here — it is
the source language.

    fsvgc hello.fsvg.svg -o hello    # flowchart  ->  ELF executable
    ./hello > out.svg                # program     ->  SVG

The compiler emits plain x86-64 assembly and links with `as`/`ld`. No external
dependencies: ~950 lines of Python, one optional rasteriser.

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

## No letters at all

A program may be written with words, or with **no letters whatsoever**. Every
instruction has an emoji and a hand-drawn icon, and the emoji is an exact
codepoint looked up in a table — never guessed from how the drawing looks, so a
hand-edited icon cannot silently compile to the wrong instruction.

    <rect id="a" data-next="b">MOV RAX, @BUF</rect>     words
    <rect id="a" data-next="b">🛡 🔺 @BUF</rect>         glyphs, same program

[**The glyph reference**](https://arslancs1993.github.io/fsvg/) lists every
instruction and register, and is generated from the compiler's own table so it
cannot drift from the language.

## It compiles real kernel code

`programs/03-entry-syscall.fsvg.svg` is `entry_SYSCALL_64` — the first
instructions a Linux kernel runs on a syscall, written as a glyph flowchart with
no letters in it. Real `swapgs`, real `push`, real `sysret`.

`build.sh` checks it against a recorded trace of the real kernel and **matches 40
of 40 instructions exactly**, opcode and operands. It cannot be run — `swapgs`
faults in ring 3 — so the trace is the only honest evidence available, and the
build fails if the match regresses.

Four gaps were open when this started. Three were real (`MOV` could not store,
no 32→64 sign-extending move, no external call). The fourth was the checker's own
bug: it filtered `nop` but not `nopl`, which put the two instruction lists out of
step and reported two gaps for one cause. Both sides are filtered identically
now, and the verifier prints the raw count beside the filtered one so no
exclusion is silent.

## Try it

    ./build.sh                       # compile, verify, regenerate the page
    python3 test-icons.py            # glyph vocabulary invariants
    python3 tools/verify-trace.py    # compare against the kernel trace
    python3 tools/shapes.py          # can two register outlines be told apart?
    python3 svg2png.py build/01-hello.svg   # rasterise (ImageMagick cannot)

## Layout

    fsvgc.py          compiler: SVG -> x86-64 assembly + runtime
    icons.py          the glyph vocabulary (the language's dictionary)
    svg2png.py        rasteriser for the SVG subset FSVG emits
    programs/         source flowcharts
    tools/            verifier, shape distinctness, page generator
    build/            binaries and generated assembly (not versioned)
    dist/             release artefacts
    index.html        the glyph reference page (generated; repo root is the Pages root)

See `SPEC.md` for the language.