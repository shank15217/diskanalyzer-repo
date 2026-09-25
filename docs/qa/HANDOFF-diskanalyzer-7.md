# HANDOFF diskanalyzer v1.1 (iteration 7) — fixes for QA report 6

## D1 (blocker) — FIXED
Root cause: in the `--follow` path, a symlinked **directory** was enqueued
with `self._enqueue(link_rel, full)` **without** inserting a `DirRec` for it
first (the regular-dir path does both atomically under `queue_lock`). The
worker popped the item and did `self.dirs[relpath]` → `KeyError`.
Fix (scan loop): build `link_dir` relpath, insert `DirRec(link_dir)` and
append to the queue atomically under `queue_lock`, exactly like normal dirs.
Also guards the name-collision case (`ln -s .. up` where the derived relpath
already exists) → recorded as a symlink-loop warning instead of clobbering.

Verified:
- `ln -s sub1 dirlink; diskanalyzer . --follow` → rc 0, descends, counts file once.
- QA's exact shape `/tmp/d1nested/sub1/dirlink -> /tmp/d1nested` → rc 0.
- `ln -s .. up` cycle → rc 0, `warning: skipping symlink loop up`.
- du parity: `du -sB1 --apparent-size` on repro = 7, tool = 3; delta is
  exactly the 4-byte dir inode du counts (documented dir-inode policy, same
  as every other mode) — not a regression.
- `--follow` now in the permanent regression fixture (`alink -> a` added to
  the shared fixture tree) and 5 `--follow` combos in the sweep, incl.
  `--follow --jobs 4` determinism: json identical serial vs 4 jobs ✅.

## D2 (major) — FIXED
Dotfile rule now matches the docstring explicitly:
```
stem = name.lstrip(".")
no stem ("." )          -> (none)
"." in stem             -> last suffix lowercased  (.config.json -> .json)
name starts with "."    -> name.lower()            (.bashrc -> .bashrc)
else                    -> (none)
```
Rule table verified live: `.bashrc`→`.bashrc`, `.config.json`→`.json`,
`app.LOG`→`.log`, `x.tar.gz`→`.gz`, `noext`→`(none)`, `..hidden.cfg`→`.cfg`.
Note for QA's oracle script: rule changed from the lstrip-blind version in
HANDOFF-6 — bare dotfiles now roll up under their own name, as documented.

## Symlink-count note (QA's "your call")
Design call: `--by-type` / `--by-owner` keep counting symlinks (bytes and
file-count). Rationale: du semantics include the symlink's own size and our
du-parity guarantee is bytes; silently dropping them from counts while their
bytes stay would make count and bytes inconsistent within the same view.
Documented in the header.

## Version
`__version__` bumped per the agreed scheme → `--version` prints
`diskanalyze 1.1`.

## Regression (this box)
28/28 flag combos pass, including the new permanent symlinked-dir fixture,
all v1.0 features, and the diff error paths.
