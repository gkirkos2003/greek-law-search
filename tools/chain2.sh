#!/bin/bash
# wait for the running caselaw build (pid $1), then rebuild ΦΕΚ Α΄ (parser v10), xref, ΦΕΚ Β΄, xref
cd "$(dirname "$0")/.."
while kill -0 "$1" 2>/dev/null; do sleep 30; done
export OMP_NUM_THREADS=1
R="nice -n 19 ionice -c3 venv/bin/python build_index.py"
$R text --sources fek_a  > logs/build_fek_a3.out 2>&1
$R xref                  > logs/build_xref.out 2>&1
$R text --sources fek_b  > logs/build_fek_b.out 2>&1
$R xref                  > logs/build_xref2.out 2>&1
echo chain2 done
