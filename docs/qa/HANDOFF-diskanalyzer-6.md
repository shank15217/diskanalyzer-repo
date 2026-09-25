# HANDOFF diskanalyzer v1.0 (iteration 6)

## What changed
`diskanalyzer/diskanalyzer.py` — five new features per the agreed shortlist, plus
version metadata. Now ~980 lines, stdlib-only. `__version__ = "1.0"` at module top;
`--version` prints `diskanalyze 1.0`. Future revisions bump .1.

## New flags (v1.0)

1. **`--snapshot FILE` / `--diff OLD NEW`** — snapshot is JSON
   (`diskanalyze-snapshot/1` format tag, root, mode, totals, per-dir rollups,
   per-file sizes keyed by relpath). `--diff` takes TWO snapshot paths
   (no live scan; `path` positional not consumed): net change, top dirs by
   |change|, file added/removed/changed lists with old→new sizes.
   Wrong-format file → exit 2 with clear error. Mode mismatch → stderr warning.
2. **`--by-type`** — extension rollup (bytes + count + % per ext). Ext is
   lowercased last-suffix; no-extension → `(none)`; dotfile `.bashrc` keeps
   `.bashrc` (lstrip trick). Honors `--min-bytes`, `--apparent`.
3. **`--by-owner`** — bytes per uid AND per gid with pwd/grp name resolution
   (`? (uid)` when unmapped). Scan records `uid`/`gid` on every FileRec now
   (all three creation sites: plain-file, own-size symlink, followed symlink).
4. **`--mounts`** — no scan; parses /proc/mounts, statvfs each real mount
   (block devs, nfs/smb/ceph/virtiofs; pseudo-fs and bind dupes skipped),
   sorted by Use% desc, `◀ FULL` flag at ≥90%. Escapes \040/\011 in mount points.
5. **`--interactive`** — curses drill-down over the finished scan:
   ↑↓/jk move, Enter/l descend, h/Backspace up, q/Esc quit; size+bar+% rows,
   `../` pseudo-entry, dirs bold, header shows root/title/total. Runs AFTER the
   scan so `--snapshot` can still be combined. Non-tty/small-terminal failure →
   exit 2 with "rerun without --interactive" hint (no traceback).
6. **`--version`** + versioning scheme in module docstring.

## Verified pre-handoff (this box, python3.14)
- Fixture `fix/` with mixed exts (incl. `.LOG`/`.log` case-fold, dotfile, no-ext,
  symlink, empty), mixed uids (1234/0) and gid 5432.
- `--by-type` byte parity vs `find -printf %b` ×512 grouped by ext: **OK**
  (within human() display rounding).
- `--by-owner` parity vs `find -printf %u`: **OK** (4.94 MiB = 5,181,440 rounded).
- Snapshot diff on mutated tree (grow file, delete file, add file, add dir):
  direction correct — `--diff s1 s2` shows `+4.94 MiB` net, added `new.txt` +
  `gone_later.bin`, removed `app.log`, changed `big.bin`.
- Snapshot error paths: bogus JSON format → rc 2; missing file → rc 2;
  `--diff` with one arg → argparse error rc 2.
- `--interactive` driven headlessly through a real pty (TERM=xterm-256color):
  renders title `diskanalyze v1.0`, descend/up arrows, q quits rc 0.
- Full regression of all 19 existing flag combos: **19/19 pass**.
- `--jobs 1` vs `--jobs 4` `--json` identical except `elapsed_s` (pre-existing).

## Suggested QA planted-fixture tests (per your bar)
- snapshot diff on mutated dir tree (use the four mutation types above; also
  diff of identical snapshots = all-zero rows absent, "added 0 removed 0 changed 0").
- `--by-type` exact-byte parity vs `find`+awk on a richer tree; include uppercase
  ext, double ext (`.tar.gz` → `.gz` by design), no-ext dir.
- `--by-owner` vs `du`/`find` with mixed uids incl. an unmapped uid > 60000.
- `--mounts` sanity: every non-pseudo row in `df -b` should appear (± tmpfs policy)
  and Use% within rounding.
- `--interactive` needs a pty: `script -qec 'python3 diskanalyzer.py DIR --interactive' /dev/null`
  or the pty.fork driver pattern; check q-quit exit code and no-traceback on
  `COLUMNS=20`.
- `--version` prints `diskanalyze 1.0`.

## Known limitations (honest)
- `--diff` compares per-file by relpath only; a file moved between dirs shows as
  removed+added (no rename detection). Snapshots from different roots diff fine
  (relpath-relative) but header shows both roots.
- Snapshot stores ALL file sizes → large trees (millions of files) make multi-100MB
  JSON. Fine for /usr-scale (measured here); not streamed.
- `--interactive` bars are relative to SCAN ROOT total (like ncdu's %), not
  current-dir total. No delete (x) key — deliberate, this tool is read-only.
- `--mounts` skips overlay/tmpfs (df shows them; flag me if you want a `--all` passthrough).
- Dotfile rule: `.config.json` → `.json`; bare `.bashrc` → `.bashrc`. QA parity
  script must use the same lstrip rule or it'll false-positive.
