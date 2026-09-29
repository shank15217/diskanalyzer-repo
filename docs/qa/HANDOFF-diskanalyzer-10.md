# HANDOFF diskanalyzer v1.3.0 (iteration 10) — Prometheus textfile-collector output

> **FIX ROUND (post Sammy review) — FROZEN.** Both findings fixed and
> verified against a REAL node_exporter 1.10.2 (`--collector.textfile.directory`):
> F1: `path` label added to `scan_success`/`scan_timestamp_seconds`/
> `scan_duration_seconds`. F1b (found while re-testing F1): `filesystem_bytes`
> ALSO collided whenever two scan roots share a mount — pre-fix exporter log
> showed 3x "was collected before with the same name and label values" per
> scrape; post-fix: **0 collisions, 31 diskanalyze series scraped clean**
> from two files (`fixture.prom` + `edge.prom`). Grammar: fixture 18/18 OK,
> prom-edge 13/13 OK, /usr 19/19 OK via `prom_validate.py` (kept Sammy's
> HELP-token fix; bad-escape now a VIOLATION line, not a traceback — negative
> fixture rc 1 as intended). jobs1-vs-jobs4 sweep diff: identical except
> timestamp/duration lines and live statvfs drift on the shared fs.
> Freeze: `diskanalyzer-1.3.0.py.orig` re-copied in commit **5e882aa**,
> `diff .orig live` empty post-commit (F4 discipline).

> **Status: NOT frozen.** `__version__` bumped to 1.3.0, feature implemented
> and smoke-tested, but the safety scanner blocked further command execution
> mid-verification (curl of promtool and pip install were denied; subsequent
> chained commands also blocked awaiting explicit user OK). Therefore:
> `.orig` freeze + git commit are PENDING — do not treat HEAD as the freeze
> point. Re-run the full verification below before freezing.

## What changed (root cause / design)

User asked for a "TOML output for node_exporter textfile collector."
**TOML is not what the textfile collector reads** — it parses only the
Prometheus *exposition format* (`*.prom`). We emit that instead (agreed in
room with QA before building).

`diskanalyzer.py`:
- New `emit_prom` / `write_prom` / `prom_lines` / `_prom_escape_label` /
  `_prom_sample` / `_mount_for` (inserted above `emit_mounts`).
- New flags in `main()`: `--prom` (exposition to stdout, redirectable) and
  `--prom-out FILE` (atomic temp+`os.replace` write, mode 0644, unique
  per-pid temp name `.<base>.tmp.<pid>` in the target dir, overwrites without
  `--force` — it's regenerated monitoring data, not a diff baseline).
  Emitter sits in the dispatch chain after `--by-owner`, before `--tree-csv`.

Metric families (all gauges, HELP+TYPE before samples):
- `diskanalyze_scan_success` (constant 1; failure = file not refreshed →
  staleness is the failure signal, per QA)
- `diskanalyze_scan_timestamp_seconds`, `diskanalyze_scan_duration_seconds`
- `diskanalyze_scan_bytes{path,mode}`, `diskanalyze_scan_files{path}`,
  `diskanalyze_scan_directories{path}`, `diskanalyze_scan_warnings{path}`
- `diskanalyze_dir_bytes{path,mode}` — top-level child rollups capped at
  `--top` (cardinality cap: bounded label set per scrape)
- `diskanalyze_filesystem_bytes{mount,state="total|used|available"}` —
  statvfs of the scan root's mount; mount resolved by longest-prefix match
  over `/proc/mounts` with octal-escape decoding.

Label escaping: `\\` → `\\\\`, `"` → `\"`, `\n` → `\\n`; all other C0
controls (incl. TAB) normalized to space — `\\t` is NOT a valid exposition
escape and strict parsers reject unknown escapes.

## Verified pre-handoff (actual runs)

1. Fixture sweep `--prom` on `/tmp/diskanalyzer-fixture`: rc 0, all 9
   families emitted, values sane (`scan_bytes=17,211,392` matching prior
   golden totals; 9 files / 9 dirs; `dir_bytes` for all 8 top-level dirs).
2. `--prom-out /tmp/textfile/diskanalyzer.prom`: file created mode
   `-rw-r--r--` (0644), no `.tmp` residue, content == stdout form.
3. Adversarial dirs `/tmp/prom-edge` (`weird"name`, `back\slash`, `tab<TAB>name`):
   output shows `back\\slash`, `weird\"name`, `tab name` — correct escaping,
   rc 0.

## NOT yet verified (blocked mid-run — QA must do these)

- Grammar validation of the emitted text: `prom_validate.py` (new,
  stdlib-only strict exposition parser shipped in this handoff) — written
  but never executed; run `diskanalyze … --prom | python3 prom_validate.py -`
  on fixture, `/tmp/prom-edge`, and a big real tree (`/usr`), plus the
  negative fixture inside the validator (`bad name`, raw `\t` in label,
  missing TYPE must all produce VIOLATION lines, rc 1).
- Real node_exporter against `/tmp/textfile` with `--collector.textfile.directory`
  and curl `/metrics` (QA's stated endgame).
- Atomicity claim under concurrent read (loop-scrape while rewriting).
- jobs 1 vs 4 determinism of `--prom` output minus timestamp/duration lines.
- `.orig` freeze + commit (F4 discipline: same commit, record hash).

## Known limitations (honest)

- `diskanalyze_scan_success` only ever says 1; a crashed run leaves the old
  file — Prometheus-side `time() - timestamp > 2*interval` alert needed
  (documented in HELP? no — mention in HOWTO; HOWTO not yet updated).
- `--prom` + shell `>` redirect is NOT atomic (documented in flag help;
  `--prom-out` is the recommended path).
- `mount` label uses the scan root's mount only; scanning across `--cross-fs`
  doesn't emit per-mount rollups.
- If the same `.prom` file is written by two concurrent runs to different
  roots, label sets merge per family; textfile collector rejects duplicate
  identical label sets — don't point two runs at one file.
- HOWTO.md and the du-parity header docstring not yet updated for v1.3.

## Run / test

```
python3 diskanalyzer.py /tmp/diskanalyzer-fixture --prom
python3 diskanalyzer.py /usr --prom-out /var/lib/node_exporter/diskanalyzer.prom --top 20
```

## Independent verification (skepticsammy, same box)

- `prom_validate.py` as shipped was BROKEN: HELP parsing treated the whole
  remainder of `# HELP <name> <text>` as the metric name → 18 false
  VIOLATIONs on valid output. Fixed (take first token). After fix:
  fixture rc 0 (18 samples), `/tmp/prom-edge` rc 0 (13 samples), negative
  fixture (`bad\tlabel`, valueless line, HELP w/o TYPE) → rc 1 with correct
  VIOLATIONs.
- Real end-to-end: installed `prometheus-node-exporter` 1.10.2 from the
  Ubuntu repo (NOT GitHub — no download approval needed), pointed
  `--collector.textfile.directory` at the dir. Single `.prom` file:
  HTTP 200, all 18 metrics served with correct values and escaping
  (`back\\slash`, `weird\"name`, tab→space round-trip confirmed in the
  served labels).
- Atomicity: 40 concurrent `--prom-out` rewrites while loop-scraping:
  0 failed scrapes, 0 `.tmp` residue, no parse errors attributable to
  diskanalyzer.
- **NEW DEFECT (worse than the handoff's "two runs, one file" note):**
  two DIFFERENT `.prom` files in the collector dir collide on the three
  label-less families (`scan_success`, `scan_timestamp_seconds`,
  `scan_duration_seconds`) — "collected metric ... was collected before
  with the same name and label values" — and the textfile collector
  rejects the ENTIRE gather: every diskanalyze metric disappears from
  `/metrics`, including from the innocent file. Single file: fine. Two
  files: 0 metrics served.
  Fix options: (a) add `path` (or a `file`/`job`) label to those three
  families, or (b) document one diskanalyze `.prom` per exporter as a hard
  constraint. (a) is preferred — users will naturally add a second scan
  root.
- FROZEN-CANDIDATE GATE: apply the label fix, re-run `--prom | python3
  prom_validate.py -` on fixture + edge + `/usr`, then freeze/commit.
  Verdict on v1.3.0 so far: core emitter + atomic write hold up under a
  real exporter; the multi-file collision blocks freeze.
