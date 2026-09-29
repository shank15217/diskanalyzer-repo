# HANDOFF diskanalyzer v1.4.0 (iteration 11) — view parity: stdout Top dirs == --prom dir_bytes

User rule: the default listing and the Prometheus view must show the same
info so they can be reconciled; drill-down = rerun the tool on a directory.

## Change (root cause: two different selections existed)
- New `_depth1_children(s, top)` in `diskanalyzer.py` — single source of
  truth: dirs directly under root, sort (-total, relpath), cap `--top`.
- `emit_table` "Top directories" now renders that list (was: global top-10
  dirs including deep descendants = the heaviest-chain illusion). Old walk
  DROPPED per QA (deep inspection stays covered by `--tree`).
- `prom_lines` dir_bytes family calls the same helper.
- Header/HOWTO v1.4.0; `__version__` 1.4.0.

## Verified pre-handoff (real runs)
- `parity_check.py` (new, ships with handoff): 3 roots (fixture,
  prom-edge, /usr) × 6 combos (default, --top 5, --top 1, --top 0,
  --apparent, --jobs 4) — stdout rows == expected depth-1 rollups from
  --json AND == raw escaped dir_bytes (label, exact bytes). **0 fails.**
- `--top 0` emits no dir_bytes samples and an empty section (both asserted).
- Grammar: fixture 18/18, prom-edge 13/13 via prom_validate.
- jobs1-vs-jobs4 --json diff: only elapsed_s.
- `--version` → 1.4.0. Freeze `diskanalyzer-1.4.0.py.orig` re-taken in the
  SAME commit as the change: **git `c881808`**, post-commit diff empty.

## Known limitations (honest)
- Parity harness compares prom labels against the tool's OWN
  `_prom_escape_label` — escape correctness itself still rests on the
  v13 live-exporter tests, not on this harness (partially self-referential
  by design; comparing bytes is the load-bearing half).
- stdout shows `human()` rendering; byte-exactness is verified via the
  harness (human(bytes) equality), not printed digits.
- `--min-bytes` still does not filter the Top directories section (it never
  did); only files. Flag if QA considers that a UX gap for v1.5.

## QA gate suggested (per room promise)
Run `python3 parity_check.py` (rc 0) on fixture, /usr, adversarial tree;
eyeball one `--prom` vs stdout side-by-side; `--tree` still renders.
