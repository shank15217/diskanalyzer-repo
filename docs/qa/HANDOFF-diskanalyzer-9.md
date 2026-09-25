# HANDOFF diskanalyzer v1.2.1 (iteration 9) — security polish F1–F3

> **Ship state (final verdicts: QA PASS, Sammy ship):** HEAD `499de18`, freeze
> `.orig` == live (verified post `b7e31bc` doc pass). Doc coverage is honest-
> partial: 15/38 defs per Sammy's AST count — all load-bearing paths documented
> (scan, worker, main, write_snapshot, _load_snapshot, du-parity header),
> emitters remain bare by choice. Nobody should cite this file as 100%-doc'd.
> Open 1.3 candidate: `--min-size`/`--min-bytes` human suffixes (10M) — filed,
> deliberately NOT implemented post-verdict.

> **F4 follow-up (same round):** foreign-pre-existing `.tmp` is no longer
> unlinked — `os.open` failure exits rc 2 without touching the tmp name
> (verified: PRESEEDED survives, target stays PRIOR, rc 2). Freeze
> `diskanalyzer-1.2.1.py.orig` re-taken in the SAME commit as the fix:
> **git `d802015`** (`diff .orig live` = empty, checked post-commit).
> Full 28-combo functional sweep re-run post-F4: 28/28, jobs determinism holds.

All three findings from DISKANALYZER-QA-SECURITY-1.md fixed; `--version` → `diskanalyze 1.2.1`; frozen target `diskanalyzer-1.2.1.py.orig` + git `694c3a6`.

- **F1**: `write_snapshot` refuses existing target at rc 2 ("use --force");
  new `--force` flag overrides; write is now `O_CREAT|O_EXCL` temp +
  `os.replace` (atomic; failed write never corrupts the prior baseline;
  no .tmp residue verified).
- **F2**: `--csv` help text now names the =+-@ spreadsheet-formula caveat.
  Deliberate: values stay verbatim (byte-fidelity beats silent mutation).
- **F3**: snapshot file created 0600 via the O_EXCL mode (not chmod-after —
  no world-readable window).

Re-verify suggestions: clobber guard + `--force`, 0600 on first write (not just
rewrite), .tmp cleanup on injected failure (e.g. snapshot into full fs or kill
mid-write), zero-diff via fresh snapshots, then your SECURITY-1 fuzz set against
the new writer — the traversal/20k-nest snapshot inputs should now also prove
`O_EXCL` refuses pre-seeded `path.tmp` races. Sweep 28/28 here, diff/parity
unchanged (scan path untouched — diff is writer/CLI-only).
