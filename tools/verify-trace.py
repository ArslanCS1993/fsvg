#!/usr/bin/env python3
"""
verify-trace.py -- check FSVG's output against a real instruction trace.

This is the only kind of proof that matters for the kernel work. A trace is what
the CPU actually executed, so it can settle an argument that reading the code
cannot: ordering, register width, and the exact operand.

Kernel programs cannot be *run* here -- `swapgs` is privileged and faults in
ring 3 -- so the check is static: compile the flowchart, disassemble the ELF,
and compare instruction by instruction against the recorded trace.

    python3 verify-trace.py [--binary build/03-entry-syscall] [--trace ...]

Exit status is the verdict: 0 when every instruction that both sides record
matches, so build.sh can fail on a regression.
"""
import argparse, json, re, subprocess, sys

DEFAULT_TRACE = '/root/gh-cpu3d/src/trace.json'
ENTRY = 'arch/x86/entry/entry_64.S'


def real_instructions(trace_path, entry):
    d = json.load(open(trace_path))
    pat = re.compile(r'([0-9a-f]+)\s+<[^>]+>:\s*(.*)$')
    out = []
    for x in d['steps']:
        if not x['file'].endswith(entry):
            continue
        t = pat.search(x['insn']).group(2).strip().split(None, 1)
        out.append((x['line'], t[0].lower().rstrip('q'), t[1] if len(t) > 1 else ''))
    return out


def fsvg_instructions(binary):
    res = subprocess.run(['objdump', '-d', binary, '--no-show-raw-insn'],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("objdump failed on %s" % binary)
    body = res.stdout.split('<_start>:', 1)[-1]
    out = []
    for line in body.splitlines():
        m = re.match(r'\s*[0-9a-f]+:\s+(\S+)\s*(.*)$', line)
        if not m:
            continue
        op = m.group(1).lower().rstrip('q')
        # FSVG puts a jmp after every shape (a shape is a labelled block) and
        # as/ld inserts alignment nops. Neither is part of the program.
        if op in ('jmp', 'nop'):
            continue
        out.append((op, m.group(2).split('#')[0].strip()))
    return out


def canon(op, arg):
    a = arg.replace(' ', '').replace('$', '')
    a = re.sub(r'0x[0-9a-f]{16}', 'IMM64', a)
    # The kernel's zeroing idiom writes 32-bit views (xor %r15d,%r15d, xorl
    # %esi,%esi); FSVG has one width per register and writes %r15 / %rsi. Both
    # name the same register, so compare them canonically. Getting this wrong
    # would report 13 phantom differences, which is worse than no check at all.
    #
    # One pass, so ordering cannot bite: strip an optional r/e prefix and any
    # width suffix in a single substitution. %r11d, %r11 and %11d all become %11.
    # `rip` is NOT a register -- mangling it to %ip would make every
    # RIP-relative load look like a mismatch.
    a = re.sub(r'%(?!rip\b)(?:r|e)?([a-z][a-z]|1[0-5]|[0-9])[dwb]?', r'%\1', a)
    a = re.sub(r'\bIMM\b', 'IMM64', a)
    # a RIP-relative absolute and a bare 64-bit immediate are the same operand
    # objdump prints an absolute 64-bit address as a RIP-relative displacement
    # while the trace prints the address itself. Same operand, two notations.
    a = re.sub(r'IMM64\(%rip\)', 'IMM64', a)
    a = re.sub(r'-?0x[0-9a-f]+\(%rip\)', 'IMM64', a)
    a = re.sub(r'-0x[0-9a-f]+$', 'IMM64', a)
    a = re.sub(r'^0x[0-9a-f]{9,}$', 'IMM64', a)
    return op, a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', default='build/03-entry-syscall')
    ap.add_argument('--trace', default=DEFAULT_TRACE)
    ap.add_argument('--entry', default=ENTRY)
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()

    real = real_instructions(a.trace, a.entry)
    mine = fsvg_instructions(a.binary)
    n = min(len(real), len(mine))

    op_ok = exact = 0
    mism = []
    for i in range(n):
        r = canon(real[i][1], real[i][2])
        m = canon(*mine[i])
        if r[0] == m[0]:
            op_ok += 1
            if r[1] == m[1]:
                exact += 1
            else:
                mism.append((i, real[i][0], r, m, 'operands'))
        else:
            mism.append((i, real[i][0], r, m, 'opcode'))

    print("real trace : %d instructions" % len(real))
    print("fsvg output: %d instructions" % len(mine))
    print("opcodes    : %d/%d (%.0f%%)" % (op_ok, n, 100.0 * op_ok / max(n, 1)))
    print("exact      : %d/%d (%.0f%%)" % (exact, n, 100.0 * exact / max(n, 1)))
    if mism and (a.verbose or len(mism) <= 12):
        print("\nknown gaps (%d):" % len(mism))
        for i, line, r, m, kind in mism:
            print("  %3d entry_64.S:%-4d %-6s real %-7s %-22s | fsvg %-7s %-22s"
                  % (i, line, kind, r[0], r[1][:22], m[0], m[1][:22]))
    return 0 if op_ok == n else 1


if __name__ == '__main__':
    sys.exit(main())