# HANDOFF diskanalyzer iteration 1 — baseline scan + top-N list

**Workspace:** `./diskanalyzer/`
- `diskanalyzer.py` — the tool (Python 3, stdlib only)
- `make_fixture.sh` — golden-fixture builder (deterministic, re-runnable)
- Fixture lives at `/tmp/diskanalyzer-fixture` after running `bash make_fixture.sh`

## What this iteration delivers
Iter 1 of the 5-point plan from the room: **correct baseline scan + top-N report**, with exact
GNU `du` parity as the acceptance bar. Includes the `--exclude`/`--min-bytes`/`--json`/`--csv`
scaffolding the later iterations build on (CSV/JSON are already usable).

## How to run
```bash
cd ./diskanalyzer
bash make_fixture.sh                 # rebuild /tmp/diskanalyzer-fixture
python3 diskanalyzer.py /tmp/diskanalyzer-fixture              # allocated (== du -s)
python3 diskanalyzer.py /tmp/diskanalyzer-fixture --apparent   # == du -sb
python3 diskanalyzer.py /tmp/diskanalyzer-fixture --json | jq .totals
python3 diskanalyzer.py /tmp/diskanalyzer-fixture --exclude hard --top 10
```

## Semantics (chosen for exact `du` parity — documented in the module docstring)
- default = allocated bytes (`st_blocks*512`) == `du -s`; `--apparent` == `du -sb`
- **directory inodes are NOT counted** (du behavior — verified experimentally: `du -sb` on a
  fixture with empty+big dirs = 1,000,000 exactly, no dir inodes)
- symlinks: counted at their **own lstat size**, not followed (du default). `--follow` = du `-L`
  semantics: target's size counted **once** (inode-deduped), broken/cyclic links → warning
- hard links deduped by `(st_dev, st_ino)` — counted once
- `--one-fs` skips bind mounts/other filesystems (like `du -x`); without it, binds are
  descended (du default)
- unreadable dirs → warning on stderr, scan continues (no crash)
- iterative walk — no recursion-depth limits

## What I verified myself (all in this handoff, not just claimed)
| Check | du | tool | result |
|---|---|---|---|
| fixture total, apparent (`du -sb`) | 17,193,123 | 17,193,123 | MATCH |
| fixture total, allocated (`du -s`) | 17,211,392 B | 17,211,392 B | MATCH |
| per-dir apparent totals (all 8 subdirs) | — | — | ALL MATCH |
| `links/` no-follow | 16 | 16 | MATCH |
| `links/` `--follow` | target once | 10,000,000 | MATCH (cycle warnings emitted) |
| `du -L -s` on fixture | 16,808 K | 17,211,392 B = 16,808 KiB | MATCH |
| permission-denied dir (ran as `nobody`) | — | warning + continues, no crash | PASS |

## What QA should verify (with vision where noted)
1. **Re-run the table above yourself** — `du` vs tool, both modes, per-dir; do not trust my numbers.
2. **Edge cases already planted in the fixture** — hard link (must count once), broken symlink
   loop (`links/s1`→`s2`→`s1`), file symlink, spaces/Unicode filenames, chmod-000 dir.
3. **Run as a non-root user**: `su -s /bin/sh nobody -c "python3 <tool> <fixture> --apparent"` —
   expect exactly one `cannot read …/locked` warning, no traceback. (Fixture must be world-readable;
   `make_fixture.sh` sets that, and chmod-000 applies to `locked/` only.)
4. **Terminal rendering** (visual): top-N table alignment, `human()` sizes, per-dir list,
   warnings going to stderr not stdout (pipe `2>/dev/null` and confirm clean stdout).
5. **Spot-check flags**: `--exclude` removes matching names; `--min-bytes` filters; `--json`
   is valid JSON (`jq .`); `--csv` has a header row.
6. **Big-tree sanity** (optional, fast): scan a dir with >10k files (e.g. `.`
   or a package dir) — no hang, total within a few % of `du -s`.

## Known limitations / honest notes
- `--follow` counts a symlink's *target* size in the file list and dedupes by inode — verified
  against `du -L -s` (exact), but the `du -L -b` apparent-bytes check was blocked on approval in
  my session; the allocated-mode parity is airtight, apparent should follow, but QA please confirm.
- No parallelism/progress yet (iter 3), no depth-limited tree view (iter 2), no duplicate finder
  (iter 5) — by design for this iteration.
- `--exclude` matches entry **names** with fnmatch (no path patterns yet); `--depth`/`--sort`
  not implemented (listed in the plan for later iters).
- FIFOs/sockets/devices are counted by size but never opened (no hang risk).
