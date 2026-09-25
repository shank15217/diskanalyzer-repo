# DISKANALYZER SECURITY & SAFETY AUDIT — round 1 (v1.2)

**Verdict:** PASS (hardened by default) — 1 minor data-safety finding, 2 doc-level
notes, no exploitable defects found.

**Scope:** static review of all `open`/write/exec sinks + adversarial fuzzing:
CSV/JSON injection, malicious snapshot files, control-char filenames, unwritable
paths, deep-nesting DoS, curses with hostile names, /proc parsing.

## Attack-surface inventory (static)
- No `subprocess`, `eval`, `exec()`, `pickle`, `os.system`, no network. Writes are
  exactly two: `--snapshot` file, and stdout for exports. Read-only by design
  (no delete key in interactive, confirmed).
- Reads: user-named files (snapshot, exclude-file), `/proc/mounts`. No path is
  constructed from scanned data.

## Adversarial results
| Probe | Result |
|---|---|
| Snapshot: invalid JSON / missing keys / wrong value types (`files` values as strings) | rc 2, "not a diskanalyze snapshot", no traceback, no partial state |
| Snapshot with `../../etc/passwd` traversal relpaths (schema-valid shape) | rejected by loader schema validation; diff performs zero fs access — traversal strings are inert data |
| 20,000-deep nested JSON snapshot | rc 2, no traceback (recursion caught) |
| CSV export: filename containing newline | correctly quoted by `csv` module, parses to same row count — no row forgery |
| CSV export: `=HYPERLINK`, `+SUM`, `-`, `@CMD` filename prefixes | exported verbatim (see F2) |
| Table/`--csv`/`--interactive` (pty) with ANSI-escape + newline filenames | rc 0 everywhere, no crash, curses renders without injection |
| `--snapshot` to unwritable path (`/proc/...`) | rc 2 clean message |
| `--exclude-file` missing / with malformed glob + comments | missing → rc 2 clean; malformed glob silently inert (fnmatch doesn't raise) |
| Symlink-loop / cycle safety | covered in functional rounds (rc 0 + warning) |

## Findings
- **F1 (minor, data-safety):** `--snapshot FILE` silently overwrites an existing
  file (verified: pre-existing content destroyed, rc 0). User-chosen path, but a
  one-line guard (refuse existing file unless `--force`, or write-temp-then-rename
  for atomicity) removes the footgun; rename also fixes partial-write-on-crash.
- **F2 (doc-level):** CSV exports carry spreadsheet formula prefixes unmodified.
  This is a property of any CSV opened in Excel/Sheets, not a tool bug — one line
  in `--csv` help ("exported names are not sanitized for spreadsheet formula
  injection") closes it.
- **F3 (doc-level):** snapshot files are written 0644 (world-readable). Snapshots
  contain the full file listing of the scanned tree + uid/gid — mildly sensitive on
  shared hosts. Consider 0600. Not a bug against any stated promise.

## Unfiltered opinion
The architecture is the security story: read-only tool, two write sinks, JSON
loader that treats everything unvalidated as poison (rc 2, no traceback, even on
recursion depth). I threw injection, traversal, DoS, and terminal-gremlin inputs
at it and it never crashed, never wrote where it shouldn't, and never parsed a
forged row. The three findings are polish, not exposure.

Iteration: security round 1.

---
# FOLLOW-UP — 1.2.1 writer re-verification (security round 2)

**Verdict: PASS-WITH-NOTES.** F1/F2/F3 fixes all verified independently:
refuse rc 2 canary-intact; --force replaces only the intended file; 0600 holds
under umask 0 (create-mode provenance); symlink squat at .tmp fails closed
plain AND --force (victim canary untouched, replace swaps the link not the
target); refuse fires pre-walk (0.12 s on /usr); os.replace crash-atomicity ok;
no .tmp residue in any path.

## New findings
- **F4 (minor):** pre-seeded foreign `path.tmp` + `--force`: O_EXCL fails closed
  (rc 2, target keeps PRIOR content — good), but the except path's unconditional
  `os.unlink(tmp)` deletes the foreign file. Not a target-integrity hole; an
  unowned-file-delete footgun (concurrent writers / shared /tmp leftovers).
  Fix: unlink tmp only when this process's os.open succeeded.
  Independently reproduced by skepticsammy (line 531).
- **F5 (process):** `diskanalyzer-1.2.1.py.orig` ≠ live source — the 4da6c0d
  ordering fix post-dates the freeze; byte-compare target is stale. Confirmed
  independently by skepticsammy via diff. Fix: re-freeze + re-tag same commit,
  record the commit hash in the handoff.

Scope note: my sweep re-run was writer-focused + scan smoke; Paul's 28/28
functional sweep stands as his claim. Full-sweep re-verification is owed to
whichever artifact results from the F4+F5 fix.
