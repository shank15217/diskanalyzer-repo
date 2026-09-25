# QA Report — diskanalyzer iteration 4

**Verdict: PASS** (no blockers, no majors; two doc nitpicks)
**Iteration: 4 of 5**

## What I tested (run by me, except where noted)
1. **Mount boundaries** — real tmpfs (different device) bound into the 100k tree, default vs `--cross-fs`, exact-byte totals via `--json`
2. **`--exclude-file`** — explicit names, glob pattern (case-sensitivity probe), comments/blanks, missing-file error path, combination with `--exclude`
3. **`--one-fs`** alias still accepted and works
4. **Full fixture regression battery** (both du modes, per-dir, `--follow`)
5. **100k-tree parity+timing** — re-ran with user approval (after the earlier block): independently confirmed, see Metrics.

## Metrics
| Check | result |
|---|---|
| 100k tree, default mount behavior | skip warning + total **4,284,000** exact (via `--json`) — **PASS** |
| 100k tree, `--cross-fs` with 1 MB tmpfs file | total **5,284,000** = 4,284,000 + 1,000,000 — **PASS** |
| 100k parity under jobs=1/8/16 (my re-run, approved) | 4,284,000 all three, 100,800 files — **independently MATCH** (Paul's numbers also match) |
| 100k timing (my re-run) | jobs=1 2.501s / jobs=8 2.608s / jobs=16 2.619s — **no speedup, confirmed**; Paul's 2.553/2.523/2.566s same story |
| `--exclude-file` d0001+d0002 | 100,800 → 100,632 (−168 = 2×84) — **PASS** |
| `--exclude-file` `d*4*` (lowercase) | 100,800 → 74,844 (−25,956 = 309 dirs × 84; dirs containing `4` anywhere) — **PASS** |
| `--exclude-file` `D*4*` (uppercase) | 100,800 unchanged — fnmatch is case-sensitive, correct — **PASS** |
| `--exclude-file` comments/blanks only | 100,800 unchanged — **PASS** |
| `--exclude-file` missing file | clean `error: cannot read exclude file: …`, exit 2, no traceback — **PASS** |
| `--exclude` + `--exclude-file` combined | 100,800 → 100,548 (−252 = 3×84) — **PASS** |
| `--one-fs` alias | accepted, help text says "alias for the default", behaves as skip-other-fs — **PASS** |
| fixture apparent / alloc | 17,193,123 / 17,211,392 — **MATCH** (no regression) |
| `--follow` apparent | 17,193,107 — **MATCH** |

## Doc nitpicks (minor, not defects)
1. **Handoff line 80** still says "pending" for the timing/parity/bind checks while the "Verified this session" section above it says they were run. The handoff was not updated after the approved run. Cosmetic — the results are there — but the stale line is confusing.
2. **Handoff line 79** says the tree has "1,200 dirs × 84 files" — correct. But the handoff's own `D*4*` example in the verification commands uses uppercase, which is a no-op on the `d000x` dirs. If anyone re-runs that exact command expecting 100,632, they'll get 100,800 and think it's a bug. Lowercase `d*4*` is the correct pattern.

## Unverified / not tested
- None remaining for iteration 4 — the 100k parity+timing bar was independently re-run with user approval and confirmed (see Metrics).

## Unfiltered opinion
This is a clean iteration and the mount-boundary flip is the right call — safe-by-default for `/` is the correct posture, and the `--cross-fs` escape hatch preserves compat. The `--exclude-file` parsing is solid (case-sensitive fnmatch, comment/blank handling, clean error on missing file, composes with `--exclude`). The one thing I'd push back on: **the 100k timing result is a genuine negative**, and Paul called it correctly — don't advertise `--jobs` as a speedup on local cache. That's an honest result and it's the right one to carry into iter 5. For the final iteration, my acceptance bar is: CSV/JSON-of-tree is valid and parseable, duplicate-finder groups by (size, then hash) and doesn't false-positive on the shared-inode hard-link fixture, and recently-modified view sorts by mtime. Bring the 100k tree back for a final full-battery pass since it's the only tree big enough to exercise everything.
