#!/usr/bin/env python3
"""
export-trace.py -- regenerate the verification fixture from the full trace.

verify-trace.py compares the FSVG output against a recorded kernel trace. The
full trace is 312 KB and belongs to the cpu-3d project (it observes a running
kernel); this repo only ever compares one file's instructions, so it carries a
7 KB extract instead of pointing at another project's path.

Keeping the extraction as a script rather than a one-off command is what makes
the fixture's provenance checkable: anyone can re-derive it and see it is the
real trace's entry_64.S instructions, unedited, nops and all. The nops matter --
verify-trace filters them, and a fixture that arrived pre-filtered would hide
whether that filter works.

    python3 tools/export-trace.py [path/to/trace.json]
"""
import json
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else '/root/gh-cpu3d/src/trace.json'
ENTRY = 'arch/x86/entry/entry_64.S'
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    'trace-entry_64.json')

src = json.load(open(SRC))
meta = src.get('meta', {})
steps = [x for x in src['steps'] if x['file'].endswith(ENTRY)]
if not steps:
    sys.exit('%s contains no instructions from %s' % (SRC, ENTRY))

fixture = {
    "meta": {
        "what": "The instructions a real Linux kernel executed on a syscall, "
                "restricted to %s." % ENTRY,
        "derived_from": "%s (%d traced steps in total, %d in this file)"
                        % (SRC, len(src['steps']), len(steps)),
        "why_subset": "Only this file's instructions are compared, so the "
                      "subset is equivalent to the full trace for this check.",
        "regenerate_with": "python3 tools/export-trace.py <trace.json>",
        "source_kernel": meta.get('kernel') or meta.get('uname') or meta,
    },
    "steps": [{"file": x['file'], "line": x['line'], "insn": x['insn']}
              for x in steps],
}
with open(DEST, 'w') as f:
    json.dump(fixture, f, indent=1)
print("wrote %s (%d bytes, %d steps)" % (DEST, os.path.getsize(DEST), len(steps)))
