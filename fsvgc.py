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

SVG = "{http://www.w3.org/2000/svg}"
# R11 is deliberately absent: it is compiler scratch. Indexed addressing needs
# it (x86 forbids RIP-relative WITH an index register), and a language that
# hands every GPR to the user has nowhere else to put a base address.
REGS = ["RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP"] + \
       ["R%d" % i for i in range(8, 16) if i != 11]

# ops whose operand is a SIZE, where a bare integer is therefore legal
SIZE_SLOTS = {"FILL": 2, "SHL": 1, "SHR": 1, "EXIT": 0}
ARITY = {"MOV": 2, "ADD": 2, "SUB": 2, "AND": 2, "OR": 2, "XOR": 2,
         "MUL": 2, "DIV": 2,
         "SHL": 2, "SHR": 2, "FILL": 3, "PRINT": 1, "PUTINT": 1, "EXIT": 1,
         "LOAD.B": 2, "LOAD.SB": 2, "LOAD.W": 2, "LOAD.D": 2, "LOAD.Q": 2,
         "STORE.B": 2, "STORE.W": 2, "STORE.D": 2, "STORE.Q": 2}

# ops whose FIRST operand is a destination register (STORE/FILL take memory
# first, PRINT/PUTINT/EXIT take a name or a value)
REGDST = {"MOV", "ADD", "SUB", "AND", "OR", "XOR", "MUL", "DIV", "SHL", "SHR"} | \
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
    __slots__ = ("id", "kind", "text", "a", "fn", "op")

    def __init__(self, sid, kind, text, attrs, fn):
        self.id, self.kind, self.text, self.a, self.fn = sid, kind, text, attrs, fn
        self.op = None

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
        shapes[sid] = Shape(sid, kind, shape_text(el), dict(el.attrib), fn_of(el))
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
    return consts, bss, strs, byte_tables, shapes, order, entry[0]


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


def value_of(t, consts, sid, addr_ok=False):
    """Return ('reg', name) or ('imm', int) for a plain value operand."""
    if t.upper() in REGS:
        return ("reg", t.lower())
    if t in consts:
        return ("imm", int(consts[t], 0))
    if addr_ok and t.lower().startswith("0x"):
        return ("imm", int(t, 16))
    if is_int(t):
        raise CompileError("%s: bare number %r - give it a name in data-const"
                           % (sid, t))
    raise CompileError("%s: %r is not a register, a data-const, or a 0x address"
                       % (sid, t))


class Env:
    """The program's symbol tables: named constants, buffers, byte tables."""

    def __init__(self, consts, bss, bytes_):
        self.consts, self.bss, self.bytes = consts, bss, bytes_


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
        out.append("\tmovabs\t$0x%x, %%r11" % val)
        out.append("\tmovq\t%%r11, %s" % dst)


# ------------------------------------------------------------------- emission

def compile_shape(s, env, strs, out):
    consts = env.consts
    txt = s.text
    if not txt:
        raise CompileError("%s: empty <%s> - every shape needs code"
                           % (s.id, "polygon" if s.kind == "test" else "rect"))
    head, _, rest = txt.partition(" ")
    op = head.upper()
    args = [a.strip() for a in rest.split(",") if a.strip()]

    if s.kind == "test":
        if op != "CMP":
            raise CompileError("%s: a <polygon> is a decision, so it must hold "
                               "CMP - got %r" % (s.id, head))
        if len(args) != 2:
            raise CompileError("%s: CMP takes 2 operands, got %d" % (s.id, len(args)))
        for a in args:
            check_number(a, None, s.id, "CMP")
        ka, va = value_of(args[0], consts, s.id)
        kb, vb = value_of(args[1], consts, s.id, addr_ok=True)
        if ka == "reg" and kb == "reg":
            out.append("\tcmpq\t%%%s, %%%s" % (vb, va))
        elif ka == "reg":
            emit_imm_to("%r11", vb, out)
            out.append("\tcmpq\t%%r11, %%%s" % va)
        elif kb == "reg":
            out.append("\tmovq\t%%%s, %%r11" % va)
            out.append("\tcmpq\t%%%s, %%r11" % vb)
        else:
            out.append("\tcmpq\t$0x%x, %%r11" % va)
            out.append("\ttestq\t%%r11, %%r11")
        # Three named exits. Each conditional jump goes to a local label that
        # then jumps on to the target shape, so "data-lt=foo" appears in the
        # assembly as .Lid_lt -> .Lfoo and the decision is readable in objdump.
        out += ["\t%s\t.L%s_%s" % (j, s.id, k) for j, k in
                (("jl", "lt"), ("je", "eq"), ("jg", "gt"))]
        for k in ("lt", "eq", "gt"):
            tgt = s.a.get("data-" + k)
            out.append(".L%s_%s:" % (s.id, k))
            if tgt:
                out.append("\tjmp\t.L%s" % tgt)
        s.op = "CMP"
        return

    if op not in ARITY:
        raise CompileError("%s: unknown statement %r" % (s.id, head))
    if len(args) != ARITY[op]:
        raise CompileError("%s: %s takes %d operand(s), got %d - %r"
                           % (s.id, op, ARITY[op], len(args), txt))
    for i, a in enumerate(args):
        check_number(a, i if SIZE_SLOTS.get(op) == i else None, s.id, op)
    # Only these ops take a destination REGISTER first. STORE and FILL take a
    # memory reference first, and PRINT/PUTINT/EXIT take a name or a value, so
    # calling reg_of(args[0]) unconditionally rejected every one of them.
    d = reg_of(args[0], s.id) if op in REGDST else None

    if op in ("MOV", "ADD", "SUB", "AND", "OR", "XOR"):
        src = args[1]
        if src.startswith("@"):
            mem = mem_operand(src, env, s.id)
            out.append("\tmovq\t%s, %%%s" % (mem, d))
        else:
            kind, val = value_of(src, consts, s.id, addr_ok=True)
            mn = {"MOV": "movq", "ADD": "addq", "SUB": "subq",
                  "AND": "andq", "OR": "orq", "XOR": "xorq"}[op]
            if kind == "reg":
                out.append("\t%s\t%%%s, %%%s" % (mn, val, d))
            elif -(1 << 31) <= val < (1 << 31):
                out.append("\t%s\t$0x%x, %%%s" % (mn, val, d))
            else:
                out.append("\tmovabs\t$0x%x, %%r11" % val)
                out.append("\t%s\t%%r11, %%%s" % (mn, d))
    elif op in ("MUL", "DIV"):
        # x86 has no dst = dst OP src in one instruction for MUL/DIV, so the
        # destination goes through RAX. RDX is spared by using R11 as scratch
        # for an immediate, and a division by zero is left to fault loudly.
        kind, val = value_of(args[1], consts, s.id, addr_ok=True)
        out.append("\tmovq\t%%%s, %%rax" % d)
        if kind == "reg":
            out.append("\timulq\t%%%s, %%rax" % val)
        elif -(1 << 31) <= val < (1 << 31):
            out.append("\timulq\t$0x%x, %%rax, %%rax" % val)
        else:
            out.append("\tmovabs\t$0x%x, %%r11" % val)
            out.append("\timulq\t%%r11, %%rax")
        if op == "DIV":
            if kind == "reg":
                out.append("\tmovq\t%%%s, %%r11" % val)
            else:
                out.append("\tmovabs\t$0x%x, %%r11" % val)
            out.append("\tmovq\t$0, %%edx")
            out.append("\tdivq\t%%r11")
        out.append("\tmovq\t%%rax, %%%s" % d)
    elif op in ("SHL", "SHR"):
        out.append("\t%s\t$%s, %%%s" % ("shlq" if op == "SHL" else "shrq",
                                        args[1], d))
    elif op.startswith("LOAD."):
        # SB is the signed byte: a table of signed values (a waveform) read back
        # with .B would come out 230 instead of -26.
        w = {"B": "movzbq", "SB": "movsbq", "W": "movzwq",
             "D": "movl", "Q": "movq"}[op.split(".")[1]]
        base, idx = mem_base_index(args[1], env, s.id)
        if idx is None:
            out.append("\t%s\t%s, %%%s" % (w, base, d))
        else:
            # leaq the table into scratch first: NAME(%rip,%reg,scale) is not a
            # legal x86 address, because RIP cannot be a base with an index.
            out.append("\tleaq\t%s(%%rip), %%r11" % base)
            out.append("\t%s\t(%%r11,%%%s,%d), %%%s" % (w, idx, scale_of(args[1]), d))
    elif op.startswith("STORE."):
        w = {"B": "movb", "W": "movw", "D": "movl", "Q": "movq"}[op[-1]]
        base, idx = mem_base_index(args[0], env, s.id)
        src_reg = reg_of(args[1], s.id)
        if idx is None:
            out.append("\t%s\t%%%s, %s" % (w, src_reg, base))
        else:
            out.append("\tleaq\t%s(%%rip), %%r11" % base)
            out.append("\t%s\t%%%s, (%%r11,%%%s,%d)"
                       % (w, src_reg, idx, scale_of(args[0])))
    elif op == "FILL":
        mem = mem_operand(args[0], env, s.id)
        kind, val = value_of(args[1], consts, s.id, addr_ok=True)
        out.append("\tleaq\t%s, %%rdi" % mem)
        emit_imm_to("%rax", val, out)
        out.append("\tmovl\t$%s, %%ecx" % args[2])
        out.append("\trep stosb")
    elif op == "PRINT":
        if args[0] == "SOURCE":
            pass          # emitted by the compiler via .incbin
        elif args[0] not in strs:
            raise CompileError("%s: no string named %r - declare "
                               'data-str="%s=text"' % (s.id, args[0], args[0]))
        out.append("\tleaq\tstr_%s(%%rip), %%rdi" % args[0])
        out.append("\tcall\tputstr")
    elif op == "PUTINT":
        kind, val = value_of(args[0], consts, s.id, addr_ok=True)
        if kind == "reg":
            out.append("\tmovq\t%%%s, %%rdi" % val)
        else:
            emit_imm_to("%rdi", val, out)
        out.append("\tcall\tputint")
    elif op == "EXIT":
        _k, _v = value_of(args[0], consts, s.id, addr_ok=True)
        out.append("\tmovl\t$0x%x, %%edi" % _v)
        out.append("\tmovl\t$60, %eax")
        out.append("\tsyscall")
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


def link(asm_text, out, keep_asm=None):
    d = tempfile.mkdtemp(prefix="fsvg-")
    s_path, o_path = os.path.join(d, "p.s"), os.path.join(d, "p.o")
    open(s_path, "w").write(asm_text)
    r = subprocess.run(["as", "--64", "-o", o_path, s_path],
                       capture_output=True, text=True)
    if r.returncode:
        raise CompileError("assembler rejected the generated code:\n" + r.stderr[:2000])
    r = subprocess.run(["ld", "-o", out, "-e", "_start", "--build-id=none", o_path],
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
    args = ap.parse_args()
    try:
        consts, bss, strs, byte_tables, shapes, order, entry = load(args.source)
        env = Env(consts, bss, byte_tables)
        asm_text = build_asm(env, strs, shapes, order, entry,
                             source_path=os.path.abspath(args.source))
        link(asm_text, args.out, args.out + ".s" if args.asm else None)
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