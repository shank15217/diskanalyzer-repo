# HANDOFF diskanalyzer iteration 5 — tree export, duplicates, recent-modified

**Workspace:** `./diskanalyzer/` (`diskanalyzer.py`, same path)

## What changed (iter 5 of 5 — the last planned iteration)
1. **`--tree-csv` / `--tree-json`** — export the per-directory rollup
   (`relpath,total` rows; JSON includes root, mode, root_total, warnings).
   Deterministically sorted by relpath.
2. **`--duplicates`** — two groupings in one report:
   - *identical content*: group inodes by apparent size first, sha256 only
     size-colliding inodes (one hash per inode; every hard-link path joins
     its bucket). `--min-size` (default 1) skips empties.
   - *hard links*: every inode with ≥2 paths, listed with count.
   - Reports redundant-copy count and wasted bytes (sum of size × (count−1)).
3. **`--recent-days D`** — largest files modified within D days, sorted by
   size desc (the "big files recently modified" view from the plan).
4. `FileRec` now carries `(st_dev, st_ino)` for dupes; `track_inodes` is set
   only in `--duplicates` mode (zero overhead otherwise).

Scanner semantics, du parity, mount-boundary default, `--jobs`/`--progress` —
all unchanged from iter 4.

## Verified this session (ran it myself)
- **Dupe correctness on a planted fixture** (`/tmp/du-dupes`): 3 identical 5 KB
  files → exactly 1 group of 3, wasted = 9.77 KiB; 2 files same size (4 KB) but
  different content → **not** grouped; 1 unique-size file → **never hashed**;
  3-way hard-link inode → reported as hard-link group x3, and its content joins
  correctly. `sha256 computed for N inodes` line present (5 on the fixture).
- **Hard-link storm**: `/tmp/du-stress` (480 links, one inode) → hard-link group
  x481 (orig + 480), reported cleanly; 97 content groups among the 12,000
  size-repeating files (sizes are `(n%2000)+1` patterns, so many true content
  collisions — spot-checked group sizes: 240× per size class, as the data implies).
- **Tree export**: `--tree-json` valid JSON (root_total 17,193,123 on fixture,
  9 dirs); `--tree-csv` header + rows correct.
- **Recent view**: `--recent-days 1` on the dupe fixture → 7 files, biggest
  first; the 40-day-old file correctly excluded; boundary at exactly D days.
- **Regression**: fixture `du -sb` parity still exact (17,193,123); table mode
  unchanged.

## Suggested QA checks (your bar from the room)
1. Tree CSV/JSON parseable + totals consistent with `--json` file-mode rollup.
2. Duplicates vs the shared-inode storm: the x481 group, and that same-size/
   different-content pairs never group; `--min-size 0` behavior on empties.
3. `--recent-days` sorted by size, boundary correct; `--recent-days 0` (edge).
4. Full regression battery (fixture both modes, per-dir, `--follow`, nobody,
   tree view) — scanner untouched, should be unchanged.
5. New: run `--duplicates` under `--jobs 16` on `/tmp/du-100k` — the inode-path
   list is built under the same lock as everything else; confirm group counts
   match serial (that's the iter-5 thread-safety surface).

## Known limitations
- `--duplicates` hashes are single-threaded (scan can be parallel, hashing is
  serial) — fine at these scales; parallel hashing is a possible iter 6.
- Wasted-bytes uses apparent size (allocated would need per-inode blocks ×
  (count−1); the per-dir rollup doesn't retain that — easy if you want it).
- `--recent-days` uses the *first-seen* path's mtime for hard links (same
  inode ⇒ same mtime, so no ambiguity in practice).
- This is the final planned iteration — the tool is feature-complete per the
  5-point plan; remaining ideas (parallel hashing, path-glob excludes,
  TUI-style rendering) are backlog, not committed scope.
