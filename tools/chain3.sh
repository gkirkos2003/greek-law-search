#!/bin/bash
# sequential writers: ΦΕΚ Α΄ (parser v10) -> refts (textnorm v2) -> xref -> ΦΕΚ Β΄ -> xref
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS=1
R="nice -n 19 ionice -c3 venv/bin/python build_index.py"
$R text --sources fek_a  > logs/build_fek_a3.out 2>&1
$R refts                 > logs/build_refts.out 2>&1
$R xref                  > logs/build_xref.out 2>&1
$R text --sources fek_b  > logs/build_fek_b.out 2>&1
$R xref                  > logs/build_xref2.out 2>&1
echo chain3 done
