# QA Report — diskanalyzer iteration 5 (final)

**Verdict: PASS** (defect from first pass fixed and re-verified)
**Iteration: 5 of 5 — 5-iteration plan complete**

## What I tested (all run by me)
1. **Dedicated dupe fixture I built myself** (`/tmp/qa-dupes`): 3 identical 5 KB copies + 1 hard link into the group (4 paths / 3 inodes), 2 same-size (4 KB) different-content files, 1 unique-size file, empty files
2. **Hard-link storm** `/tmp/du-stress` (481 paths, one inode): serial vs `--jobs 16`
3. **Tree export**: `--tree-json` root_total vs `--json` file-mode total; `--tree-csv` header/rows
4. **`--recent-days`**: size-desc ordering, 40-day-old boundary file excluded at D=1 / included at D=45, `--recent-days 0` edge
5. **Full regression battery**: fixture both du modes, per-dir, `--follow`, `nobody` (table+tree), tree render

## Metrics
| Check | result |
|---|---|
| content group: 3 identical + 1 hard link | exactly 1 group, x4 paths, hash `6bb9fd6d…` — **PASS** |
| same-size (4 KB) different content | **not grouped** — **PASS** |
| unique-size file | never hashed (`sha256 computed for 5 inodes` = 3×5000B-class + 2×4096B-class inodes) — **PASS** |
| hard-link group | x2 reported separately — **PASS** |
| `--min-size 0` | empty files form a 2-path group, hash `e3b0c442…` (correct empty sha256) — **PASS** |
| storm: serial vs jobs=16 | both: 97 content groups, 12,000 inodes hashed, x481 hard-link group — **identical, thread-safe** |
| `--tree-json` root_total | 17,193,123 = `--json` totals.size — **PASS** |
| `--tree-csv` | header `relpath,total` + rows — **PASS** |
| `--recent-days 1` | 40-day-old 1 MB file excluded; 8 current files, size-desc — **PASS** |
| `--recent-days 45` | old file included, first (980 KiB) — **PASS** |
| `--recent-days 0` | 0 files — **PASS** |
| fixture apparent / alloc | 17,193,123 / 17,211,392 — **MATCH** |
| per-dir, follow, nobody (table+tree), tree render | **ALL PASS** |

## Defects
- ~~Minor — `--duplicates` "wasted space" overcounts when a group contains hard links.~~ **FIXED and re-verified by me (second pass):**
  - Mixed group, my fixture: 4 paths / 3 inodes → now `Redundant inodes: 2, wasted 9.77 KiB` = (3−1)×5000 = 10,000 B exactly (was 15,000 B).
  - Paul's case: 3 paths / 2 inodes → `Redundant inodes: 1, wasted 4.88 KiB` — exact.
  - Pure case: 3 distinct inodes, identical content → `Redundant inodes: 2, wasted 9.77 KiB` — exact.
  - `(N inodes)` annotation visible on mixed groups; storm unchanged (x481, 97 groups). **Closed.**

## Unfiltered opinion
This closes the 5-point plan. The dupe engine is correct where it matters (grouping,
hash minimization, hard-link awareness, thread-safety under the storm), and the
wasted-bytes fix I flagged is verified correct on mixed, pure, and storm cases.
Done. Feature-complete per plan; backlog items (parallel hashing, path-glob
excludes, TUI render) are uncommitted scope.
