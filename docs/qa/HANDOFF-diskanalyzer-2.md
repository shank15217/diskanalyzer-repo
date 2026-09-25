# HANDOFF diskanalyzer iteration 2 — tree view with size bars

**Workspace:** `./diskanalyzer/` (tool unchanged in path: `diskanalyzer.py`)

## What changed since iteration 1
1. **`--tree`** — new output mode: depth-limited ASCII tree of directories with
   per-dir totals and a proportional bar (`████░░░░`, width `--bar-width`,
   default 24 cells). The terminal stand-in for a treemap.
2. **`--depth N`** — show at most N levels below the root (0 = unlimited, default).
3. **`--bar-width N`** — bar cell width (default 24).
4. **QA's iter-1 note applied** — docstring now states explicitly that `--one-fs`
   (like `du -x`) still traverses same-filesystem bind mounts (only a different
   `st_dev` is skipped); inode dedup prevents double-counting.

Scanner/aggregation logic is **untouched** — the `--tree` branch reuses the exact
iter-1 scan, so all du-parity results from QA report 1 still hold. The iter-1
table mode, `--json`, and `--csv` are unchanged.

## How to run
```bash
cd ./diskanalyzer
bash make_fixture.sh                      # (fixture may already exist)
python3 diskanalyzer.py /tmp/diskanalyzer-fixture --apparent --tree
python3 diskanalyzer.py /tmp/deep --apparent --tree --depth 2   # (or any deep tree)
```

## Rendering rules (for QA to check)
- Root line first: `<root>/  <total>  <full bar>`.
- Children of every visible dir are **sorted by total, descending** (ties: name).
- Bar length = `round(total/root_total * bar_width)`, min 1 for non-zero dirs;
  empty dirs show **no bar** (not a single cell).
- Unreadable dirs (per-dir warnings) get a trailing `   (!unreadable)` marker;
  their total shows 0 (contents unknown) — warning text goes to **stderr**.
- Depth: root = level 0; `--depth 2` shows levels 1 and 2, not 3.

## What I verified (ran this session)
- Fixture tree: all 8 subdirs + root, bars proportional, sorted correctly
  (16.40 MiB total — same value QA verified in report 1).
- Deep fixture `/tmp/deep` (`a/b/c/leaf.bin` 500 KB + `z/top.bin` 1 KB): full tree
  nests correctly with `│   ` / `└── ` connectors; `--depth 2` stops after `b/`.
- Ran as `nobody`: `locked/` renders as `0 B  (!unreadable)`, rest of tree intact,
  no traceback. (Tool must be copied out of /root first — nobody can't traverse /root.)

**Honest note:** one regression re-check command was blocked on approval in my
session before it could complete — the tree runs above exercise the same scan
path and show the identical total, and the table/`--json` code paths were not
modified this round, but please re-run the iter-1 parity table to be safe.

## What QA should verify
1. **Iter-1 regression** — re-run the du-parity table (both modes, per-dir) to
   confirm the untouched scan still matches exactly.
2. **Tree correctness** — bar proportions (e.g. big/ ≈ 18 of 24 cells at 93%),
   size-descending order, depth limiting at N=1,2,3, `--bar-width` 8 and 40.
3. **Unreadable handling** — as `nobody`: `(!unreadable)` marker present, total
   = root − 10,000, warnings on stderr only (stdout pipe stays clean).
4. **Visual** (you have vision): box-drawing connectors align per level, no
   overlap/truncation with long names ("dir with spaces", Unicode `uni/`).
5. Edge: `--tree --exclude locked` (marker gone, dir absent) and an all-empty
   root (only root line, no crash).

## Known limitations
- Tree shows **directories only** (files appear in table mode; top files are
  already in the table output — no `--tree` + files hybrid yet).
- Bar resolution: sub-1%-dirs get 1 cell (min-1 rule) — by design.
- No live progress/parallelism (iter 3), no CSV/JSON of the *tree* (file CSV exists).
