# QA Report — diskanalyzer iteration 3

**Verdict: PASS** (no blockers, no majors; one minor perf observation)
**Iteration: 3 of 5**

## What I tested (all run by me — Paul's handoff was explicitly unverified)
1. **Parallel du-parity** on `/tmp/du-stress` (12,481 files; 480 hard links on ONE inode): jobs=1/4/8/16 vs `du -sb`, plus file-count and file-set comparison
2. **Race hammer**: 25 repeat runs at jobs=16, size+count checked each run
3. **`--jobs 0`** (cpu count = 8)
4. **`--progress`**: stdout purity with `--json`, stderr content, table-mode separation
5. **Full iter-1/2 regression battery** on `/tmp/diskanalyzer-fixture`: du parity both modes, per-dir, `--follow`
6. **`nobody` permission test**: table + tree markers, and a NEW case — jobs=8 in parallel as nobody

## Metrics
| Check | result |
|---|---|
| `du -sb /tmp/du-stress` | 575,176 B |
| jobs=1/4/8/16 size | 575,176 each — **4/4 MATCH** |
| file count all jobs | 12,001 each (12,000 tiny + 1 shared-inode entry — dedup correct under contention) |
| 25× jobs=16 repeat runs | **25/25 exact** (size + count) — race closed |
| jobs=0 (8 cpus) | 575,176 MATCH |
| jobs=4 file-set vs serial | differs by exactly 1 path: `d119/f000.bin` → `d118/hl_475` — the documented shared-inode relpath nondeterminism, nothing else |
| `--progress` + `--json` | stdout = valid JSON only; progress on stderr; final line `done: 12,121 entries in 0.37s (32,927 entries/s)`; table mode stdout stays clean — **PASS** |
| fixture apparent / alloc | 17,193,123 / 17,211,392 — **MATCH** (no regression) |
| fixture per-dir, all 8 | **ALL MATCH** |
| `--follow` apparent | 17,193,107 = iter-2 value — **MATCH** |
| nobody table | 1 warning, total 16.39 MiB, no traceback — **PASS** |
| nobody tree | `locked/ 0 B  (!unreadable)` marker — **PASS** |
| nobody jobs=8 (parallel + permission) | size 17,183,123 = root − 10,000 exactly, 1 warning — **PASS** (parallel warning path is thread-safe) |

## Minor observation (not a defect)
**jobs=8 is slower than serial on this tree**: 0.53 s vs 0.21 s wall. Thread/queue overhead dominates on a small, page-cached fixture; GIL-bound stat work doesn't parallelize. Paul's handoff predicted this ("don't expect linear speedup"). Fine for iter 3 — the contract is correctness. But for iter 4/5 I'd like a genuinely *large* tree (e.g. 100k+ files) to confirm the speedup shows up when there's actual I/O to hide, otherwise `--jobs` is dead weight.

## Unverified / not tested
- Live `\r`-refreshed progress mid-scan visually (it fits on one line with carriage returns — format confirmed from the captured stderr; the refresh cadence itself is cosmetic, not a correctness bar).

## Unfiltered opinion
This is the iteration I was most skeptical of and it held up. The atomic inode claim works: 25/25 exact runs under a deliberate 480-link-on-one-inode contention storm, and the one nondeterminism (which relpath represents the shared inode) is exactly the documented, expected kind — totals and counts are stable, which is the right contract to guarantee. The parallel+permission combination (nobody, jobs=8) also passed, which Paul hadn't tested. The progress line is cleanly separated from stdout. Ship it. For the last two iterations: iteration 4 = filtering, iteration 5 = duplicates/export — and please bring a 100k-file tree for the timing check.
