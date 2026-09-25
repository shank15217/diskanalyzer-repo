# DISKANALYZER QA REPORT — iteration 7 (v1.1 re-verification)

**Verdict:** **PASS** — D1 and D2 both fixed and independently re-verified. No new
defects found. v1.1 (`diskanalyze 1.1`) accepted.

**Env:** rebuilt `/tmp/qa-v1/fix2` pristine (same planted fixture as iter 6:
uppercase ext, `.tar.gz`, `.bashrc`, `.config.json`, no-ext, hard-linked uid-1234
pair, unmapped uid 65111/gid 65112, file+dir symlinks), plus `minfl` (`ln -s .`)
and `cyc` (`ln -s ..`) repros from iter 6.

## D1 — `--follow` crash: CLOSED
| Repro | Result |
|---|---|
| `fix2 --follow --json` (the `sub1/dirlink -> ../sub2` shape from my report) | rc 0, clean |
| `minfl --follow` (`ln -s .` self-loop) | rc 0, "skipping symlink loop" warning, no clobber (totals: plain = follow, files 1; symlink correctly reclassified as skipped dirlink) |
| `cyc --follow` (`ln -s ..` escape cycle) | rc 0, loop warning, no traceback; descends `up/` once, does not recurse |
| 5 follow combos (table/json/tree/jobs=4/apparent) | all rc 0 |
| follow `--jobs 1` vs `--jobs 4` JSON | byte-identical (timing keys stripped) |

## D2 — `--by-type` dotfile rule: CLOSED
Live output on rebuilt fixture matches the new-rule oracle exactly, all 7 classes:
`.bashrc`→**`.bashrc` (4096 B)** ✓, `.config.json`→`.json` ✓, `a.LOG`→`.log`
(case-fold, 2 files 8 KiB) ✓, `archive.tar.gz`→`.gz` ✓, `noext`→`(none)` (bytes
8 KiB = 2 real files; count shows 4 incl. the 2 symlinks — accepted D3 design call,
bytes remain exact) ✓, `.txt`/`.bin` byte-exact vs oracle ✓.

## Regression sweep
21/21 flag combos rc-clean with no tracebacks, including `--interactive` on non-tty
(rc 2 + "rerun without --interactive", not a crash). Snapshot→4-way-mutate→diff
re-run: net **+212.00 KiB**, added 2 / removed 1 / changed 1 — identical to iter-6
baseline. `--by-owner` byte-exact again (root 132 KiB / ?1234 60 KiB / ?65111 4 KiB;
gid rows incl. unmapped 5432/65112). `--mounts` rc 0. `--version` → `diskanalyze 1.1` ✓.

## Notes (non-blocking, no action required)
- D3 (symlinks counted in rollup file-counts, 0 bytes) accepted as documented design.
- D4 (truncate invisible in alloc-mode diff) stands as a known limit; one line in
  the handoff limits section would close it for good.

**Iteration:** 7. Per the 5-cycle rule this loop is done from my side — verdict is
PASS; further changes are new-minor territory, take them as 1.2+ without re-opening
this acceptance.
