#!/bin/bash
# sequential text builds (one writer at a time), all niced
cd "$(dirname "$0")/.."
export OMP_NUM_THREADS=1
R="nice -n 19 ionice -c3 venv/bin/python build_index.py"
$R text --sources fek_a                                   > logs/build_fek_a2.out 2>&1
$R text --sources areios_pagos,elsyn,nsk,ste,eisap,coa    > logs/build_caselaw.out 2>&1
$R text --sources fek_b                                   > logs/build_fek_b.out 2>&1
$R xref                                                   > logs/build_xref.out 2>&1
echo chain done
