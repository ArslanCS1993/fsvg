# FSVG — the language where a program *is* a flowchart

A program is one SVG document. The **geometry is the program**: a rectangle is a
statement, a rhombus is a test, and the edges are the control flow.

The rule that shapes everything: **the only numeric literals in a program are
memory addresses and sizes.** Every other value is a register or a named
constant. `MOV RAX, 5` is a compile error — you write `MOV RAX, COUNT`. A
number in the source always means *where in memory* or *how many things*.

## Two ways to write it

A program may hold **letters** or it may hold **no letters at all**.

```
<rect id="a" data-next="b">MOV RAX, @BUF</rect>      words
<rect id="a" data-next="b">🛡 🔺 @BUF</rect>          glyphs, same program
```

The glyph form exists because a program should not require reading to be
readable. Every instruction has an emoji and a hand-drawn SVG icon; both are
required to mean the same thing.

| | word form | glyph form |
|---|---|---|
| resolved by | the parser | an exact emoji codepoint |
| readable by | anyone who knows English | anyone who can see the drawing |
| error on typo | yes | yes — an unknown glyph resolves to nothing |

**Meaning never comes from the picture.** `icons.py` is a dictionary: an emoji is
looked up, not recognised. A glyph is never guessed at from its shape, so the
compiler cannot misread a hand-drawn icon.

## Shapes

| shape | meaning |
|---|---|
| `<rect id="…">` | one statement — or a whole block of them, one per line |
| `<polygon id="…">` (4 points) | one test — a rhombus, exactly one `CMP` |
| `<g data-fn="name">` | a function |

Inside a shape, the **text is the code**. Execution starts at `data-entry="1"`;
every shape names its successors explicitly, so a box can be moved anywhere in
the drawing without changing the program.

```
<rect    data-next="id"/>              one successor
<polygon data-lt="id" data-eq="id" data-gt="id"/>   three, matching CMP
```

### A box may hold a block

One shape is normally one statement, but the kernel's own macros are many
instructions written as one source line — `PUSH_AND_CLEAR_REGS` is 28 of them. A
glyph shape may therefore hold a **block**, one statement per line of its text.
The block still has one successor, exactly like a basic block. Splitting those 28
into 28 boxes would make the drawing lie about the source it came from.

## Statements

| form | meaning |
|---|---|
| `MOV dst, src` | `dst = src`. `dst` may be `@mem`, which makes it a **store** |
| `ADD`/`SUB`/`MUL`/`DIV` `dst, src` | arithmetic |
| `SHL`/`SHR` `dst, n` | shifts (`n` is a size, so a literal is legal) |
| `AND`/`OR`/`XOR` `dst, src` | bitwise |
| `PUSH`/`POP` `src` | stack; x86 has no `PUSH reg,mem` |
| `CMP a, b` | sets flags; only valid in a rhombus |
| `MOVSXD dst, src` | 32→64 sign-extending move (`movslq %eax, %rsi`) |
| `CALL @name` | call a shape in this program, or a symbol in `data-extern` |
| `SWAPGS`/`SYSRET`/`IRET`/`TO_USER` | privileged or terminal |
| `PRINT`/`PUTINT`/`EXIT` | output and exit |
| `LOAD.*`/`STORE.*` | width-suffixed access |

## The number rule, and the one place it bends

A literal is only an address (`@0x…`, `@NAME`) or a size (`FILL`, `SHL`,
`SHR`, `EXIT`). Everywhere else a value must be named:

```
fsvgc: bare number '7' in MOV. In this language a number is only a memory
address (@0x...) or a size (FILL count, SHL/SHR amount, EXIT status).
Give this one a name: data-const="MY_CONST=7".
```

**The glyph form relaxes this, and only this.** An immediate like `push $0x2b`
should be named — but a name is a letter, and that form exists precisely to have
none. So a glyph-form operand may be a bare integer. It is still checked to be a
well-formed integer; only the naming requirement is dropped.

## The kernel check

`tools/verify-trace.py` compares compiled output against a recorded trace of the
real kernel (fixture: `tools/trace-entry_64.json`, regenerable from cpu-3d via
`tools/export-trace.py`). `programs/03-entry-syscall.fsvg.svg` matches
**40 of 40** instructions that both sides record — opcode *and* operands.

Four gaps were open; three were real and one was the checker's own bug:

| gap | resolution |
|---|---|
| `mov %rsp, PER_CPU_VAR(…)` | `MOV` gained a memory **destination**, so it can store |
| `movslq %eax, %rsi` | new `MOVSXD` — same register, different instruction |
| `call do_syscall_64` | `data-extern` emits a real call to the symbol |
| `nopl (%rax,%rax,1)` | **was a false gap** — see below |

### Why the `nopl` was never a gap

The checker filtered `nop` but not `nopl`, so `nopl` survived on one side only
and knocked the two lists one instruction out of step. That reported two gaps
for one cause: the `nopl` itself, and the `call` it displaced. The verdict was
39/41 when the truth was 40/41.

Checking the real `vmlinux` settled what the `nopl` actually is — and corrected
the first guess. It is not one 5-byte `nopl` but **five separate 1-byte `nop`s**,
sitting exactly where `IBRS_ENTER`, `UNTRAIN_RET` and `CLEAR_BRANCH_HISTORY`
(lines 115–117) are:

```
ffffffff8120009f:  48 63 f0          movslq %eax,%rsi
ffffffff812000a2:  90 90 90 90 90    five nops (feature macros, off here)
ffffffff812000a7:  e8 b3 69 f5 ff    call do_syscall_64
```

They are the residue of CPU-feature macros that compile to nothing on this
machine — patch sites, not instructions the source asks for. Both sides are now
filtered identically, and the checker prints the raw count alongside the
filtered one so the exclusion is visible rather than silent.

### What the trace check does *not* prove

A matching `call` proves the right **symbol** is called. It cannot prove the
callee behaves correctly, because the callee is in another file that has not been
translated. `data-extern` emits the call against a stub so the name survives
linking and appears in the disassembly; the stub is never executed, and this
program cannot run at all — `swapgs` faults in ring 3.

## Registers

R11 is compiler scratch, because indexed addressing needs it — x86 forbids a
RIP-relative operand *with* an index register, so a load must compute its address
somewhere first. Kernel code is the exception: the `SYSCALL` instruction leaves
RFLAGS in R11, so `--use-r11` hands R11 to the program and the compiler borrows
RDI as scratch instead.

## Try it

    ./build.sh                       # compile + verify every program
    python3 test-icons.py            # glyph vocabulary invariants
    python3 tools/verify-trace.py    # compare against the real kernel trace
    python3 tools/shapes.py          # can two register outlines be told apart?

`build.sh` is the only entry point needed: it runs the glyph checks, compiles
every program, verifies the kernel program against the trace, and regenerates the
reference page. It fails rather than shipping a page built from output that did
not verify.