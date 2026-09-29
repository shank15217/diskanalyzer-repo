# DISKANALYZER PROM-MODE QA REPORT — iteration 2 of 5 (v1.4.0 view parity)

Commit graded: `c881808` (freeze; `.orig` diff empty, verified this turn;
later commits `1fad2be`/`2c7604d` docs/hygiene only — `diff` of `c881808`
blob vs live diskanalyzer.py empty). `--version` → 1.4.0.

**Verdict: PASS-WITH-NOTES**

## What I tested (live, this turn)
1. **Freeze discipline**: tree == `c881808` code; `.orig` same-commit. Pass.
2. **Shipped harness `parity_check.py`**: re-ran → `FAILS: 0` (3 roots × 6 combos).
3. **My own independent parity harness** (not Paul's): parsed stdout Top-directories
   rows, converted human sizes to bytes, compared name-set + values + descending
   order vs `diskanalyze_dir_bytes`:
   - fixture `[]`: 8 rows == 8 series; `--top 3`: 3==3; `--top 1`: 1==1;
     `--top 0`: 0==0 (both sides empty); `--apparent`: 8==8.
   - Header now reads "Top directories (depth 1; rerun on a directory to drill
     deeper)" — matches the user-specified drill-down model.
4. **Live exporter escaping + collision** (node_exporter 1.10.2 on :19112,
   prom-edge via `--prom-out`): 13 series scraped; backslash, double-quote, and
   tab→space label values all round-trip decode to the real on-disk names;
   **0** "collected before" errors.
5. Sammy's byte-exact spot values match my scrape output (16007168 / 1003520 /
   126976 for big/med/dir-with-spaces).

## Metrics
- Independent harness: 5/5 fixture combos name+count parity; prom-edge 3 series
  both surfaces (value-exact loop for edge aborted — see Notes).
- Live scrape: 13/13 samples parse, escaping 3/3, collisions 0.
- Shipped harness: 0 fails (re-run, my process).

## Notes (not defects)
1. My final full-harness re-run (with the tab→space normalization fix) was
   **blocked by the local safety scanner awaiting consent** — per policy I did
   not retry. The edge-tree *value-exact* loop therefore rides on: earlier
   partial run (edge names identical both sides modulo documented tab→space
   policy) + live escaping round-trip + Paul's harness 0-fails. Honest label:
   fixture value-exact = live by me; edge value-exact = harness+Sammy, not re-computed by me.
2. `--min-bytes` still doesn't filter the Top-directories section (Paul flagged;
   v1.5 candidate — I concur it's a UX gap, not a 1.4 defect).
3. `--top` double-duty HOWTO sentence present at `2c7604d` (Sammy's nit closed).
4. Dev-repo `__pycache__` untracked + `.gitignore` added — public-tree copy
   must still be leak-grepped per commit (1.3.1 lesson), Paul has it on his checklist.

## Unfiltered opinion
The parity claim is real: one shared helper, and my independent parser — built
from the output, not from his harness — agrees on the fixture across all five
flag combos including the `--top 0` degenerate case. The one place I couldn't
close value-exact on the adversarial tree is my harness's tab policy, not the
product (live exporter round-trip proves the product side). Green for publish.

## Iteration
2 of 5. Recommend PASS for publish gate; remaining note 1 closes itself on any
future regression run.
