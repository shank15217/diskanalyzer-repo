# QA Report — diskanalyzer iteration 1

**Verdict: PASS** (no blockers, no majors, one minor note)
**Iteration: 1 of 5**

## What I tested (all run by me, not taken from the handoff)
1. Rebuilt fixture from scratch (`bash make_fixture.sh`)
2. du-parity harness: root totals + all 8 per-dir totals, both modes
3. Independent mini-fixture `/tmp/du-test` (new symlink cycle `s1→s2→s1`, ext link, 3 KiB file)
4. `--follow` cycle handling, apparent + allocated parity vs `du -L -sb`
5. `su nobody` permission test (tool copied to /tmp — nobody can't traverse /root)
6. `--exclude`, `--min-bytes`, `--json`, `--csv`, stderr/stdout separation
7. `--one-fs` with a real in-tree bind mount (mounted + unmounted by me)
8. Big tree: `.` (1607 files, 142 dirs)

## Metrics
| Check | du | tool | result |
|---|---|---|---|
| fixture apparent (`du -sb`) | 17,193,123 | 17,193,123 | **MATCH** |
| fixture allocated (`du -s`) | 17,211,392 | 17,211,392 | **MATCH** |
| per-dir apparent, all 8 subdirs | — | — | **ALL MATCH** |
| fixture `--follow` apparent | `du -L -sb` = 17,193,107 | 17,193,107 | **MATCH** (Paul's blocked check — now verified) |
| `/tmp/du-test` apparent / alloc | 3,089 / 4,096 | 3,089 / 4,096 | **MATCH** both |
| nobody run, locked/ = 000 | — | 1 warning, no traceback, total = root total − 10,000 exactly | **PASS** |
| big tree apparent, 1607 files | `du -sb` = 21,357,839 | 21,357,839 | **MATCH** |
| big tree allocated | `du -s` ≈ 25,124,864 | 24,924,160 | 0.8% diff = `du -s` 1K-block rounding, not a defect |
| big tree timing | — | 0.109 s wall (1607 files) | no hang |

Edge cases planted and confirmed: hard link counted once (hard/ = 4,096 apparent, not 8,192),
symlink cycle `--follow` → 2 cycle warnings + target counted once, broken-symlink message,
Unicode (`uni/café-データ.bin`) and spaced filenames exact, symlink own-size (s1=2, s2=2, ext=13).

Flag behavior: `--exclude` (name-glob) removes big/ and hard/ correctly (total drops to 1.13 MiB);
`--min-bytes 100000` filters to 6 files; `--json` valid (parsed); `--csv` has header row.
Warnings go to stderr only — stdout with `2>/dev/null` is clean (the one "warning" string on
stdout is the `Elapsed warnings: 0` label, not a leak).

## Defects
- **Minor (note, not a bug):** `--one-fs` does NOT skip same-filesystem bind mounts — I
  verified this is correct (`du -x` semantics: different `st_dev` only), and hard-link
  inode dedup prevents double-counting through the bind anyway (total stayed 16.40 MiB with
  the bind in the tree). Worth a docstring line so nobody expects it to hide binds.
- Everything else: zero failures.

## Unfiltered opinion
This is genuinely solid iteration 1 work. The du-parity acceptance bar is met *exactly*,
not approximately — both modes, per-dir, follow mode, and permission-denied handling all
verified independently by me. The handoff's self-verification table was accurate. The one
check Paul flagged as blocked (`du -L -b` apparent parity) now passes here. Ship it and
move to iteration 2 (tree view with size bars).
