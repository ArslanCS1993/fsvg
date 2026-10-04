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
| `MOV dst, src` | `dst = src` (register, constant, or `@mem`) |
| `ADD`/`SUB`/`MUL`/`DIV` `dst, src` | arithmetic |
| `SHL`/`SHR` `dst, n` | shifts (`n` is a size, so a literal is legal) |
| `AND`/`OR`/`XOR` `dst, src` | bitwise |
| `PUSH`/`POP` `src` | stack; x86 has no `PUSH reg,mem` |
| `CMP a, b` | sets flags; only valid in a rhombus |
| `CALL @shape` | call a shape |
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

## Known gaps against the real kernel

`tools/verify-trace.py` compares compiled output against a trace of the real
kernel. `programs/03-entry-syscall.fsvg.svg` currently matches **37 of 41**
instructions exactly. The four that do not are all known:

| gap | why |
|---|---|
| `mov %rsp, PER_CPU_VAR(…)` | `MOV` writes only registers; the kernel stores to memory too |
| `movslq %eax, %rsi` | no 32→64 sign-extending move; registers have one width |
| `nopl (%rax,%rax,1)` | alignment padding inserted by `as`, not by us |
| `call do_syscall_64` | the callee is emitted inline; it needs `data-fn` grouping |

## Registers

R11 is compiler scratch, because indexed addressing needs it — x86 forbids a
RIP-relative operand *with* an index register, so a load must compute its address
somewhere first. Kernel code is the exception: the `SYSCALL` instruction leaves
RFLAGS in R11, so `--use-r11` hands R11 to the program and the compiler borrows
RDI as scratch instead.

## Try it

    ./build.sh            # compile + verify every program
    python3 test-icons.py # glyph vocabulary invariants
    python3 tools/verify-trace.py   # compare against the real kernel trace