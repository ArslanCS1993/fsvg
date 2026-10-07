#!/usr/bin/env python3
"""fsvgc - compile an FSVG program (an SVG flowchart) into a Linux x86-64 binary.

The source SVG *is* the program: a <rect> is a statement, a 4-point <polygon>
is a test, and the edges are control flow. This turns that into AT&T assembly
and links an ELF, so the result is an ordinary Linux process - no interpreter,
nothing to install.

One rule is enforced rather than merely documented: **the only numeric literals
in a program are memory addresses and sizes.** `MOV RAX, 5` is rejected; you
must write `MOV RAX, COUNT`. A bare integer is legal only after `@` (an
address) or in a size slot (FILL count, shift amount, EXIT status).

    fsvgc prog.fsvg.svg -o prog            # -> ELF
    ./prog > out.svg                       # the program writes SVG to stdout
    fsvgc prog.fsvg.svg --asm -o prog.s    # keep the assembly too
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icons  # the glyph vocabulary: no letters live in a program

SVG = "{http://www.w3.org/2000/svg}"
# R11 is compiler scratch for ordinary programs: indexed addressing needs it
# (x86 forbids RIP-relative WITH an index register), and a language that hands
# every GPR to the user has nowhere else to put a base address.
#
# Kernel code is the exception, and it is a real one. `entry_SYSCALL_64` pushes
# R11 because the SYSCALL instruction put RFLAGS there -- the CPU chose that
# register, not us. So R11 is available on request via --use-r11, and the shapes
# that need a scratch register then borrow RDI instead. A program that both
# passes --use-r11 and keeps a live value in RDI is told so rather than
# silently miscompiled.
SCRATCH = "r11"
USE_R11 = False   # set by --use-r11 for kernel code that owns the register


def scratch():
    """The register the compiler borrows for immediates and absolute addresses.

    R11 by default. When the program owns R11 (kernel entry paths), RDI is
    borrowed instead so a program may keep a live value in R11.
    """
    return "rdi" if USE_R11 else "r11"
REGS = ["RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP"] + \
       [f"R{i}" for i in range(8, 16) if i != 11]
RESERVED = {"R11"}          # yielded when --use-r11 is given
ALT_SCRATCH = "RDI"         # what the compiler borrows instead

# ops whose operand is a SIZE, where a bare integer is therefore legal
SIZE_SLOTS = {"FILL": 2, "SHL": 1, "SHR": 1, "EXIT": 0}
ARITY = {"MOV": 2, "ADD": 2, "SUB": 2, "AND": 2, "OR": 2, "XOR": 2,
         "MUL": 2, "DIV": 2,
         "SHL": 2, "SHR": 2, "FILL": 3, "PRINT": 1, "PUTINT": 1, "EXIT": 1,
         "LOAD.B": 2, "LOAD.SB": 2, "LOAD.W": 2, "LOAD.D": 2, "LOAD.Q": 2,
         "STORE.B": 2, "STORE.W": 2, "STORE.D": 2, "STORE.Q": 2,
         # kernel-facing ops. These assemble and link like any other, but a
         # privileged one (SWAPGS) faults in ring 3 -- that is a property of
         # the CPU, not of the compiler.
         "PUSH": 1, "POP": 1, "SWAPGS": 0, "SYSRET": 0, "IRET": 0,
         "CALL": 1, "CMP": 2, "NOP": 0, "TO_USER": 0,
         # Sign-extend a 32-bit view into a 64-bit register. Not sugar for MOV:
         # `movslq %eax, %rsi` is what the kernel writes where the syscall number
         # (a signed int in the ABI) becomes a 64-bit argument. Without it the
         # only available form is `mov %rax,%rsi` -- same register, same value,
         # DIFFERENT INSTRUCTION, and the trace says so.
         "MOVSXD": 2}

# ops whose FIRST operand is a destination register (STORE/FILL take memory
# first, PRINT/PUTINT/EXIT take a name or a value)
REGDST = {"MOV", "ADD", "SUB", "AND", "OR", "XOR", "MUL", "DIV", "SHL", "SHR",
          "MOVSXD"} | \
         {"LOAD." + x for x in ("B", "SB", "W", "D", "Q")}


class CompileError(Exception):
    pass


# --------------------------------------------------------------- syscall shim
# write(1) and exit(60) only. The helpers touch caller-saved registers alone,
# so a value held in RBX/RBP/R12-R15 survives a PRINT - the same promise C makes.
RUNTIME = r"""
.text
.globl _start

# BOTH helpers are fully register-neutral: they change NOTHING the caller can
# see. That is not politeness, it is necessity with a sharp edge - on Linux
# x86-64 the `syscall` instruction itself destroys RCX (it receives the return
# address) and R11 (it receives RFLAGS). A loop counter left in RCX across a
# PUTINT came back as 4198704, a code address, and the loop ran exactly once.
# A language whose whole point is "the registers ARE the program" cannot leave
# that as a rule the user has to remember, so the whole file is saved here.
.macro PROLOG
	pushq	%rax
	pushq	%rcx
	pushq	%rdx
	pushq	%rsi
	pushq	%rdi
	pushq	%r8
	pushq	%r9
	pushq	%r10
	pushq	%r11
.endm
.macro EPILOG
	popq	%r11
	popq	%r10
	popq	%r9
	popq	%r8
	popq	%rdi
	popq	%rsi
	popq	%rdx
	popq	%rcx
	popq	%rax
.endm

putstr:                              # rdi = NUL-terminated string
	PROLOG
	movq	%rdi, %rsi
	movq	%rdi, %rdx
	xorl	%ecx, %ecx
1:	cmpb	$0, (%rdx)
	je	2f
	incq	%rdx
	incq	%rcx
	jmp	1b
2:	movq	%rcx, %rdx		# the COUNT, not the end pointer
	movl	$1, %edi                  # fd 1 = stdout
	movl	$1, %eax                  # SYS_write
	syscall
	EPILOG
	ret

putint:                              # rdi = signed value -> decimal
	PROLOG
	movq	%rdi, %rax
	leaq	digbuf+24(%rip), %rdi     # rdi = one past the last byte
	xorl	%ecx, %ecx
	testq	%rax, %rax
	jns	1f
	movb	$45, -1(%rdi)              # '-'
	decq	%rdi
	incq	%rcx
	negq	%rax
1:	movl	$10, %r9d
2:	xorl	%edx, %edx
	divq	%r9                       # rax = /10, rdx = %10
	addl	$48, %edx
	movb	%dl, -1(%rdi)
	decq	%rdi
	incq	%rcx
	testq	%rax, %rax
	jne	2b
	movq	%rdi, %rsi
	movq	%rcx, %rdx
	movl	$1, %edi
	movl	$1, %eax
	syscall
	EPILOG
	ret

.section .bss
.p2align 3
digbuf:	.skip 24
"""


# ----------------------------------------------------------------- the program

class Shape:
    __slots__ = ("id", "kind", "text", "a", "fn", "op", "el", "block")

    def __init__(self, sid, kind, text, attrs, fn, el=None):
        self.id, self.kind, self.text, self.a, self.fn = sid, kind, text, attrs, fn
        self.op = None
        # kept so the glyph reader can walk a shape's nested icon/text nodes
        self.el = el
        self.block = None   # set when this shape is a block of glyph statements

    def successors(self):
        a = self.a
        if self.kind == "test":
            return [(k, a.get("data-" + k)) for k in ("lt", "eq", "gt")]
        return [("next", a.get("data-next"))]


def shape_text(el):
    """The shape's own text. SVG source is written across lines, so join it."""
    parts = (el.text or "").split()
    for child in el:
        parts += (child.tail or "").split()
    return " ".join(parts).strip()


def parse_pairs(s, what, sep=","):
    """`NAME=VALUE` items separated by `sep`.

    Strings need a different separator from constants: an SVG path is full of
    commas (`d='M30,240,`) and `data-str="HEAD=...,x,y"` split on commas
    produced `expected NAME=VALUE, got '240'`.
    """
    out = {}
    for item in (s or "").split(sep):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise CompileError("data-%s: expected NAME=VALUE, got %r" % (what, item))
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load(path):
    root = ET.parse(path).getroot()
    consts = parse_pairs(root.get("data-const"), "const")
    bss = parse_pairs(root.get("data-bss"), "bss")
    strs = parse_pairs(root.get("data-str"), "str", sep=";")

    # data-extern="do_syscall_64" - symbols this program CALLs but does not
    # define, because they live in another kernel file. Declaring them is what
    # lets CALL tell an external function from a typo'd shape id: without the
    # declaration an unknown name would silently become an undefined symbol.
    externs = set(x.strip() for x in (root.get("data-extern") or "").split(",")
                  if x.strip())

    # data-bytes="NAME=1,2,3" - a table. Parsed as a list, not the flat string
    # parse_pairs returns, because a byte table's VALUE is a comma list and the
    # two would otherwise collide.
    byte_tables = {}
    raw = root.get("data-bytes") or ""
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise CompileError('data-bytes: expected NAME=v,v,v, got %r' % item)
        name, vals = item.split("=", 1)
        nums = []
        for v in vals.split(","):
            v = v.strip()
            if not v:
                continue
            try:
                x = int(v, 0)
            except ValueError:
                raise CompileError('data-bytes %s: %r is not a number' % (name, v))
            # -128..255: a table is allowed to hold signed bytes (a waveform
            # needs them), and `.byte -26` assembles to 0xE6. Read those back
            # with LOAD.SB, which sign-extends, exactly like the hardware.
            if not -128 <= x <= 255:
                raise CompileError('data-bytes %s: %d does not fit in a byte'
                                   % (name, x))
            nums.append(x)
        if not nums:
            raise CompileError("data-bytes %s: no values" % name)
        byte_tables[name.strip()] = nums

    # a shape inherits the data-fn of its nearest <g> ancestor
    parent = {}
    for p in root.iter():
        for c in p:
            parent[c] = p

    def fn_of(el):
        p = parent.get(el)
        while p is not None:
            if p.get("data-fn"):
                return p.get("data-fn")
            p = parent.get(p)
        return None

    shapes, order = {}, []
    for el in root.iter():
        if el.tag == SVG + "rect":
            kind = "stmt"
        elif el.tag == SVG + "polygon":
            kind = "test"
        else:
            continue
        sid = el.get("id")
        if not sid:
            raise CompileError("every shape needs an id (one <%s> has none)"
                               % el.tag.replace(SVG, ""))
        if sid in shapes:
            raise CompileError("duplicate id %r" % sid)
        pts = el.get("points", "")
        if kind == "test" and len([p for p in pts.replace(",", " ").split() if p]) != 8:
            raise CompileError("%s: a test must be a rhombus - 4 points "
                               "(got %d numbers in points)" % (sid, len(pts.split())))
        shapes[sid] = Shape(sid, kind, shape_text(el), dict(el.attrib), fn_of(el), el)
        order.append(sid)

    entry = [s for s in order if shapes[s].a.get("data-entry") == "1"]
    if len(entry) != 1:
        raise CompileError('need exactly one shape with data-entry="1", found %d'
                           % len(entry))

    for s in shapes.values():
        for name, tgt in s.successors():
            if not tgt:
                raise CompileError("%s (%s): missing successor - every %s needs %s"
                                   % (s.id, s.text[:24] or "empty", s.kind,
                                      "data-lt/data-eq/data-gt" if s.kind == "test"
                                      else "data-next"))
            if tgt not in shapes:
                raise CompileError("%s: successor %r does not exist" % (s.id, tgt))
    return consts, bss, strs, byte_tables, shapes, order, entry[0], externs


# ------------------------------------------------------------ operand checking

def is_int(t):
    return bool(re.fullmatch(r"[0-9]+", t))


def check_number(token, size_slot, sid, op):
    if is_int(token) and size_slot is None:
        hint = token.upper() if not token.startswith("0") else "VALUE"
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", hint):
            hint = "MY_CONST"
        raise CompileError(
            "%s: bare number %r in %s. In this language a number is only a "
            "memory address (@0x...) or a size (FILL count, SHL/SHR amount, "
            "EXIT status). Give this one a name: data-const=\"%s=%s\"."
            % (sid, token, op, hint, token))


def reg_of(t, sid):
    if t.upper() not in REGS:
        raise CompileError("%s: %r is not a register. Have: %s"
                           % (sid, t, " ".join(REGS)))
    return t.lower()


# The 32-bit view of each classic register. r8-r15 follow a rule instead of a
# table (r8 -> r8d), so both are handled here rather than duplicated.
_REG32 = {"rax": "eax", "rbx": "ebx", "rcx": "ecx", "rdx": "edx",
          "rsi": "esi", "rdi": "edi", "rbp": "ebp", "rsp": "esp"}


def reg32(r, sid="?"):
    """64-bit register name -> its 32-bit view. `movslq` needs %eax, not %rax."""
    if r in _REG32:
        return _REG32[r]
    if re.fullmatch(r"r(?:[89]|1[0-5])", r):
        return r + "d"
    raise CompileError("%s: %r has no 32-bit view to sign-extend from" % (sid, r))


def value_of(t, consts, sid, addr_ok=False, allow_bare=False):
    """Return ('reg', name) or ('imm', int) for a plain value operand."""
    if t.upper() in REGS:
        return ("reg", t.lower())
    if t in consts:
        return ("imm", int(consts[t], 0))
    if addr_ok and t.lower().startswith("0x"):
        return ("imm", int(t, 16))
    if is_int(t):
        if allow_bare:
            return ("imm", int(t, 10))
        raise CompileError("%s: bare number %r - give it a name in data-const"
                           % (sid, t))
    raise CompileError("%s: %r is not a register, a data-const, or a 0x address"
                       % (sid, t))


def shapes_of(s, env):
    """Every shape id in the program, for validating CALL targets."""
    return env.shape_ids


class Env:
    """The program's symbol tables: named constants, buffers, byte tables."""

    def __init__(self, consts, bss, bytes_, shape_ids=(), externs=()):
        self.consts, self.bss, self.bytes = consts, bss, bytes_
        # shape ids, so CALL can check its target names a real shape
        self.shape_ids = set(shape_ids)
        # symbols defined in another kernel file, so CALL can emit a real
        # external call instead of refusing the name
        self.externs = set(externs)


def mem_of(t, env, sid):
    """@0x404000 | @NAME | @NAME[idx] | @NAME[idx*4] -> (kind, ref, idx, scale).

    Indexed addressing is not optional: without it there are no arrays, and
    therefore no tables, no framebuffers and no strings you can walk. The index
    is a REGISTER, so the hardware computes the offset rather than a separate
    add - and the scale is checked against what x86 actually supports.
    """
    m = re.fullmatch(r"@(\w+)(?:\[(\w+)(?:\*(\d+))?\])?", t)
    if not m:
        raise CompileError("%s: %r is not a memory reference. Write @0x404000, "
                           "@NAME, @NAME[idx] or @NAME[idx*4]." % (sid, t))
    ref, idx, scale = m.group(1), m.group(2), m.group(3)
    if idx is not None:
        if idx.upper() not in REGS:
            raise CompileError("%s: %r is not a register, so it cannot index memory"
                               % (sid, idx))
        scale = int(scale) if scale else 1
        if scale not in (1, 2, 4, 8):
            raise CompileError("%s: index scale must be 1, 2, 4 or 8 (got %s)"
                               % (sid, scale))
        idx = idx.lower()
    if ref in env.bss:
        return ("bss", ref, idx, scale)
    if ref in env.bytes:
        return ("bytes", ref, idx, scale)
    if ref.lower().startswith("0x"):
        return ("abs", int(ref, 16), None, None)
    if ref in env.consts:
        return ("abs", int(env.consts[ref], 0), None, None)
    raise CompileError("%s: nothing named %r - declare data-bss=\"%s=<size>\" "
                       "or data-bytes=\"%s=…\"" % (sid, ref, ref, ref))


def mem_operand(t, env, sid):
    """A COMPLETE RIP-relative operand: NAME(%rip) or NAME(%rip,%reg,scale)."""
    kind, ref, idx, scale = mem_of(t, env, sid)
    if kind == "abs":
        base = "0x%x" % ref
    else:
        base = ref
    if idx is None:
        return "%s(%%rip)" % base
    return "%s(%%rip,%%%s,%d)" % (base, idx, scale)


def mem_base_index(t, env, sid):
    """(base-operand, index-register-or-None) - the caller assembles the rest."""
    kind, ref, idx, _scale = mem_of(t, env, sid)
    return ("0x%x" % ref if kind == "abs" else ref), idx


def scale_of(t):
    m = re.search(r"\*(\d+)\]?$", t)
    return int(m.group(1)) if m else 1


def emit_imm_to(dst, val, out):
    """Materialise a 64-bit constant, using movabs when it does not fit in
    sign-extended imm32 - a silent truncation here would be invisible."""
    if -(1 << 31) <= val < (1 << 31):
        out.append("\tmovq\t$%d, %s" % (val, dst))
    else:
        out.append("\tmovabs\t$0x%x, %%%s" % (val, scratch()))
        out.append("\tmovq\t%%%s, %s" % (scratch(), dst))


# ------------------------------------------------------------------- emission

def parse_glyphs(s):
    """Read a shape's code as glyphs instead of text.

    A program need not contain letters. Each shape may hold emoji; the leading
    glyph is the instruction, the rest are its operands. Meaning comes from
    icons.py -- a dictionary -- never from how the drawing looks, so a glyph is
    never guessed at.

    Only glyphs are translated here. Anything that is not a glyph is passed
    through untouched, so memory refs (@0x...), sizes and named constants reach
    the ordinary operand checks and keep every rule they had before.

    Returns (opname, [operands]) or None if this shape is not in glyph form.
    """
    toks = []
    for node in [s.el] + list(s.el.iter()):
        for t in (node.text, node.tail):
            if t:
                toks += t.split()
    if not toks:
        return None

    opname = icons.op_for(toks[0])
    if opname is None:
        return None                     # not glyph form: fall back to text

    args = []
    for t in toks[1:]:
        r = icons.reg_for(t)
        args.append(r if r else t)      # non-glyph operand: leave it alone
    return opname, args


def glyph_lines(s):
    """A shape's glyph statements, one per line of text.

    One shape is normally one statement. But the kernel's own macros are many
    instructions written as one source line -- PUSH_AND_CLEAR_REGS is 28 of them
    -- so a glyph shape may hold a *block*, one statement per line. Control flow
    is unchanged: the block still has one successor, exactly as a basic block
    does. Forcing those 28 into 28 boxes would make the drawing lie about the
    source it came from.

    Returns a list of (opname, args), or None if this shape is not glyph form.
    """
    stmts = []
    for node in ([s.el] if s.el is not None else []) + list(s.el.iter() if s.el is not None else []):
        for chunk in (node.text, node.tail):
            if not chunk:
                continue
            for raw in chunk.splitlines():
                toks = raw.split()
                if not toks:
                    continue
                op = icons.op_for(toks[0])
                if op is None:
                    return None         # not glyph form after all
                args = []
                for t in toks[1:]:
                    r = icons.reg_for(t)
                    args.append(r if r else t)
                stmts.append((op, args))
    return stmts or None


def compile_shape(s, env, strs, out, stmt=None, label=None):
    consts = env.consts
    txt = s.text
    # When a shape is a block, `label` names the individual statement so an
    # error points at the exact line inside the box.
    sid = label or s.id

    # A glyph shape may be a block of statements, one per line. A rhombus is a
    # decision, so it must stay exactly one CMP.
    block = glyph_lines(s)
    if block is not None and s.kind == "test":
        if len(block) != 1 or block[0][0] != "cmp":
            raise CompileError(
                "%s: a <polygon> is one decision, so it must hold exactly one "
                "CMP - this glyph shape holds %d statement(s)"
                % (sid, len(block)))
    if block is not None and len(block) > 1:
        # One shape, many statements. Emit them in order through the same
        # single-statement path, so every check below applies unchanged. The id
        # is suffixed so an error still names the box and the line inside it.
        for i, (bop, bargs) in enumerate(block):
            sub = Shape(sid, "stmt", "", s.a, s.fn, None)
            compile_shape(sub, env, strs, out, stmt=(bop, bargs),
                          label="%s#%d" % (sid, i + 1))
        return

    g = stmt if stmt is not None else parse_glyphs(s)
    if g is not None:
        opname, args = g
        op = opname.upper()
        spec = icons.OPS[opname]
        if len(args) != spec['arity']:
            raise CompileError("%s: %s takes %d operand(s), the glyph form has %d"
                               % (sid, op, spec['arity'], len(args)))
    else:
        if not txt:
            raise CompileError("%s: empty <%s> - every shape needs code"
                               % (sid, "polygon" if s.kind == "test" else "rect"))
        head, _, rest = txt.partition(" ")
        op = head.upper()
        args = [a.strip() for a in rest.split(",") if a.strip()]
    # glyph form relaxes the named-constant rule (a name is a letter)
    bare = g is not None

    if s.kind == "test":
        if op != "CMP":
            raise CompileError("%s: a <polygon> is a decision, so it must hold "
                               "CMP - got %r" % (sid, head))
        if len(args) != 2:
            raise CompileError("%s: CMP takes 2 operands, got %d" % (sid, len(args)))
        for a in args:
            check_number(a, None, sid, "CMP")
        ka, va = value_of(args[0], consts, sid)
        kb, vb = value_of(args[1], consts, sid, addr_ok=True, allow_bare=bare)
        if ka == "reg" and kb == "reg":
            out.append("\tcmpq\t%%%s, %%%s" % (vb, va))
        elif ka == "reg":
            emit_imm_to("%" + scratch(), vb, out)
            out.append("\tcmpq\t%%%s, %%%s" % (scratch(), va))
        elif kb == "reg":
            out.append("\tmovq\t%%%s, %%%s" % (va, scratch()))
            out.append("\tcmpq\t%%%s, %%%s" % (vb, scratch()))
        else:
            out.append("\tcmpq\t$0x%x, %%%s" % (va, scratch()))
            out.append("\ttestq\t%%%s, %%%s" % (scratch(), scratch()))
        # Three named exits. Each conditional jump goes to a local label that
        # then jumps on to the target shape, so "data-lt=foo" appears in the
        # assembly as .Lid_lt -> .Lfoo and the decision is readable in objdump.
        out += ["\t%s\t.L%s_%s" % (j, sid, k) for j, k in
                (("jl", "lt"), ("je", "eq"), ("jg", "gt"))]
        for k in ("lt", "eq", "gt"):
            tgt = s.a.get("data-" + k)
            out.append(".L%s_%s:" % (sid, k))
            if tgt:
                out.append("\tjmp\t.L%s" % tgt)
        s.op = "CMP"
        return

    if op not in ARITY:
        raise CompileError("%s: unknown statement %r" % (sid, head))
    if len(args) != ARITY[op]:
        raise CompileError("%s: %s takes %d operand(s), got %d - %r"
                           % (sid, op, ARITY[op], len(args), txt))
    for i, a in enumerate(args):
        if g is not None and is_int(a):
            # The letter-free form has a genuine trade-off. The number rule says
            # a literal is only an address or a size, so an immediate like
            # `push $0x2b` should be named -- but a name is a letter, and this
            # form exists precisely to have none. So a glyph-form operand may be
            # a bare immediate. It is still checked to be a well-formed integer;
            # only the naming requirement is relaxed, and only here.
            continue
        check_number(a, i if SIZE_SLOTS.get(op) == i else None, sid, op)
    # Only these ops take a destination REGISTER first. STORE and FILL take a
    # memory reference first, and PRINT/PUTINT/EXIT take a name or a value, so
    # calling reg_of(args[0]) unconditionally rejected every one of them.
    # A MOV whose DESTINATION is memory is a STORE. This is not a convenience:
    # `movq %rsp, PER_CPU_VAR(cpu_tss_rw + TSS_sp2)` (save the user stack) and
    # `movq PER_CPU_VAR(pcpu_hot + X86_top_of_stack), %rsp` (load the kernel
    # stack) are opposite directions, and they are the first two real
    # instructions the kernel runs after swapgs. With MOV only able to load, the
    # program could express the second and not the first, and the trace showed
    # it as one instruction out of place rather than as a missing capability.
    mov_store = (op == "MOV" and args[0].startswith("@"))
    d = reg_of(args[0], sid) if (op in REGDST and not mov_store) else None

    if mov_store:
        # Only the register form is meaningful here. Storing a constant to
        # memory is what FILL is for, and allowing both would make `MOV @mem, 5`
        # mean something the number rule says cannot be written anyway.
        mem = mem_operand(args[0], env, sid)
        src = reg_of(args[1], sid)
        out.append("\tmovq\t%%%s, %s" % (src, mem))
    elif op == "MOVSXD":
        # Read at 32 bits, write at 64: `movslq %eax, %rsi`. The source MUST be
        # named with its 32-bit view (%eax, not %rax) or the assembler rejects
        # it -- and if it were accepted it would be a different instruction.
        kind, val = value_of(args[1], consts, sid, addr_ok=True, allow_bare=bare)
        if kind == "reg":
            out.append("\tmovslq\t%%%s, %%%s" % (reg32(val, sid), d))
        else:
            # A constant is already exactly known, so widening it is a plain
            # move. emit_imm_to handles the sign correctly, which "$0x%x" does
            # not: -1 would come out as the nonsense $0x-1.
            emit_imm_to("%" + d, val, out)
    elif op in ("MOV", "ADD", "SUB", "AND", "OR", "XOR"):
        src = args[1]
        if src.startswith("@"):
            mem = mem_operand(src, env, sid)
            out.append("\tmovq\t%s, %%%s" % (mem, d))
        else:
            kind, val = value_of(src, consts, sid, addr_ok=True, allow_bare=bare)
            mn = {"MOV": "movq", "ADD": "addq", "SUB": "subq",
                  "AND": "andq", "OR": "orq", "XOR": "xorq"}[op]
            if kind == "reg":
                out.append("\t%s\t%%%s, %%%s" % (mn, val, d))
            elif -(1 << 31) <= val < (1 << 31):
                out.append("\t%s\t$0x%x, %%%s" % (mn, val, d))
            else:
                out.append("\tmovabs\t$0x%x, %%%s" % (val, scratch()))
                out.append("\t%s\t%%%s, %%%s" % (mn, scratch(), d))
    elif op in ("MUL", "DIV"):
        # x86 has no dst = dst OP src in one instruction for MUL/DIV, so the
        # destination goes through RAX. RDX is spared by using R11 as scratch
        # for an immediate, and a division by zero is left to fault loudly.
        kind, val = value_of(args[1], consts, sid, addr_ok=True, allow_bare=bare)
        out.append("\tmovq\t%%%s, %%rax" % d)
        if kind == "reg":
            out.append("\timulq\t%%%s, %%rax" % val)
        elif -(1 << 31) <= val < (1 << 31):
            out.append("\timulq\t$0x%x, %%rax, %%rax" % val)
        else:
            out.append("\tmovabs\t$0x%x, %%%s" % (val, scratch()))
            out.append("\timulq\t%%%s, %%rax" % scratch())
        if op == "DIV":
            if kind == "reg":
                out.append("\tmovq\t%%%s, %%%s" % (val, scratch()))
            else:
                out.append("\tmovabs\t$0x%x, %%%s" % (val, scratch()))
            out.append("\tmovq\t$0, %%edx")
            out.append("\tdivq\t%%%s" % scratch())
        out.append("\tmovq\t%%rax, %%%s" % d)
    elif op in ("SHL", "SHR"):
        out.append("\t%s\t$%s, %%%s" % ("shlq" if op == "SHL" else "shrq",
                                        args[1], d))
    elif op.startswith("LOAD."):
        # SB is the signed byte: a table of signed values (a waveform) read back
        # with .B would come out 230 instead of -26.
        w = {"B": "movzbq", "SB": "movsbq", "W": "movzwq",
             "D": "movl", "Q": "movq"}[op.split(".")[1]]
        base, idx = mem_base_index(args[1], env, sid)
        if idx is None:
            out.append("\t%s\t%s, %%%s" % (w, base, d))
        else:
            # leaq the table into scratch first: NAME(%rip,%reg,scale) is not a
            # legal x86 address, because RIP cannot be a base with an index.
            out.append("\tleaq\t%s(%%rip), %%%s" % (base, scratch()))
            out.append("\t%s\t(%%%s,%%%s,%d), %%%s" % (w, scratch(), idx, scale_of(args[1]), d))
    elif op.startswith("STORE."):
        w = {"B": "movb", "W": "movw", "D": "movl", "Q": "movq"}[op[-1]]
        base, idx = mem_base_index(args[0], env, sid)
        src_reg = reg_of(args[1], sid)
        if idx is None:
            out.append("\t%s\t%%%s, %s" % (w, src_reg, base))
        else:
            out.append("\tleaq\t%s(%%rip), %%%s" % (base, scratch()))
            out.append("\t%s\t%%%s, (%%%s,%%%s,%d)"
                       % (w, src_reg, idx, scale_of(args[0])))
    elif op == "FILL":
        mem = mem_operand(args[0], env, sid)
        kind, val = value_of(args[1], consts, sid, addr_ok=True, allow_bare=bare)
        out.append("\tleaq\t%s, %%rdi" % mem)
        emit_imm_to("%rax", val, out)
        out.append("\tmovl\t$%s, %%ecx" % args[2])
        out.append("\trep stosb")
    elif op == "PRINT":
        if args[0] == "SOURCE":
            pass          # emitted by the compiler via .incbin
        elif args[0] not in strs:
            raise CompileError("%s: no string named %r - declare "
                               'data-str="%s=text"' % (sid, args[0], args[0]))
        out.append("\tleaq\tstr_%s(%%rip), %%rdi" % args[0])
        out.append("\tcall\tputstr")
    elif op == "PUTINT":
        kind, val = value_of(args[0], consts, sid, addr_ok=True, allow_bare=bare)
        if kind == "reg":
            out.append("\tmovq\t%%%s, %%rdi" % val)
        else:
            emit_imm_to("%rdi", val, out)
        out.append("\tcall\tputint")
    elif op == "EXIT":
        _k, _v = value_of(args[0], consts, sid, addr_ok=True, allow_bare=bare)
        out.append("\tmovl\t$0x%x, %%edi" % _v)
        out.append("\tmovl\t$60, %eax")
        out.append("\tsyscall")
    elif op in ("PUSH", "POP"):
        a = args[0]
        if a.startswith("@"):
            mem = mem_operand(a, env, sid)
            out.append("\t%s\t%s" % (op.lower(), mem))
        else:
            kind, val = value_of(a, consts, sid, addr_ok=True, allow_bare=bare)
            if kind == "reg":
                out.append("\t%s\t%%%s" % (op.lower(), val))
            else:
                out.append("\tpushq\t$0x%x" % val if op == "PUSH"
                           else "\tpopq\t%%rax")
                if op == "POP" and kind != "reg":
                    # POP of an immediate is meaningless; the value came from a
                    # constant, so push it back to keep the operand meaningful.
                    out.append("\tpushq\t$0x%x" % val)
    elif op == "SWAPGS":
        out.append("\tswapgs")
    elif op == "NOP":
        out.append("\tnop")
    elif op == "TO_USER":
        # No instruction: control leaves the program here. A kernel fragment
        # does not "exit" the way a userspace process does.
        pass
    elif op == "CMP":
        # CMP only sets flags; the rhombus's data-eq/gt edges read them.
        a, b = args
        ka, va = value_of(a, consts, sid, addr_ok=True, allow_bare=bare)
        if ka == "reg":
            out.append("\tcmpq\t%%%s, %%%s" % (va, reg_of(b, sid)))
        else:
            kb, vb = value_of(b, consts, sid, addr_ok=True, allow_bare=bare)
            if kb == "reg":
                out.append("\tcmpq\t$0x%x, %%%s" % (vb, va))
            else:
                out.append("\tmovq\t$0x%x, %%%s" % (vb, scratch()))
                out.append("\tcmpq\t%%%s, %%%s" % (scratch(), va))
    elif op == "SYSRET":
        out.append("\tsysretq")
    elif op == "IRET":
        out.append("\tiretq")
    elif op == "CALL":
        # A call names a *shape*, the same way the edges do. That is checked
        # first: the target is a label in this program, not a value, so it must
        # not go through the register/constant/address machinery.
        tgt = args[0]
        if not tgt.startswith("@"):
            raise CompileError("%s: CALL names a shape or an extern, so its "
                               "operand must be @name - got %r" % (sid, tgt))
        name = tgt[1:]
        if name in env.externs:
            # Defined in another kernel file. Emit a real call to that symbol,
            # NOT a jump to a local block, because the callee's name is the only
            # thing about a call site that can be checked at all: the address is
            # a link-time accident, but "we called do_syscall_64" is a fact about
            # the program. The name is preserved into the disassembly so
            # verify-trace can compare it against the real trace.
            out.append("\tcall\t%s" % name)
        elif name in shapes_of(s, env):
            out.append("\tcall\t.L%s" % name)
        else:
            # Deliberately an error. An unknown name that silently became an
            # external symbol would turn a typo'd shape id into an undefined
            # reference, and the linker's complaint would point at the symbol
            # table rather than at the shape that was misspelled.
            raise CompileError("%s: CALL @%s is neither a shape in this program "
                               "nor listed in data-extern" % (sid, name))
    s.op = op


def build_asm(env, strs, shapes, order, entry, source_path=None):
    consts, bss, bytes_ = env.consts, env.bss, env.bytes
    lines = ['\t.file "fsvg"', "\t.text", "_start:"]

    # group shapes by function so CALL targets exist
    by_fn = {}
    for sid in order:
        by_fn.setdefault(shapes[sid].fn, []).append(sid)

    def emit_group(fns):
        for sid in fns:
            s = shapes[sid]
            lines.append(".L%s:" % sid)
            compile_shape(s, env, strs, lines)
            if s.kind == "stmt" and s.a.get("data-next"):
                lines.append("\tjmp\t.L%s" % s.a["data-next"])

    main_group = by_fn.get(None, [])
    other = {k: v for k, v in by_fn.items() if k is not None}
    emit_group(main_group)
    for fn, fns in other.items():
        lines.append("fn_%s:" % fn)
        emit_group(fns)
        lines.append("\tret")
    if not main_group:
        raise CompileError("no shapes outside a data-fn group: the entry point "
                           "must be top-level")
    lines.insert(3, "\tjmp\t.L%s" % entry)

    lines.append(RUNTIME)
    lines.append("\t.section .rodata")
    if source_path:
        # PRINT SOURCE emits the program's own flowchart, byte for byte. Using
        # .incbin rather than a string literal means the SVG's quotes, commas
        # and semicolons need no escaping at all - and the round trip is exact.
        lines.append("str_SOURCE:\t.incbin \"%s\"" % source_path)
        lines.append("\t.byte 0")
    for name, text in strs.items():
        lines.append('str_%s:\t.asciz "%s"' % (name, esc_asm(text)))
    for name, nums in bytes_.items():
        lines.append("\t.p2align 3")
        lines.append("%s:" % name)
        for i in range(0, len(nums), 16):
            lines.append("\t.byte " + ",".join(str(x) for x in nums[i:i + 16]))
    if bss:
        lines.append("\t.section .bss")
        for name, size in bss.items():
            try:
                n = int(size, 0)
            except ValueError:
                raise CompileError('data-bss: %s=<size> must be a number, got %r'
                                   % (name, size))
            if n <= 0:
                raise CompileError("data-bss: %s must have a positive size" % name)
            lines.append("\t.p2align 3")
            lines.append("%s:\t.skip %d" % (name, n))
    return "\n".join(lines) + "\n"


def esc_asm(t):
    return t.replace("\\", "\\\\").replace('"', '\\"')


def external_stub(names):
    """A stub object defining symbols the program calls but does not own.

    The kernel fragment calls do_syscall_64, which lives in a different file we
    have not translated. The linker needs *something* with that name, and the
    reason to give it one is narrow and worth stating: it makes the symbol appear
    in the disassembly as `call <do_syscall_64>`, which is the only part of a
    call site a trace can actually verify.

    The bodies are `ret` and are never executed -- this program cannot run
    (swapgs faults in ring 3) and is only disassembled. So the stub changes no
    behaviour and is not a claim about the kernel; it exists so the name survives
    linking. Anything that did run this code would be running a lie, so nothing
    does.
    """
    lines = ["\t.text"]
    for n in sorted(names):
        lines += ["\t.globl\t%s" % n, "\t.type\t%s, @function" % n,
                  "%s:" % n, "\tret"]
    return "\n".join(lines) + "\n"


def link(asm_text, out, keep_asm=None, externs=()):
    d = tempfile.mkdtemp(prefix="fsvg-")
    s_path, o_path = os.path.join(d, "p.s"), os.path.join(d, "p.o")
    open(s_path, "w").write(asm_text)
    r = subprocess.run(["as", "--64", "-o", o_path, s_path],
                       capture_output=True, text=True)
    if r.returncode:
        raise CompileError("assembler rejected the generated code:\n" + r.stderr[:2000])
    objs = [o_path]
    if externs:
        e_s, e_o = os.path.join(d, "extern.s"), os.path.join(d, "extern.o")
        open(e_s, "w").write(external_stub(externs))
        r = subprocess.run(["as", "--64", "-o", e_o, e_s],
                           capture_output=True, text=True)
        if r.returncode:
            raise CompileError("assembler rejected the extern stubs:\n" + r.stderr[:2000])
        objs.append(e_o)
    r = subprocess.run(["ld", "-o", out, "-e", "_start", "--build-id=none"] + objs,
                       capture_output=True, text=True)
    if r.returncode:
        raise CompileError("link failed:\n" + r.stderr[:2000])
    if keep_asm:
        open(keep_asm, "w").write(asm_text)
    return out


def main():
    ap = argparse.ArgumentParser(description="compile an FSVG flowchart to a Linux binary")
    ap.add_argument("source")
    ap.add_argument("-o", "--out", default="a.out")
    ap.add_argument("--asm", action="store_true", help="also write the .s next to the binary")
    ap.add_argument("--use-r11", action="store_true",
                    help="R11 belongs to the program, not the compiler "
                         "(kernel entry paths: the CPU puts RFLAGS there). "
                         "The compiler then borrows RDI as scratch instead.")
    args = ap.parse_args()
    if args.use_r11:
        global USE_R11, REGS
        USE_R11 = True
        REGS = REGS + ["R11"]
        icons.REGS["r11"] = dict(emoji='\U0001F7E7', hue='warm',
                                 out='M4 4h16v16H4zM8 8h8v8H8z')
    try:
        (consts, bss, strs, byte_tables, shapes, order, entry,
         externs) = load(args.source)
        env = Env(consts, bss, byte_tables, shapes.keys(), externs)
        asm_text = build_asm(env, strs, shapes, order, entry,
                             source_path=os.path.abspath(args.source))
        link(asm_text, args.out, args.out + ".s" if args.asm else None,
             externs=externs)
    except CompileError as e:
        sys.exit("fsvgc: %s" % e)
    nstmt = sum(1 for s in shapes.values() if s.kind == "stmt")
    ntest = sum(1 for s in shapes.values() if s.kind == "test")
    print("%s -> %s   %d statements, %d tests, %d constants, %d buffers, "
          "%d strings, %d tables"
          % (args.source, args.out, nstmt, ntest, len(consts), len(bss),
             len(strs), len(byte_tables)))


if __name__ == "__main__":
    main()