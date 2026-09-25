# HANDOFF diskanalyzer iteration 3 — parallel scan + live progress

**Workspace:** `./diskanalyzer/` (`diskanalyzer.py`, same path)

## What changed
1. **`--jobs N`** — N worker threads drain a shared work queue (default 1 = serial;
   `--jobs 0` = cpu count). The walk itself is now queue-based; all output is
   sorted/deterministic, so serial and parallel runs produce **identical** results.
2. **`--progress`** — live progress on stderr: `entries, entries/s, elapsed`,
   refreshed every 0.5 s, final line on completion.
3. **Thread safety (the part you'll hammer, @qaquinn):**
   - `seen_inodes` claim is now **atomic**: `_claim_inode()` does check-and-add
     under one lock, closing the old check-then-act race on hard links and
     `--follow` symlink targets.
   - `files`, `warnings`, `progress` appended under `self.lock`;
     work queue + `dirs` inserts under `self.queue_lock`.
   - Per-dir `warnings` counter and `dirs` lookup happen after insert, single-owner.

Scanner semantics are unchanged (du parity rules from iter 1–2 all stand).
Table/tree/json/csv renderers untouched — only the scan engine was replaced.

## Stress fixture (built this session, ready)
`/tmp/du-stress` — **12,481 files**: 12,000 tiny files in 120 dirs + 480 hard links
(all pointing at ONE 4 KB inode, spread 4 per dir) — a deliberate contention storm
for the inode dedup under concurrency.

## ⚠️ HONEST STATUS — verification is PENDING, not done
My verification command (du reference capture + serial-vs-jobs=1/4/8/16 comparison)
was **blocked on approval and timed out without user consent**, so I did NOT run it,
per the block instruction. I am not claiming parity for this iteration. What I do
know: the module compiles (lint-verified on write) and the serial code path is the
same logic as iter 1–2 minus the old racy check-then-act, which is now atomic.

## Exact verification commands for QA (please run; du reference first)
```bash
cd ./diskanalyzer
du -sb /tmp/du-stress > /tmp/du_ref.txt
for j in 1 4 8 16; do
  python3 diskanalyzer.py /tmp/du-stress --apparent --jobs $j --json > /tmp/stress_j$j.json
  python3 -c "import json;print('jobs=$j', json.load(open('/tmp/stress_j$j.json'))['totals']['size'])"
done
python3 - <<'EOF'
import json
du = int(open("/tmp/du_ref.txt").read().split()[0])
base_files = sorted(f["relpath"] for f in json.load(open("/tmp/stress_j1.json"))["files"])
for j in [1, 4, 8, 16]:
    o = json.load(open(f"/tmp/stress_j{j}.json"))
    files = sorted(f["relpath"] for f in o["files"])
    print(f"jobs={j}: size={o['totals']['size']} du={du} "
          f"{'MATCH' if o['totals']['size']==du else 'MISMATCH'} "
          f"files==serial:{files==base_files}")
EOF
```
Expected: all four job counts MATCH du exactly; file sets identical to serial.
Hard-link note: the 480 links share one inode → exactly **one** entry for that inode
in `files` (kind `file`, relpath whichever dir's worker claimed it first — the
relpath is nondeterministic across runs, which is why the check compares the
*set* of relpaths, and note the set itself can differ by that one entry's path;
compare `totals.size` as the hard bar and file **count** as the soft one).

Also spot-check: `--progress` on stderr only (stdout stays clean for `--json`),
final line format, and that `--jobs 1` output is byte-identical to iter-2 behavior
on the small fixture (`/tmp/diskanalyzer-fixture`, still valid).

## Regression ask (fast, since scan engine changed)
Re-run the iter-1/2 battery on `/tmp/diskanalyzer-fixture`: du parity both modes,
per-dir totals, `links/` `--follow`, `nobody` permission test, tree view — all
should be unchanged.

## Known limitations / honest notes
- Parallelism is thread-based (GIL): I/O-bound scandir/stat work still benefits,
  but don't expect linear speedup; the acceptance bar is *correctness*, speed is
  bonus (WizTree pitch is speed, but exact du parity is the contract here).
- `--jobs` with a deep single-dir tree gives no parallelism at all (one worker
  effectively) — by design, dirs are the unit of parallelism.
- The pending-verification status above is the real blocker for this handoff;
  everything else is verified-by-construction only.
