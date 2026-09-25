# HANDOFF diskanalyzer v1.2 (iteration 8) — performance pass

> **Corrected headline (post-review, authoritative):** triple-interleaved best-of-5
> on bench-box via /tmp/qa-ab/run11.py harness: v1.0 2.32s / v1.2 1.83s / du 0.62s
> → real speedup **1.26×**, du gap ~3.0×. The "22%"/"3.7×" framings below compared
> non-comparable runs (Pi 5 big.LITTLE core placement differs per harness/cgroup)
> and are superseded. Accepted by QA (PASS) and verified by 3 independent runners.
>
> **Cross-host datapoint (labhost, 64-core aarch64, Python 3.12.3, QA-executed
> triple, interleaved best-of-5):** Sep-9 v1.0-era `/root/diskanalyzer` 12.72s /
> v1.2 11.08s / `du -s /usr` 1.31s → **1.15×** like-for-like, du gap 8.4×.
> Totals parity exact between builds AND vs `du -B512`: 377,825 files / 19,556
> symlinks / 51,204 dirs / 8,702,033,920 B — **counts exact; bytes carry the
> documented dir-inode delta**, NOT byte-equal to du (initial "equals du ×512"
> line was wrong: du ×512 = 8,923,688,960 B; delta 221,655,040 B). Delta
> verified live on labhost: `find /usr -type d -printf %b` = 432,920 blocks ×512
> = 221,655,040 B exactly — dir-inode exclusion accounts for it to the byte,
> zero residual (note: dir inodes here are 432,920 blocks, not 51,204×8 —
> some dirs span >4 KiB inodes). v1.2 is **Python 3.12-compatible**
> (CI box here is 3.14) — verified clean rc on labhost. Load ~2 during window;
> ratio holds (interleaved), absolutes are not quiet-box. labhost's 8.4× du gap
> = most Python-overhead-dominated target → biggest 1.3 perf headroom where the
> tool is actually run. Cleanup owed: delete `labhost:/tmp/dk12.py` after user OK.

## Cross-host compatibility
Tested on labhost Python 3.12.3: all sweep-relevant paths clean (QA run); no
3.13+/3.14-only syntax or stdlib in use. Keep it that way — labhost is the
primary deployment target.

## Profile first (cProfile on real /usr, 431k entries, python3.14)
Hot spots: worker loop itself (1.32s self of 4.85s), `DirEntry.stat` (0.73s),
`os.path.join` (0.60s), `_excluded` fnmatch-any (0.52s), `os.path.dirname` in
post-scan aggregation (0.49s), per-entry lock churn (341k `_thread.lock.__exit__`).
Confirmed QA's GIL diagnosis: threads added coordination, not parallelism.

## Changes (behavior-preserving)
1. **Reordered type checks to use `DirEntry` C-level predicates**:
   `ent.is_symlink()` / `ent.is_dir(follow_symlinks=False)` (d_type from readdir,
   zero syscalls for most entries) replace lstat-then-test. `stat()` is now
   called per-kind only (symlinks still lstat for their own size; dirs only
   when `--one-fs` needs st_dev; regular files need it for size anyway).
2. **No `os.path.join` / `_get_sep` in the hot loop**: relpath built by direct
   `relpath + "/" + name`; `full = ent.path` (C attribute).
3. **Exclusion fast-path**: `if excludes and self._excluded(...)` — the fnmatch
   loop is skipped entirely when no `--exclude` given (the common case).
4. **Lock churn**: one lock acquisition per *new* regular file (inode check +
   set-add + record built+appended inside it — was 2 acquisitions), progress
   counter batched per-directory instead of per-entry.
5. **Aggregation rewrite**: per-dir file bytes accumulated by the owning worker
   during the walk into `DirRec.total`; the post-scan pass now only adds
   children totals — the 431k-iteration `os.path.dirname` re-grouping is gone.
6. **uid/gid tracking is opt-in** (`track_owner`, set only for `--by-owner`);
   default scans store -1. `--json` does NOT emit uid/gid (verified against 1.1
   emitter), so it stays on the cheap path.
7. **`--jobs` honesty**: help text now states >1 is currently slower (GIL-bound)
   with measured numbers — per QA's "no bandaid" ruling. Not removed (would
   break the flag contract for scripts); candidate for process-pool redesign.

## Measured (this box, 8 cores, warm page cache, best of 3, /usr 377k files)
| build | serial | jobs4 | jobs8 | du -s |
|---|---|---|---|---|
| 1.1 (pre) | 2.34s | 6.55s | — | 0.62s |
| 1.2 | **1.83s** | 5.40s | 6.61s | 0.64s |
≈ **22% faster serial**; gap to du now ~2.9× (was 3.8×). Remaining floor is
CPython per-entry overhead (stat syscalls + record allocation); closing to du
would need an os.scandir-free or C-extension rewrite — out of scope for .2.

## Verification
- Sweep 31/31 rc-clean (incl. 3 D1 repros, 5 `--follow` combos, all v1.0 features).
- `--json` stable across runs (byte-identical minus `elapsed_s`); jobs1-vs-4
  determinism still holds.
- **du exact parity both modes**: fixture `du -sx -B1` = 4,222,976 = tool
  (alloc); apparent = 4,212,213 = tool. QA's own fixture fix2 re-scanned:
  417,792 = live du (their s1.json baseline is stale — fixture was mutated by
  their own diff tests since the snapshot).
- by-type / by-owner oracle parity re-run: pass; no uid=-1 leak on the
  tracking path.

## Honest caveat on the "byte-identical vs 1.1 build" bar
There is **no frozen 1.1 source** on this box (no git repo; QA's /tmp/qa-v1
holds fixtures/snapshots, not source). My parity evidence is: du-exact totals
both modes + snapshot-format compatibility + 31-combo sweep. For a true A/B,
freeze the 1.2 source first, then future revisions can diff against it. If
@qaquinn kept a 1.1 copy anywhere, an A/B `--json` on a frozen fixture is the
missing piece and I'll run it immediately.

Version: `--version` → `diskanalyze 1.2`.
