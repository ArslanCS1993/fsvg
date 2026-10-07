"""
icons.py -- the glyph vocabulary of FSVG.

A program contains no letters. An instruction is named by an *emoji*, and drawn
as an SVG icon. Both are required and must agree: the emoji is the key the
compiler resolves, the SVG is what a human reads. If they disagree it is a
compile error, so a hand-edited icon cannot silently mean the wrong opcode.

Every glyph lives here, in the compiler -- never in the program. That is the
whole trick: the drawing carries meaning, and this table is the dictionary.

Operand glyphs
--------------
Registers are shape + hue, never colour alone, so a diagram survives being
printed in black and white: eight distinct outlines, in two hue families, give
sixteen registers. Colour alone would fail for a colour-blind reader and would
vanish on a monochrome printer.

Memory addresses and sizes stay numeric -- that is the one rule the language
never bends.
"""

# --------------------------------------------------------------- instruction
# d      : SVG path data on a 24x24 grid
# arity  : number of operands the instruction takes
# io     : how many it writes ("MOV RAX, RBX" writes the first)
OPS = {
    'swapgs': dict(
        emoji='\U0001F504',                      # arrows clockwise
        d='M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4',  # two arcs = exchange halves
        arity=0, io=0,
        title='swapgs'),
    'mov': dict(
        emoji='\U0001F6E1',                      # highway = straight move
        d='M3 12h13M13 8l5 4-5 4',               # arrow into a stop bar
        arity=2, io=1,
        title='mov'),
    'movsxd': dict(
        emoji='\u23EB',                          # double up arrow = widen upward
        # A short bar and a long bar joined by an up arrow: the narrow 32-bit
        # operand becoming the wide 64-bit one, sign carried up into the top
        # half. Deliberately not a second arrow -- `mov` already owns one, and
        # the two must not look interchangeable, because they are not:
        # `mov %rax,%rsi` and `movslq %eax,%rsi` differ in the trace.
        d='M4 17h4M4 20h9M6 15V6M3 9l3-3 3 3',
        arity=2, io=1,
        title='movsxd'),
    'push': dict(
        emoji='\U0001F4E5',                      # inbox tray = down onto stack
        d='M12 3v11M8 10l4 4 4-4M4 20h16',
        arity=1, io=0,
        title='push'),
    'pop': dict(
        emoji='\U0001F4E4',                      # outbox tray = up off stack
        d='M12 14V3M8 7l4-4 4 4M4 20h16',
        arity=1, io=0,
        title='pop'),
    'xor': dict(
        emoji='\U0001F5D1',                      # wastebasket = clear to zero
        d='M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
        arity=2, io=1,
        title='xor'),
    'cmp': dict(
        emoji='\U0001F916',                      # robot = compare
        d='M5 9h14M5 15h14M9 5v14',
        arity=2, io=0,
        title='cmp'),
    'call': dict(
        emoji='\U0001F4DE',                      # handset = invoke
        d='M6 3l4 5-2.5 2A11 11 0 0 0 14 17.5L16 15l5 4',
        arity=1, io=0,
        title='call'),
    'sysret': dict(
        emoji='\U0001F680',                      # rocket = return to user
        d='M12 3c4 3 5 8 4 12l-4 6-4-6c-1-4 0-9 4-12z',
        arity=0, io=0,
        title='sysret'),
    'iret': dict(
        emoji='\U0001F6D1',                      # stop sign = the slow path
        d='M12 3l9 16H3z',
        arity=0, io=0,
        title='iret'),
    'nop': dict(
        emoji='\U0001F6D0',                      # (unused placeholder)
        d='M8 12h8',
        arity=0, io=0,
        title='nop'),
    # The end of a kernel fragment. Control does not come back to this code: a
    # syscall returns to userspace and the kernel keeps running elsewhere. It
    # emits no instruction -- it is a label that exists so the flow has an end.
    'to_user': dict(
        emoji='\U0001F6AA',                      # door
        d='M6 3h12v18H6zM10 12h4M12 10v4',
        arity=0, io=0,
        title='to_user'),
}

# ------------------------------------------------------------------- operand
# Registers are shape + hue, never colour alone, so a diagram survives being
# printed in black and white: distinct outlines, in two hue families. Colour
# alone would fail for a colour-blind reader and would vanish on a monochrome
# printer.
#
# The emoji below are the compiler's *key* -- an exact codepoint, never a guess
# from a picture. The SVG outline above it is what a human reads, and it is the
# outline that carries the shape distinction.
#
# Note honestly: a few of the R8-R15 silhouettes are similar squares, so those
# rely on hue as a secondary cue. Correctness never depends on it -- a wrong
# codepoint is a compile error, not a silent misread.
REGS = {
    'rax': dict(emoji='\U0001F53A', out='M12 3l8 5v8l-8 5-8-5V8z',  hue='warm'),
    'rbx': dict(emoji='\U0001F536', out='M5 5h14v14H5z',            hue='cool'),
    'rcx': dict(emoji='\U0001F537', out='M12 3l9 9-9 9-9-9z',        hue='warm'),
    'rdx': dict(emoji='\U0001F535', out='M12 2l10 10-10 10L2 12zM8 12h8M12 8v8', hue='cool'),
    'rsi': dict(emoji='⭐',           out='M12 2l3 7 7 1-5 5 1 7-6-3-6 3 1-7-5-5-7-1 7-1z', hue='warm'),
    'rdi': dict(emoji='\U0001F511', out='M9 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm1 3v2h6v-2z', hue='cool'),
    'rbp': dict(emoji='\U0001F512', out='M6 10V7a6 6 0 0 1 12 0v3M5 10h14v10H5z', hue='warm'),
    'rsp': dict(emoji='\U0001F9F1', out='M4 6h16v5H4zM4 15h16v4H4z', hue='cool'),
    'r8':  dict(emoji='\U0001F7E8', out='M3 12h6V6h6v6h6v6h-6v6H9v-6H3z',  hue='warm'),
    'r9':  dict(emoji='\U0001F7E2', out='M5 3l8 4v10l-8 4z',           hue='cool'),
    'r10': dict(emoji='\U0001F7E1', out='M3 6h18l-3 14H6z',            hue='warm'),
    'r12': dict(emoji='\U0001F7E0', out='M12 2l10 18H2zM12 8l5 8H7z',    hue='cool'),
       # r13 was once the SAME path as r8 (both were a cross), which is precisely
       # the failure this table exists to prevent: two registers that draw
       # identically cannot be told apart on paper, so the outline is no longer
       # carrying the identity and only hue is left. It is now a triangle-in-square.
    'r13': dict(emoji='\U0001F7EB', out='M4 4h16v16H4zM12 7l4.5 8h-9z',  hue='warm'),
    'r14': dict(emoji='▣',           out='M4 4h16v16H4zM8 8h8v8H8z',      hue='cool'),
    'r15': dict(emoji='◈',           out='M12 2l10 10-10 10L2 12zM12 7l5 5-5 5-5-5z', hue='warm'),
}

# 32-bit views share the register's identity; the compiler tracks the width.
REG32 = {k: k[1:] for k in REGS}

HUES = {'warm': '#f0883e', 'cool': '#58a6ff'}


# ------------------------------------------------------------------ helpers
def op_for(emoji):
    """Emoji -> opcode, or None. Exact codepoint match, never a guess."""
    for name, spec in OPS.items():
        if spec['emoji'] == emoji:
            return name
    return None


def reg_for(emoji):
    for name, spec in REGS.items():
        if spec['emoji'] == emoji:
            return name
    return None


def op_icon(name):
    """Opcode -> the SVG <path> markup for its icon."""
    s = OPS[name]
    return '<path class="ico" d="%s" fill="none" stroke="currentColor" ' \
           'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' % s['d']


def reg_icon(name):
    s = REGS[name]
    return ('<path class="ico" d="%s" fill="none" stroke="%s" stroke-width="2" '
            'stroke-linejoin="round"/>' % (s['out'], HUES[s['hue']]))


def describe(emoji):
    """A short human name, used only in error messages -- never in a program."""
    n = op_for(emoji)
    if n:
        return OPS[n]['title']
    n = reg_for(emoji)
    if n:
        return n
    return None


def vocab():
    return dict(ops=len(OPS), regs=len(REGS))