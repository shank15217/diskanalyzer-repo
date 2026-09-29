# diskanalyzer — Howto (v1.4.0)

Single-file, stdlib-only Python (3.12+), read-only disk-usage analyzer with
du-exact semantics. Canonical source: `./diskanalyzer.py` (frozen dev-side, see git log).
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

## Prometheus (node_exporter textfile collector)
The textfile collector parses ONLY Prometheus exposition format (`*.prom`) —
JSON/TOML files in the directory are silently ignored. Two emitters:
```bash
diskanalyzer /data --prom                    # exposition to stdout (NOT atomic
                                             # through shell '>' — prefer below)
diskanalyzer /data --prom-out /var/lib/node_exporter/diskanalyze.prom
```
`--prom-out` writes temp+rename (atomic: a scrape never reads a partial file),
mode 0644, overwrites every run without `--force` — it is regenerated
monitoring data, not a diff baseline. Periodic refresh via cron:
```
* * * * * /usr/local/bin/diskanalyzer /data --prom-out /var/lib/node_exporter/diskanalyze.prom
```
Metrics (all gauges): `diskanalyze_scan_{success,timestamp_seconds,
duration_seconds,bytes,files,directories,warnings}`, `diskanalyze_dir_bytes`
(top `--top` child rollups of the root — **view parity (v1.4)**: the default
 stdout "Top directories" list is this exact family, same flat depth-1 set,
 same `--top` cap and sort, agreeing to the byte. **`--top` does double
 duty:** it sizes the terminal list AND caps the scrape's `dir_bytes`
 series count — `--top 5` in your cron line means Prometheus only ever
 sees the 5 biggest children; a "missing directory" on a dashboard is
 usually this, not a scan bug; drill down by rerunning the
 tool on a chosen directory (du-style) or use `--tree` for the full walk),
 `diskanalyze_filesystem_bytes`
(statvfs of the root's mount). Every sample carries `path` (the scan root) so
several scan roots can coexist as several `.prom` files in one collector dir
— a colliding label set makes node_exporter drop the ENTIRE diskanalyze
gather, which is why no family is label-less. `mode` is `allocated`/`apparent`.
Failure model: a crashed run never touches the file, so alert on staleness,
e.g. `time() - diskanalyze_scan_timestamp_seconds > 2 * <refresh interval>`.

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
