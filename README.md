# diskanalyzer — Howto (v1.2.1)

Single-file, stdlib-only Python (3.12+), read-only disk-usage analyzer with
du-exact semantics. Canonical source: `./diskanalyzer/diskanalyzer.py`
(frozen target `diskanalyzer-1.2.1.py.orig`, git `d802015` + doc pass).
Deploy anywhere: `cp diskanalyzer.py host:~/diskanalyzer && chmod +x`.

## Everyday
```bash
diskanalyzer /                  # top dirs + top-15 files (allocated bytes == du -s)
diskanalyzer --mounts           # which filesystem is full? (◀ FULL at ≥90%)
diskanalyzer /usr --tree        # directory tree with size bars (--depth N to trim)
diskanalyzer /usr --by-type     # space by file extension (.so, .fd, ...)
diskanalyzer /export --by-owner # space by uid/gid (unmapped ids shown as ?(id))
diskanalyzer ~ --recent-days 7  # biggest files touched this week
diskanalyzer /var --duplicates --min-size 10485760   # dupes + hard links (--min-size is BYTES, no 10M suffix)
diskanalyzer /var --interactive # ncdu-style: j/k or arrows, Enter descend, h up, q quit
```

## Watch a tree over time
```bash
diskanalyzer /usr --snapshot usr-$(date +%F).json      # baseline (cron-able weekly)
diskanalyzer --diff usr-2026-09-01.json usr-2026-09-22.json   # what grew/shrank
```
Snapshots are 0600, refuse to overwrite (use --force), and are per-file
relpath-keyed: the diff lists grown/shrunk dirs and added/removed/changed files.

## Exports
```bash
diskanalyzer /usr --json > scan.json      # totals + every file + dir rollup
diskanalyzer /usr --csv > files.csv       # caveat: =+-@-prefixed filenames are
                                          # spreadsheet formula prefixes; import
                                          # via the wizard, don't double-click
diskanalyzer /usr --tree-csv > dirs.csv   # per-directory rollup only
```

## Semantics you should know
- Default = **allocated** bytes (`du -s`); `--apparent` = `du -sb`. Directory
  inodes are excluded (the tool-vs-du byte delta is exactly `find ROOT -type d
  -printf %b`×512 — measured 221,655,040 B on labhost /usr).
- Hard links counted once; other-filesystem mounts skipped by default
  (`--cross-fs` opts in); `--follow` = `du -L` and can escape the scan root
  through upward symlinks, by design.
- `--jobs N>1` is currently SLOWER (GIL) — never use it; serial is the fast path.
- Exit codes: 0 ok, 2 clean error on stderr (bad path, bad snapshot, clobber
  refused) — never a traceback. Safe on shared hosts: no network, no
  subprocess, atomic 0600 snapshot writes that fail closed on /tmp squats.
