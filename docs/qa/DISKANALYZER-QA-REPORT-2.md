# QA Report — diskanalyzer iteration 2

**Verdict: PASS** (no blockers, no majors, no minors)
**Iteration: 2 of 5**

## What I tested (all run by me)
1. **Iter-1 regression** — rebuilt fixture, re-ran full du-parity table (both modes + per-dir)
2. **Tree view** on the fixture AND on my own deep fixture `/tmp/qa-deep` (`a/b/c/leaf.bin` 500 KiB + `z/top.bin` 1 KiB) — not trusting Paul's `/tmp/deep`
3. **Depth semantics** at N=1, 2 (unlimited default)
4. **Bar widths** 8, 24 (default), 40 — proportion math hand-verified
5. **All-empty root** (`/tmp/qa-empty` with one empty nested dir)
6. **`--exclude locked`** in tree mode
7. **Unreadable handling as `nobody`** (tool copied to /tmp — nobody can't traverse /root)

## Metrics
| Check | result |
|---|---|
| fixture apparent `du -sb` | 17,193,123 = tool — **MATCH** |
| fixture allocated `du -s` | 17,211,392 = tool — **MATCH** |
| per-dir apparent, all 8 subdirs | **ALL MATCH** (regression: no drift) |
| bar proportion: big/ 15.26/16.40 MiB = 93.1% | 22/24 filled cells (expected round(22.34)=22) — **MATCH** |
| bar proportion: med/ 5.78% | 1 cell (min-1 rule) — **MATCH** |
| bar width 8: z/ 0the-host% | 1 cell; width 40: 2 cells — **MATCH** |
| `--depth 1` | shows a/, z/ only — **MATCH** |
| `--depth 2` | shows a/, b/, z/; c/ (level 3) hidden — **MATCH** |
| empty dir bar | no bar at all (not 1 cell) — **MATCH spec** |
| all-empty root | root line + empty children, no bar, no crash — **PASS** |
| `--exclude locked` | dir absent from tree, total 16.39 MiB (−9.77 KiB) — **PASS** |
| `nobody` run | `locked/ 0 B  (!unreadable)` marker present; total = root − 10,000 exactly; no traceback — **PASS** |
| stderr separation in tree mode | `2>/dev/null` stdout has 0 warning lines — **PASS** |

Visual (vision check of rendered output): box-drawing connectors (`├──`/`└──`/`│`) align per
level in the deep tree; long names (`dir with spaces/`) and Unicode (`uni/`) render without
overlap or truncation; bars right-aligned after size column stay column-aligned.

## Defects
None found.

## Unfiltered opinion
Clean iteration. The scanner was genuinely untouched — the iter-1 parity table re-runs
identical, so Paul's honesty note (blocked re-check) resolved itself: no regression.
Bar math is exactly per spec, including the sub-1% min-1 rule and the no-bar-for-empty
edge. Depth semantics match the handoff's "levels 1 and 2, not 3" precisely. The iter-1
docstring note about `--one-fs` same-fs binds is in place (docstring lines 18-20).
Ship it. Iter 3 (parallel scan + live progress) is the next thing I'll be measuring —
plan to time a >10k-file tree against `du` wall-clock and verify the parallel totals
still match `du -sb` exactly (that's where thread-safety bugs will live, e.g. the
shared `seen_inodes` set).
