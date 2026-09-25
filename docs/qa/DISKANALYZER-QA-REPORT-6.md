# DISKANALYZER QA REPORT — iteration 6 (v1.0 feature acceptance)

**Verdict:** **FAIL** — one regression crash on an old flag (`--follow`), one feature
that contradicts its own documented rule (dotfiles in `--by-type`). Both are small,
fixable in minutes; everything else on the five-feature list passes.

**Artifact:** `diskanalyzer/diskanalyzer.py` (978 lines, `--version` → `diskanalyze 1.0` ✓)
**Env:** python3.14, bench-box. Planted fixture `/tmp/qa-v1/fix2`: uppercase ext
(`a.LOG`), double ext (`archive.tar.gz`), bare dotfile (`.bashrc`), `.config.json`,
no-ext files, hard-linked pair (`data.bin`/`data_hardlink.bin`, uid 1234/gid 5432),
unmapped uid 65111/gid 65112, file+dir symlinks, empty dir, subdir tree.

## What I tested and how
- Independent Python oracle over the fixture computing expected per-ext / per-uid /
  per-gid allocated bytes (same `lstrip` rule as handoff), compared to tool output.
- `--snapshot` → mutate tree 4 ways (grow, delete, add file, add dir) → `--diff`;
  identity diff (`s1` vs `s1`); bogus/missing snapshot files; one-arg `--diff`;
  cross-root diff; alloc-vs-apparent mode-mismatch warning.
- `--mounts` vs `df -B1` on this box.
- `--interactive` driven through a real `pty.fork` (render check, j/Enter/q), tiny
  terminal (COLUMNS=20 LINES=3), and non-tty (rc + message).
- Regression sweep of 14 old flag combos + `--jobs 1` vs `--jobs 8` JSON determinism.

## Metrics
| Check | Result |
|---|---|
| `--version` = `diskanalyze 1.0` | PASS |
| `--diff` 4-way mutation: +212.00 KiB net, added×2, removed×1, changed×1, dir deltas | PASS (exact, both directions) |
| `--diff s1 s1` | PASS — `added 0 removed 0 changed 0`, no zero-delta rows |
| `--diff` bad-file rc 2 / missing rc 2 / one-arg rc 2 | PASS |
| mode mismatch (alloc vs apparent snapshot) warning | PASS |
| cross-root diff (subdir snapshot vs root snapshot) | PASS, header shows both roots |
| `--by-type` ext rollup vs oracle (case-fold, `.tar.gz`→`.gz`, sizes) | PASS on values |
| dotfile rule: `.bashrc` should classify as `.bashrc` | **FAIL — lands in `(none)`** |
| `--by-owner` uid bytes: root 135,168 B / 1234 61,440 B / 65111 4,096 B | PASS (byte-exact vs oracle) |
| `--by-owner` gid bytes + unmapped `? (5432)` / `? (65112)` display | PASS |
| hard-link inode counted once in all rollups (du parity, totals 200,704 B) | PASS |
| `--mounts` vs `df` (3 real fs, pseudo/tmpfs filtered, Use% ±rounding) | PASS |
| `--interactive` pty: title renders, descend, q-quit rc 0, no traceback | PASS |
| `--interactive` COLUMNS=20 and non-tty: no traceback, rc 2 + rerun hint (non-tty) | PASS |
| `--min-bytes` + `--apparent` interaction with `--by-type` | PASS |
| `--exclude '*.log'` honored in scan and reflected in `--by-type` | PASS |
| jobs 1 vs 8 JSON determinism | PASS (identical after stripping `elapsed_s`) |
| old-flag regression sweep 14 combos | **13/14 — `--follow` CRASHES** |

## Defects

### D1 — BLOCKER (regression): `--follow` crashes with KeyError on any tree containing a symlink to a directory
Repro (minimal):
```
mkdir -p /tmp/minfl && echo a > /tmp/minfl/f.txt && ln -s . /tmp/minfl/dlink
python3 diskanalyzer.py /tmp/minfl --follow
# KeyError: 'dlink'   (diskanalyzer.py line 149, worker(): d = self.dirs[relpath])
```
Crashes in table mode and `--json`; exit 1 with traceback. Any real home dir has
symlinked dirs, so `--follow` is dead in practice. Almost certainly collateral from
iter-6's uid/gid insertion ("all three creation sites") — the followed-dir path in
`worker()` registers a child walk against a `self.dirs` entry that is never created.
Paul's 19/19 regression pass evidently never exercised `--follow` against a fixture
with a symlinked **directory** (only symlinked files). Note this is pre-existing-or-
introduced either way; QA iter 5 didn't flag it, but v1.0 label says otherwise —
a crash on a documented flag is not a 1.0.

### D2 — MAJOR (feature vs. docs): `--by-type` dotfile rule inverted
`emit_by_type` (line 566): `if "." in name.lstrip("."): ext = ... else: ext = "(none)"`.
For `.bashrc`, `lstrip(".")` → `bashrc`, no dot → **(none)**. The docstring, the
handoff, and the QA-parity note all promise `.bashrc` → `.bashrc`. Verified live on
a 3-file micro fixture: only `.txt` and `(none)` appear. `.config.json` → `.json`
works (that case passes the dot-check). Fix: handle the "starts with dot and only
one dot" branch explicitly.

### D3 — MINOR (cosmetic): symlinks inflate file counts in `--by-type` / `--by-owner`
Both rollups include symlinks (0 allocated bytes) in the `N files` count and classify
them under `(none)`; owner rollups attribute them to the link owner. Byte totals are
correct (links are 0 bytes; my parity checks absorbed them). Either exclude them from
the rollups (they were excluded from... actually they're in the scan's file count too)
or label the count "entries". Design-choice call for Paul; not blocking.

### D4 — OBSERVATION (not a defect): truncate not seen in `--diff` (allocated mode)
Truncating a 2.5 KiB file to 200 B showed `+0 B` because XFS hadn't released blocks
(`st_blocks` stayed 8 at snapshot time). Correct tool behavior for allocated mode;
`--apparent` snapshots would catch it. Worth one line in the handoff's known limits.

## Unfiltered opinion
Four of the five features are genuinely good: the snapshot/diff is the standout —
exact deltas, clean error paths, identity-diff renders sensibly; by-owner is
byte-exact against my independent oracle including unmapped ids; mounts and
interactive behave, including the hostile-terminal paths. But a v1.0 tag with a
known crash on `--follow` and a headline rule in `--by-type` doing the literal
opposite of its own docstring is shipping with the hood loose. The 19/19 "regression
pass" missed a crashing flag, which tells me the regression set has no
symlinked-directory case — add one permanently. Fix D1+D2, re-handoff, I re-run the
fixture suite (it's scripted, ~2 min) and I expect PASS.

**Iteration:** 6 of 5-iteration guidance — per room rules I'm flagging this to the
human after the next fix cycle rather than grinding more rounds unilaterally.
