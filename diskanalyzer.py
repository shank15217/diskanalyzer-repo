#!/usr/bin/env python3
"""diskanalyze — terminal disk-usage analyzer, v1.3.0.

Current: v1.3.0 — Prometheus textfile-collector output (--prom stdout /
--prom-out atomic file, exposition format — node_exporter does NOT parse
TOML/JSON). v1.2 perf pass (C-level DirEntry predicates, lock-churn and
aggregation rework, ~1.15-1.26x faster scan depending on host) plus security
polish (1.2.1: snapshot clobber guard + --force, atomic 0600 temp-rename
writer, CSV formula-prefix note). v1.0 added: --version, --snapshot/--diff
(scan diffing), --by-type (extension rollup), --by-owner (bytes per uid/gid),
--mounts (df-style filesystem overview), --interactive (curses drill-down).
Versioning: future revisions bump by .1 (1.3, 1.4, ...).

Security posture: read-only except two write sinks (stdout, --snapshot);
no eval/subprocess/pickle/network. Snapshot writes are O_EXCL + atomic
rename at 0600 and refuse existing targets unless --force (fail-closed
against /tmp symlink squats — see write_snapshot).

Recursively scans a directory tree with os.scandir, aggregates bottom-up,
and prints top-N space hogs.

Semantics (chosen for exact parity with GNU `du`):
  * default size = allocated bytes (st_blocks * 512)      == `du -s`
  * --apparent   = apparent bytes (st_size)               == `du -sb`
  * directory inodes are NOT counted (du semantics — totals match du exactly).
  * symlinks are counted by their OWN size (target-string length for apparent,
    lstat st_blocks*512 for allocated) and are NOT followed by default;
    --follow descends into symlinked dirs (a dir target is counted at most
    once; cycles are detected and skipped with a warning). NOTE: like `du -L`,
    a symlink pointing ABOVE the scan root (e.g. `ln -s .. up`) legitimately
    traverses outside it — only already-visited inodes are skipped, so totals
    can include sibling trees. Determinism tests must avoid upward symlinks.
  * hard links are deduped by (st_dev, st_ino): counted exactly once.
  * mount boundaries: by default we do NOT descend into other devices
    (bind mounts, /proc, /sys, other mounts) — each is skipped with a
    warning. --cross-fs opts back into du's default (descend everywhere);
    same-filesystem bind mounts are always traversed (du -x semantics —
    only a different st_dev is skipped), and the (st_dev, st_ino) inode
    dedup prevents double-counting.
  * unreadable directories are reported as warnings and skipped (no crash).
  * --exclude-file PATH reads exclude patterns (one per line, # comments,
    blank lines ignored); patterns match entry NAMES via fnmatch.
  * --jobs N parallelizes the walk across worker threads (0 = cpu count).
    All shared state (inode set, files, dirs, queue) is lock-protected; the
    inode check-and-add is atomic under one lock so hard links and symlink
    targets are counted exactly once even under concurrency. Results are
    identical to the serial scan (all output is sorted/deterministic).
  * --progress prints live scan progress (entries/s, elapsed) on stderr.
  * Iter 5 extras: --tree-csv / --tree-json export the per-directory rollup;
    --duplicates finds hard-link groups (same inode) and identical-content
    groups (group-by-size, then sha256 only the size-colliding candidates —
    keeps it fast; empty files excluded by default via --min-size 1);
    --recent-days D lists the largest files modified within D days.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

__version__ = "1.3.0"


@dataclass
class FileRec:
    relpath: str
    kind: str            # 'file' | 'symlink'
    apparent: int        # st_size / target-string length
    allocated: int       # st_blocks * 512
    hard_links: int
    mtime: float
    uid: int = -1            # st_uid (regular files / symlinks)
    gid: int = -1            # st_gid
    inode: tuple = None  # (st_dev, st_ino) — None for own-size symlinks


@dataclass
class DirRec:
    relpath: str          # '' for root
    total: int = 0        # files + symlink sizes + children totals (dir inode NOT counted, du semantics)
    warnings: int = 0


class Scanner:
    def __init__(self, root: str, follow: bool, apparent: bool,
                 excludes: list[str], one_fs: bool) -> None:
        self.root = root
        self.follow = follow
        self.apparent = apparent
        self.excludes = excludes
        self.one_fs = one_fs
        self.files: list[FileRec] = []
        self.dirs: dict[str, DirRec] = {"" : DirRec("")}
        self.warnings: list[str] = []
        self.seen_inodes: set[tuple[int, int]] = set()
        self.progress = 0
        self.t0 = 0.0
        self.track_inodes = False            # set before scan() for --duplicates
        self.track_owner = False             # set before scan() for --by-owner
                                             # (uid/gid recording; saves work off that path)
        self.inode_paths: dict[tuple[int, int], list[str]] = {}
        self.lock = threading.Lock()          # protects files/warnings/seen_inodes/progress
        self.queue_lock = threading.Lock()    # protects the work queue + dirs inserts
        root_stat = os.stat(root)
        self.root_dev = root_stat.st_dev

    # -- helpers ---------------------------------------------------------
    def _excluded(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in self.excludes)

    def _size(self, st: os.stat_result) -> int:
        return st.st_size if self.apparent else st.st_blocks * 512

    # -- work queue --------------------------------------------------------
    def _claim(self) -> tuple[str, str] | None:
        with self.queue_lock:
            if self.queue:
                return self.queue.popleft()
            return None

    def _enqueue(self, relpath: str, fullpath: str) -> None:
        with self.queue_lock:
            self.queue.append((relpath, fullpath))

    def _claim_inode(self, key: tuple[int, int]) -> bool:
        """Atomically check-and-add an inode key; True if it was new."""
        with self.lock:
            if key in self.seen_inodes:
                return False
            self.seen_inodes.add(key)
            return True

    # -- main walk (serial, or worker-threaded with --jobs N) -------------
    def scan(self, jobs: int = 1, progress: bool = False) -> None:
        """Walk the tree breadth-first from the root and fill self.files /
        self.dirs with bottom-up totals.

        Work is a queue of (relpath, fullpath) directory items; each item is
        claimed by exactly one worker, so the DirRec it writes d.total into is
        single-owner until the final children pass (no lock needed for sizes).
        jobs <= 1 runs inline; jobs > 1 spawns that many threads (NOTE: GIL —
        more threads is currently SLOWER; keep 1). progress=True ticks
        entries/s on stderr. Hard links and --follow symlink targets are
        deduped via an atomic (st_dev, st_ino) claim. Never raises on unreadable
        dirs or broken links — those become self.warnings entries.
        """
        self.t0 = time.monotonic()
        self.queue: deque[tuple[str, str]] = deque([("", self.root)])

        stop_ticker = threading.Event()

        def ticker() -> None:
            while not stop_ticker.is_set():
                el = time.monotonic() - self.t0
                with self.lock:
                    n = self.progress
                rate = n / el if el > 0 else 0.0
                print(f"\rscanning: {n:,} entries, {rate:,.0f} entries/s, {el:.1f}s ",
                      file=sys.stderr, end="", flush=True)
                stop_ticker.wait(0.5)

        def worker() -> None:
            """Pop directories off the queue and record their entries.

            Per entry: classify via C-level DirEntry predicates (is_symlink /
            is_dir — no syscall for most kinds), stat only what needs it, and
            route to: own-size symlink record, followed-symlink (count target
            once, loop-guarded), subdirectory (DirRec + enqueue atomically),
            or regular file (inode claim + FileRec). File bytes for THIS dir
            accumulate into dtotal and are written to d.total once per
            directory — d is owned by this worker until aggregation ends, so
            only self.files/self.progress/self.dirs need locks, and progress
            is batched. Inode check + set-add + append happen under ONE lock
            acquisition so hard links are counted exactly once."""
            app = self.apparent
            excludes = self.excludes          # hoist: empty most of the time
            track_inodes = self.track_inodes
            track_owner = self.track_owner
            seen_add = self.seen_inodes.add   # bound methods, off the hot path
            seen = self.seen_inodes
            while True:
                item = self._claim()
                if item is None:
                    return
                relpath, fullpath = item
                d = self.dirs[relpath]
                try:
                    it = os.scandir(fullpath)
                except OSError as e:
                    with self.lock:
                        self.warnings.append(f"cannot read {fullpath}: {e.strerror}")
                        d.warnings += 1
                    continue
                try:
                    entries = list(it)
                finally:
                    it.close()

                dtotal = 0                    # this dir's file bytes (dir owned
                                              # by this worker until aggregation)
                cnt = 0                       # progress batched per directory
                for ent in entries:
                    if excludes and self._excluded(ent.name):
                        continue
                    full = ent.path           # C attribute: no os.path.join
                    rel = relpath + "/" + ent.name if relpath else ent.name

                    if ent.is_symlink():
                        if not self.follow:
                            try:
                                lst = ent.stat(follow_symlinks=False)
                            except OSError as e:
                                with self.lock:
                                    self.warnings.append(f"cannot stat {full}: {e.strerror}")
                                continue
                            # du semantics: symlink counted at its own lstat size
                            rec = FileRec(relpath=rel, kind="symlink",
                                          apparent=lst.st_size,
                                          allocated=lst.st_blocks * 512,
                                          hard_links=lst.st_nlink, mtime=lst.st_mtime,
                                          uid=lst.st_uid if track_owner else -1,
                                          gid=lst.st_gid if track_owner else -1,
                                          inode=(lst.st_dev, lst.st_ino))
                            dtotal += lst.st_size if app else lst.st_blocks * 512
                            cnt += 1
                            with self.lock:
                                self.files.append(rec)
                            continue
                        # --follow (du -L semantics): count the TARGET's size
                        try:
                            tst = ent.stat(follow_symlinks=True)
                        except OSError as e:
                            with self.lock:
                                self.warnings.append(f"broken symlink {ent.name}: {e.strerror}")
                            continue
                        if stat.S_ISDIR(tst.st_mode):
                            key = (tst.st_dev, tst.st_ino)
                            if not self._claim_inode(key):
                                with self.lock:
                                    self.warnings.append(f"skipping symlink loop {ent.name}")
                                continue
                            if rel in self.dirs:
                                with self.lock:
                                    self.warnings.append(
                                        f"skipping symlink loop {ent.name} (dir name already in tree)")
                                continue
                            # DirRec insert + enqueue must be atomic, like dirs
                            with self.queue_lock:
                                self.dirs[rel] = DirRec(rel)
                                self.queue.append((rel, full))
                            continue
                        # target is a regular file: count target bytes once (du -L)
                        key = (tst.st_dev, tst.st_ino)
                        if not self._claim_inode(key):
                            continue
                        rec = FileRec(relpath=rel, kind="symlink",
                                     apparent=tst.st_size,
                                     allocated=tst.st_blocks * 512,
                                     hard_links=tst.st_nlink, mtime=tst.st_mtime,
                                     uid=tst.st_uid if track_owner else -1,
                                     gid=tst.st_gid if track_owner else -1,
                                     inode=key)
                        dtotal += tst.st_size if app else tst.st_blocks * 512
                        cnt += 1
                        with self.lock:
                            self.files.append(rec)
                        continue

                    if ent.is_dir(follow_symlinks=False):
                        try:
                            lst = ent.stat(follow_symlinks=False)
                        except OSError as e:
                            with self.lock:
                                self.warnings.append(f"cannot stat {full}: {e.strerror}")
                            continue
                        if self.one_fs and lst.st_dev != self.root_dev:
                            with self.lock:
                                self.warnings.append(
                                    f"skipping {full}: different device (bind mount / other fs)")
                            continue
                        with self.queue_lock:
                            self.dirs[rel] = DirRec(rel)
                            self.queue.append((rel, full))
                            self.progress += 1
                        continue

                    # regular file (or fifo/socket/device — counted, not descended)
                    try:
                        lst = ent.stat(follow_symlinks=False)
                    except OSError as e:
                        with self.lock:
                            self.warnings.append(f"cannot stat {full}: {e.strerror}")
                        continue
                    key = (lst.st_dev, lst.st_ino)
                    with self.lock:
                        if key in seen:
                            continue  # hard link already counted
                        seen_add(key)
                        rec = FileRec(
                            relpath=rel,
                            kind="file", apparent=lst.st_size,
                            allocated=lst.st_blocks * 512,
                            hard_links=lst.st_nlink, mtime=lst.st_mtime,
                            uid=lst.st_uid if track_owner else -1,
                            gid=lst.st_gid if track_owner else -1,
                            inode=key,
                        )
                        if track_inodes:
                            self.inode_paths.setdefault(key, []).append(rel)
                        self.files.append(rec)
                    sz = lst.st_size if app else lst.st_blocks * 512
                    dtotal += sz
                    cnt += 1

                d.total += dtotal
                if cnt:
                    with self.lock:
                        self.progress += cnt

        if progress:
            tk = threading.Thread(target=ticker, daemon=True)
            tk.start()

        if jobs <= 1:
            worker()
        else:
            threads = [threading.Thread(target=worker, daemon=True)
                       for _ in range(jobs)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        if progress:
            stop_ticker.set()
            el = time.monotonic() - self.t0
            n = self.progress
            print(f"\rdone: {n:,} entries in {el:.2f}s "
                  f"({n / el:,.0f} entries/s)" if el > 0 else f"\rdone: {n:,} entries",
                  file=sys.stderr)

        # bottom-up aggregation
        by_parent: dict[str, list[DirRec]] = {}
        for dr in self.dirs.values():
            if not dr.relpath:
                continue  # root has no parent — must not be its own child
            parent = os.path.dirname(dr.relpath)
            by_parent.setdefault(parent, []).append(dr)
        # per-dir file bytes were accumulated by the owning worker into
        # d.total during the walk; now add children totals, deepest first.
        for relpath in sorted(self.dirs, key=lambda r: -len(r)):
            d = self.dirs[relpath]
            for child in by_parent.get(relpath, []):
                d.total += child.total

    def _size_of(self, f: FileRec) -> int:
        return f.apparent if self.apparent else f.allocated


def _is_symlink(st: os.stat_result) -> bool:
    import stat as _s
    return _s.S_ISLNK(st.st_mode)


def _is_dir(st: os.stat_result) -> bool:
    import stat as _s
    return _s.S_ISDIR(st.st_mode)


# -- output ---------------------------------------------------------------
def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if n < 1024 or unit == "PiB":
            if unit == "B":
                return f"{n} B"
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n} B"


def emit_tree(s: Scanner, depth: int, bar_width: int = 24) -> None:
    """ASCII tree of directories with per-dir totals and proportional bars
    (the terminal stand-in for a treemap). depth=0 means unlimited;
    otherwise N levels below the root are shown."""
    root_total = s.dirs[""].total

    def bar(total: int) -> str:
        if total <= 0 or root_total <= 0:
            return ""
        n = max(1, min(bar_width, round(total / root_total * bar_width)))
        return "█" * n + "░" * (bar_width - n)

    print(f"Tree of {s.root}   ({'apparent' if s.apparent else 'allocated'} bytes)")
    print(f"{s.root}/  {human(root_total):>12}  {bar(root_total)}")

    by_parent: dict[str, list[str]] = {}
    for r in s.dirs:
        if not r:
            continue
        by_parent.setdefault(os.path.dirname(r), []).append(r)

    def rec(rel: str, prefix: str) -> None:
        items = sorted(by_parent.get(rel, []), key=lambda r: (-s.dirs[r].total, r))
        for i, r in enumerate(items):
            last = i == len(items) - 1
            d = s.dirs[r]
            mark = "   (!unreadable)" if d.warnings else ""
            print(f"{prefix}{'└── ' if last else '├── '}"
                  f"{os.path.basename(r)}/  {human(d.total):>12}  {bar(d.total)}{mark}")
            level = r.count("/") + 1
            if depth == 0 or level < depth:
                rec(r, prefix + ("    " if last else "│   "))

    rec("", "")
    if s.warnings:
        for w in s.warnings:
            print(f"  warning: {w}", file=sys.stderr)


def emit_tree_csv(s: Scanner) -> None:
    w = csv.writer(sys.stdout)
    w.writerow(["relpath", "total"])
    for d in sorted(s.dirs.values(), key=lambda d: d.relpath):
        w.writerow([d.relpath or ".", d.total])


def emit_tree_json(s: Scanner) -> None:
    out = {
        "root": s.root,
        "mode": "apparent" if s.apparent else "allocated",
        "root_total": s.dirs[""].total,
        "warnings": s.warnings,
        "dirs": [
            {"relpath": d.relpath or ".", "total": d.total}
            for d in sorted(s.dirs.values(), key=lambda d: d.relpath)
        ],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError as e:
        return f"ERR:{e.strerror}"


def emit_duplicates(s: Scanner, min_size: int) -> None:
    """Hard-link groups (same inode) + identical-content groups.
    Fast path: group inodes by apparent size first; sha256 is computed only
    for inodes whose size collides with at least one other inode (and is
    computed once per inode — every hard-link path joins its bucket)."""
    inode_size: dict[tuple[int, int], int] = {}
    for key, paths in s.inode_paths.items():
        st = os.lstat(os.path.join(s.root, paths[0]))
        inode_size[key] = st.st_size

    size_classes: dict[int, list[tuple[int, int]]] = {}
    for key, size in inode_size.items():
        if size >= min_size:
            size_classes.setdefault(size, []).append(key)

    buckets: dict[str, list[str]] = {}
    bucket_inodes: dict[str, set] = {}
    bucket_size: dict[str, int] = {}
    hashed = 0
    for size, keys in size_classes.items():
        if len(keys) < 2:
            continue  # unique size — cannot have a content duplicate
        for key in keys:
            digest = _sha256(os.path.join(s.root, s.inode_paths[key][0]))
            hashed += 1
            bucket_size[digest] = size
            bucket_inodes.setdefault(digest, set()).add(key)
            buckets.setdefault(digest, []).extend(s.inode_paths[key])

    groups = sorted(
        ({"size": bucket_size[d], "inodes": len(bucket_inodes[d]),
          "count": len(p), "sha256": d,
          "paths": sorted(p)}
         for d, p in buckets.items() if len(p) >= 2),
        key=lambda g: (-g["count"], -g["size"], g["sha256"]),
    )
    hardlink = sorted(
        ({"count": len(p), "paths": sorted(p)}
         for p in s.inode_paths.values() if len(p) >= 2),
        key=lambda g: (-g["count"], g["paths"][0]),
    )
    # reclaimable = keep one copy per distinct inode; hard links share blocks,
    # so they don't add wasted space.
    redundant_inodes = sum(g["inodes"] - 1 for g in groups)
    wasted_bytes = sum(g["size"] * (g["inodes"] - 1) for g in groups)
    print(f"Duplicate groups (identical content, >=2 paths): {len(groups)} "
          f"[sha256 computed for {hashed} inodes]")
    print(f"Redundant inodes: {redundant_inodes}   wasted space (reclaimable): "
          f"{human(wasted_bytes)}")
    for g in groups[:20]:
        extra = f"  ({g['inodes']} inode{'s' if g['inodes']!=1 else ''})" if g["inodes"] != g["count"] else ""
        print(f"  {human(g['size']):>12}  x{g['count']:<4}{extra} {g['sha256'][:12]}…")
        for p in g["paths"][:5]:
            print(f"      {p}")
        if g["count"] > 5:
            print(f"      … {g['count'] - 5} more")
    print(f"Hard-link groups (>=2 paths, same inode): {len(hardlink)}")
    for g in hardlink[:10]:
        print(f"  x{g['count']}  e.g. {g['paths'][0]} (+{g['count']-1} more)")


# -- v1.0 features ----------------------------------------------------------
SNAPSHOT_FORMAT = "diskanalyze-snapshot/1"


def snapshot_of(s: Scanner, elapsed: float) -> dict:
    """Serializable scan snapshot for --snapshot / --diff. Stores per-directory
    rollups plus per-file sizes keyed by relpath (enables add/remove/change)."""
    return {
        "format": SNAPSHOT_FORMAT,
        "version": __version__,
        "root": s.root,
        "mode": "apparent" if s.apparent else "allocated",
        "created": time.time(),
        "elapsed_s": round(elapsed, 3),
        "totals": {
            "files": len([f for f in s.files if f.kind == "file"]),
            "symlinks": len([f for f in s.files if f.kind == "symlink"]),
            "directories": len(s.dirs),
            "size": s.dirs[""].total,
        },
        "dirs": {d.relpath or ".": d.total for d in s.dirs.values()},
        "files": {f.relpath: s._size_of(f) for f in s.files},
    }


def write_snapshot(s: Scanner, path: str, elapsed: float, force: bool = False) -> None:
    """Atomically write scan snapshot JSON to path at mode 0600.

    Refuses an existing target unless force (snapshots are diff baselines;
    silent clobber breaks every future --diff). The write goes to path.tmp via
    O_CREAT|O_EXCL then os.replace, so: a failed write never corrupts the
    prior baseline; a pre-existing tmp (squat or leftover) makes O_EXCL fail
    closed and the FOREIGN file is left alone (only an tmp we opened is ever
    unlinked); os.replace over a symlink swaps the link, never its target."""
    if os.path.exists(path) and not force:
        print(f"error: snapshot target exists: {path}  "
              f"(use --force to overwrite)", file=sys.stderr)
        raise SystemExit(2)
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as e:
        # F4: open failed — tmp was NOT created by us (EEXIST squat or
        # leftover). Never unlink a file we don't own. Fail closed.
        print(f"error: cannot write snapshot: {e}", file=sys.stderr)
        raise SystemExit(2)
    try:
        # fd is now ours; cleanup below only ever touches our own tmp
        with os.fdopen(fd, "w") as fh:
            json.dump(snapshot_of(s, elapsed), fh, ensure_ascii=False)
        os.replace(tmp, path)          # atomic on same fs
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"error: cannot write snapshot: {e}", file=sys.stderr)
        raise SystemExit(2)
    print(f"snapshot written: {path}  ({s.dirs[''].total:,} bytes, "
          f"{len(s.files):,} files, {len(s.dirs):,} dirs)")


def _load_snapshot(path: str) -> dict:
    """Read and validate a snapshot JSON. Exit 2 (never traceback) on
    unreadable file, invalid JSON, or a foreign format tag — snapshots are
    attacker-influenceable input and this is the only place they are parsed."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read snapshot: {e}", file=sys.stderr)
        raise SystemExit(2)
    if data.get("format") != SNAPSHOT_FORMAT:
        print(f"error: not a diskanalyze snapshot: {path}", file=sys.stderr)
        raise SystemExit(2)
    return data


def emit_diff(path_a: str, path_b: str, top: int) -> int:
    """Compare two snapshots: per-directory growth/shrink plus file
    added/removed/grown/shrunk lists."""
    a = _load_snapshot(path_a)
    b = _load_snapshot(path_b)
    if a["mode"] != b["mode"]:
        print(f"warning: snapshots use different size modes "
              f"({a['mode']} vs {b['mode']}); diff may be misleading",
              file=sys.stderr)
    da, db = a["dirs"], b["dirs"]
    fa, fb = a["files"], b["files"]

    keys = set(da) | set(db)
    deltas = sorted(((db.get(k, 0) - da.get(k, 0), k) for k in keys),
                    key=lambda t: -abs(t[0]))
    print(f"Diff A→B: {a['root']} [{a['mode']}]  vs  {b['root']} [{b['mode']}]")
    print(f"  A: {human(a['totals']['size'])} ({a['totals']['files']:,} files)  "
          f"B: {human(b['totals']['size'])} ({b['totals']['files']:,} files)")
    net = db.get(".", 0) - da.get(".", 0)
    print(f"  net change: {'+' if net >= 0 else '-'}{human(abs(net))}")
    print()
    print(f"Top directories by |change| (top {top}):")
    shown = 0
    for delta, k in deltas:
        if delta == 0:
            continue
        shown += 1
        if shown > top:
            break
        label = k if k != "." else "(root)"
        print(f"  {'+' if delta > 0 else '-'}{human(abs(delta)):>12}  "
              f"{human(da.get(k, 0)):>12} -> {human(db.get(k, 0)):>12}  {label}/")

    added = sorted(set(fb) - set(fa))
    removed = sorted(set(fa) - set(fb))
    grown = sorted((k for k in set(fa) & set(fb) if fb[k] != fa[k]),
                   key=lambda k: -abs(fb[k] - fa[k]))
    print()
    print(f"Files added: {len(added)}   removed: {len(removed)}   changed: {len(grown)}")
    for label, items in (("added", added[:top]), ("removed", removed[:top]),
                         ("changed", grown[:top])):
        if not items:
            continue
        print(f"  {label}:")
        for k in items:
            if label == "added":
                print(f"    +{human(fb[k]):>12}  {k}")
            elif label == "removed":
                print(f"    -{human(fa[k]):>12}  {k}")
            else:
                d = fb[k] - fa[k]
                print(f"    {'+' if d > 0 else '-'}{human(abs(d)):>12}  "
                      f"{human(fa[k])} -> {human(fb[k])}  {k}")
    return 0


def emit_by_type(s: Scanner, top: int, min_bytes: int) -> None:
    """Extension rollup: bytes per file extension (baobab-style). Files with
    no extension roll up as (none); dotfiles like .bashrc keep '.bashrc'."""
    agg: dict[str, list[int, int]] = {}
    for f in s.files:
        sz = s._size_of(f)
        if sz < min_bytes:
            continue
        name = os.path.basename(f.relpath)
        stem = name.lstrip(".")
        if not stem:                 # names like ".." or "."
            ext = "(none)"
        elif "." in stem:            # app.LOG -> .log ; .config.json -> .json
            ext = "." + stem.rsplit(".", 1)[1].lower()
        elif name.startswith("."):   # bare dotfile .bashrc -> .bashrc
            ext = name.lower()
        else:
            ext = "(none)"
        e = agg.setdefault(ext, [0, 0])
        e[0] += sz
        e[1] += 1
    rows = sorted(agg.items(), key=lambda kv: (-kv[1][0], kv[0]))
    total = sum(v[0] for _, v in rows)
    print(f"Bytes by file type ({'apparent' if s.apparent else 'allocated'}; "
          f"{len(rows)} types, total {human(total)}), top {top}:")
    for ext, (sz, n) in rows[:top]:
        pct = 100.0 * sz / total if total else 0.0
        print(f"  {human(sz):>12}  {pct:5.1f}%  {n:>8,} files  {ext}")


def emit_by_owner(s: Scanner, top: int) -> None:
    """Bytes per uid/gid with username/group resolution (falls back to the
    numeric id when the name is unknown)."""
    import pwd
    import grp
    per_uid: dict[int, list[int, int]] = {}
    per_gid: dict[int, list[int, int]] = {}
    for f in s.files:
        sz = s._size_of(f)
        u = per_uid.setdefault(f.uid, [0, 0]); u[0] += sz; u[1] += 1
        g = per_gid.setdefault(f.gid, [0, 0]); g[0] += sz; g[1] += 1

    def uname(uid: int) -> str:
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return "?"

    def gname(gid: int) -> str:
        try:
            return grp.getgrgid(gid).gr_name
        except KeyError:
            return "?"

    total = sum(v[0] for v in per_uid.values())
    print(f"Bytes by owner ({'apparent' if s.apparent else 'allocated'}; total {human(total)}), top {top}:")
    for uid, (sz, n) in sorted(per_uid.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top]:
        pct = 100.0 * sz / total if total else 0.0
        print(f"  {human(sz):>12}  {pct:5.1f}%  {n:>8,} files  {uname(uid)} ({uid})")
    print()
    print(f"Bytes by group, top {top}:")
    for gid, (sz, n) in sorted(per_gid.items(), key=lambda kv: (-kv[1][0], kv[0]))[:top]:
        pct = 100.0 * sz / total if total else 0.0
        print(f"  {human(sz):>12}  {pct:5.1f}%  {n:>8,} files  {gname(gid)} ({gid})")


# -- v1.3 Prometheus textfile-collector emission ----------------------------
# node_exporter's textfile collector parses ONLY the Prometheus exposition
# format (*.prom) — it does not read TOML/JSON. --prom prints that format on
# stdout (shell-redirectable); --prom-out writes it atomically (temp +
# os.replace) so a scrape never sees a half-written file.
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


def _prom_escape_label(v: str) -> str:
    """Escape a label value per the exposition spec: backslash, quote, then
    newline. Remaining control chars (\t, \r, \x00...) are stripped — a raw
    control byte would corrupt the sample line for the parser. Filenames can
    contain any byte except / and NUL, so this path is load-bearing."""
    v = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    # \\t is NOT a valid exposition escape (strict parsers reject unknown
    # escapes); other C0 controls would corrupt the line. Normalise to space.
    v = "".join(" " if (ch < " " and ch != "\n") else ch for ch in v)
    return v


def _prom_sample(name: str, labels: dict, value) -> str:
    """One 'name{k=\"v\",...} value' line. Asserts the metric name shape
    (programmer error otherwise) and sanitizes every label value."""
    assert _METRIC_NAME_RE.match(name), name
    if labels:
        lab = ",".join(f'{k}="{_prom_escape_label(str(v))}"'
                       for k, v in labels.items())
        return f"{name}{{{lab}}} {value}"
    return f"{name} {value}"


def _mount_for(path: str) -> str:
    """Longest mount-point prefix of path from /proc/mounts ('/' fallback).
    Octal escapes in /proc/mounts (\040 space etc.) are decoded first."""
    best = "/"
    try:
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 2:
                    continue
                mnt = (parts[1].replace("\\040", " ").replace("\\011", "\t")
                       .replace("\\012", "\n").replace("\\134", "\\"))
                if mnt == "/" or path == mnt or path.startswith(mnt.rstrip("/") + "/"):
                    if len(mnt) > len(best):
                        best = mnt
    except OSError:
        pass
    return best


def prom_lines(s: "Scanner", elapsed: float, top: int) -> list:
    """Build the exposition lines for one scan. Metric families (all gauges):
      diskanalyze_scan_timestamp_seconds   — wall clock of scan completion
      diskanalyze_scan_duration_seconds
      diskanalyze_scan_success             — always 1 here: a run that fails
        never writes/updates the file, so staleness (time() - timestamp) plus
        absence of a fresh success=1 is the failure signal
      diskanalyze_scan_bytes{path,mode}    — total for the scan root
      diskanalyze_scan_files{path}
      diskanalyze_scan_directories{path}
      diskanalyze_scan_warnings{path}
      diskanalyze_dir_bytes{path,mode}     — top-level child rollups, top N
        by size (cardinality cap: N labels per run, not one per directory)
      diskanalyze_filesystem_bytes{mount,state="total|used|available"}
    mode is 'allocated' or 'apparent' to match the scanner."""
    mode = "apparent" if s.apparent else "allocated"
    path = s.root
    out = []
    help_type = [
        ("# HELP diskanalyze_scan_success 1 when the last scan completed.",
         "# TYPE diskanalyze_scan_success gauge"),
        ("# HELP diskanalyze_scan_timestamp_seconds Unix time of scan completion.",
         "# TYPE diskanalyze_scan_timestamp_seconds gauge"),
        ("# HELP diskanalyze_scan_duration_seconds Wall-clock scan time.",
         "# TYPE diskanalyze_scan_duration_seconds gauge"),
        ("# HELP diskanalyze_scan_bytes Total bytes under the scanned path.",
         "# TYPE diskanalyze_scan_bytes gauge"),
        ("# HELP diskanalyze_scan_files File count under the scanned path.",
         "# TYPE diskanalyze_scan_files gauge"),
        ("# HELP diskanalyze_scan_directories Directory count under the scanned path.",
         "# TYPE diskanalyze_scan_directories gauge"),
        ("# HELP diskanalyze_scan_warnings Unreadable entries during the scan.",
         "# TYPE diskanalyze_scan_warnings gauge"),
        ("# HELP diskanalyze_dir_bytes Rollup bytes for a top-level directory of the scan root.",
         "# TYPE diskanalyze_dir_bytes gauge"),
        ("# HELP diskanalyze_filesystem_bytes Filesystem size from statvfs at the scan root's mount (path = scan root, so two roots on one mount coexist).",
         "# TYPE diskanalyze_filesystem_bytes gauge"),
    ]
    for h in help_type:
        out.extend(h)
    # path label on the run-status family too (v1.3 QA F1): two .prom files
    # from different scans in one collector dir otherwise collide on these
    # label-less samples and node_exporter drops the ENTIRE diskanalyze
    # gather for both files ("collected ... with the same name and label
    # values"). With path=, per-scan stale alerts also key cleanly.
    out.append(_prom_sample("diskanalyze_scan_success", {"path": path}, 1))
    out.append(_prom_sample("diskanalyze_scan_timestamp_seconds",
                            {"path": path}, f"{time.time():.3f}"))
    out.append(_prom_sample("diskanalyze_scan_duration_seconds",
                            {"path": path}, f"{elapsed:.3f}"))
    out.append(_prom_sample("diskanalyze_scan_bytes",
                            {"path": path, "mode": mode}, s.dirs[""].total))
    out.append(_prom_sample("diskanalyze_scan_files", {"path": path},
                            sum(1 for f in s.files if f.kind == "file")))
    out.append(_prom_sample("diskanalyze_scan_directories", {"path": path},
                            len(s.dirs)))
    out.append(_prom_sample("diskanalyze_scan_warnings", {"path": path},
                            len(s.warnings)))
    # top-level child rollups (dirs directly under root), capped at top N
    root_prefix = path.rstrip("/") + "/"
    children = [(d.relpath, d.total) for d in s.dirs.values()
                if d.relpath and "/" not in d.relpath]
    children.sort(key=lambda kv: (-kv[1], kv[0]))
    for rel, total in children[:max(top, 0)]:
        out.append(_prom_sample("diskanalyze_dir_bytes",
                                {"path": root_prefix + rel, "mode": mode},
                                total))
    # filesystem-level context for the scanned mount
    try:
        st = os.statvfs(path)
        mnt = _mount_for(path)
        total_fs = st.f_blocks * st.f_frsize
        used_fs = total_fs - st.f_bfree * st.f_frsize
        avail_fs = st.f_bavail * st.f_frsize
        for state, v in (("total", total_fs), ("used", used_fs),
                         ("available", avail_fs)):
            # path label required (v1.3 QA F1b): two scan roots on the SAME
            # mount (e.g. /data and /var on one fs) collide on mount+state
            # and node_exporter drops the whole gather.
            out.append(_prom_sample("diskanalyze_filesystem_bytes",
                                    {"path": path, "mount": mnt,
                                     "state": state}, v))
    except OSError:
        pass
    return out


def emit_prom(s: "Scanner", elapsed: float, top: int) -> None:
    """Print exposition format to stdout (--prom)."""
    for line in prom_lines(s, elapsed, top):
        print(line)


def write_prom(s: "Scanner", path: str, elapsed: float, top: int) -> None:
    """Atomically write exposition text to path (--prom-out). Unlike
    --snapshot this OVERWRITES without --force: the file is regenerated
    monitoring data, not a diff baseline. Temp file is unique per process in
    the target directory (same fs => os.replace is atomic); the collector
    only reads *.prom so the .tmp.<pid> name is invisible to it. Mode 0644:
    node_exporter typically runs as another user and must read it."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp.{os.getpid()}")
    body = "\n".join(prom_lines(s, elapsed, top)) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError as e:
        print(f"error: cannot write prom file: {e}", file=sys.stderr)
        raise SystemExit(2)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        os.replace(tmp, path)          # atomic swap on same fs
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print(f"error: cannot write prom file: {e}", file=sys.stderr)
        raise SystemExit(2)


def emit_mounts() -> int:
    """df-style overview of every mounted filesystem: parse /proc/mounts,
    os.statvfs each mount point, skip pseudo-filesystems."""
    PSEUDO = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
              "overlay", "squashfs", "mqueue", "debugfs", "tracefs", "securityfs",
              "configfs", "pstore", "bpf", "autofs", "hugetlbfs", "fusectl",
              "binfmt_misc", "efivarfs", "rpc_pipefs", "nsfs", "ramfs", "selinuxfs",
              "fuse.gvfsd-fuse", "fuse.portal"}
    seen_mounts: dict[str, str] = {}   # mount point -> fstype (first wins)
    try:
        with open("/proc/mounts") as fh:
            lines = fh.readlines()
    except OSError as e:
        print(f"error: cannot read /proc/mounts: {e}", file=sys.stderr)
        return 2
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mnt, fstype = parts[0], parts[1], parts[2]
        mnt = mnt.replace("\\040", " ").replace("\\011", "\t")
        if fstype in PSEUDO:
            continue
        if mnt in seen_mounts:
            continue  # bind mount of an already-listed point
        # only real block devices or nfs-ish nets; skip odd pseudo devs
        if not (dev.startswith("/") or ":" in dev or fstype.startswith("nfs")
                or fstype.startswith("smb") or fstype == "ceph" or fstype == "virtiofs"):
            if not (mnt == "/" and dev in ("rootfs",)):
                continue
        seen_mounts[mnt] = fstype

    rows = []
    for mnt, fstype in seen_mounts.items():
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total = st.f_blocks * st.f_frsize
        if total == 0:
            continue
        free = st.f_bavail * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        pct = 100.0 * used / total if total else 0.0
        rows.append((pct, mnt, dev_of(lines, mnt), fstype, total, used, free))
    rows.sort(key=lambda r: -r[0])
    print(f"{'Use%':>5}  {'Total':>10}  {'Used':>10}  {'Avail':>10}  Mount (fstype, device)")
    for pct, mnt, dev, fstype, total, used, free in rows:
        flag = "  ◀ FULL" if pct >= 90 else ""
        print(f"{pct:5.1f}  {human(total):>10}  {human(used):>10}  {human(free):>10}  "
              f"{mnt} ({fstype}, {dev}){flag}")
    return 0


def dev_of(lines: list[str], mnt: str) -> str:
    want = mnt.replace(" ", "\\040").replace("\t", "\\011")
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == want:
            return parts[0]
    return "?"


def run_interactive(s: Scanner, top: int, min_bytes: int) -> int:
    """ncdu-lite: curses drill-down over the finished scan.
    keys: ↑/↓ or j/k move, Enter/l/cd descend, h/Backspace up, q/Esc quit."""
    try:
        import curses
    except ImportError:
        print("error: curses not available on this build", file=sys.stderr)
        return 2

    files_by_dir: dict[str, list] = {}
    for f in s.files:
        files_by_dir.setdefault(os.path.dirname(f.relpath), []).append(f)
    kids: dict[str, list[str]] = {}
    for r in s.dirs:
        if r:
            kids.setdefault(os.path.dirname(r), []).append(r)

    def entries(rel: str):
        """(label, size, is_dir) rows for a directory, sorted by size desc."""
        out = []
        for c in kids.get(rel, []):
            out.append((os.path.basename(c) + "/", s.dirs[c].total, True))
        for f in files_by_dir.get(rel, []):
            sz = s._size_of(f)
            if sz >= min_bytes:
                out.append((os.path.basename(f.relpath), sz, False))
        out.sort(key=lambda t: (-t[1], t[0]))
        return out

    root_total = max(1, s.dirs[""].total)

    def _run(stdscr):
        import curses
        curses.curs_set(0)
        path = ""
        sel = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            rows = entries(path)
            total = s.dirs[path].total if path in s.dirs else s.dirs[""].total
            title = (s.root + "/" + path) if path else s.root
            stdscr.addnstr(0, 0, f" diskanalyze v{__version__}  {title}  "
                                 f"{human(total)}  ↑↓move Enter descend h up q quit",
                           max(w - 1, 1), curses.A_REVERSE)
            sel = min(sel, max(len(rows) - 1, 0))
            page = h - 3
            first = max(0, min(sel - page + 1, sel))
            # parent entry at top when not at root
            offset = 0
            if path:
                if sel == 0:
                    stdscr.addnstr(1, 2, "../", max(w - 3, 1), curses.A_BOLD)
                offset = 1
            for i in range(first, min(first + page - offset, len(rows))):
                label, sz, is_dir = rows[i]
                row = 1 + (i - first) + offset
                pct = 100.0 * sz / root_total
                bar_w = max(0, min(int(sz / root_total * 20), w - 46))
                line = f"{human(sz):>10} {pct:5.1f}% {'█' * bar_w} {label}"
                attr = curses.A_REVERSE if (i + offset) == sel else curses.A_NORMAL
                if is_dir and not (attr & curses.A_REVERSE):
                    attr |= curses.A_BOLD
                stdscr.addnstr(row, 1, line, max(w - 2, 1), attr)
            stdscr.addnstr(h - 1, 0,
                           f" {len(rows)} entries   scan: {len(s.files):,} files, "
                           f"{len(s.dirs):,} dirs", max(w - 1, 1))
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (ord("q"), 27):
                return 0
            elif ch in (curses.KEY_UP, ord("k")):
                sel = max(0, sel - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                sel = min(max(len(rows) + offset - 1, 0), sel + 1)
            elif ch in (curses.KEY_ENTER, 10, 13, ord("l")):
                if path and sel == 0:
                    path = os.path.dirname(path)
                    sel = 0
                elif sel - offset < len(rows):
                    label, _, is_dir = rows[sel - offset]
                    if is_dir:
                        path = os.path.join(path, label.rstrip("/")) if path else label.rstrip("/")
                        sel = 0
            elif ch in (curses.KEY_BACKSPACE, 8, 127, ord("h")):
                if path:
                    path = os.path.dirname(path)
                    sel = 0
    try:
        return curses.wrapper(_run)
    except Exception as e:  # terminal too small, no tty, etc.
        print(f"error: interactive mode failed ({e}); rerun without --interactive",
              file=sys.stderr)
        return 2


def emit_recent(s: Scanner, days: float, top: int) -> None:
    cutoff = time.time() - days * 86400
    recent = [f for f in s.files if f.kind == "file" and f.mtime >= cutoff]
    recent.sort(key=lambda f: (-s._size_of(f), f.relpath))
    print(f"Files modified in the last {days:g} days: {len(recent)} (top {top})")
    for f in recent[:top]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.mtime))
        print(f"  {human(s._size_of(f)):>12}  {ts}  {f.relpath}")


def emit_table(s: Scanner, top: int, min_bytes: int) -> None:
    mode = "apparent" if s.apparent else "allocated"
    root_total = s.dirs[""].total
    print(f"Root: {s.root}   ({mode} bytes)")
    print(f"Files: {len([f for f in s.files if f.kind == 'file']):>6}   "
          f"Symlinks: {len([f for f in s.files if f.kind == 'symlink']):>4}   "
          f"Dirs: {len(s.dirs):>5}   Total: {human(root_total)}")
    print()

    top_dirs = [d for d in s.dirs.values() if d.relpath and d.total > 0]
    top_dirs.sort(key=lambda d: (-d.total, d.relpath))
    print("Top directories:")
    for d in top_dirs[:10]:
        print(f"  {human(d.total):>12}  {d.relpath}/")
    print()

    files = [f for f in s.files if s._size_of(f) >= min_bytes]
    files.sort(key=lambda f: (-s._size_of(f), f.relpath))
    print(f"Top {top} files:")
    if not files:
        print("  (none)")
    for f in files[:top]:
        print(f"  {human(s._size_of(f)):>12}  {f.kind:<8}  {f.relpath}")
    print()
    print(f"Elapsed warnings: {len(s.warnings)}")
    for w in s.warnings:
        print(f"  warning: {w}", file=sys.stderr)


def emit_json(s: Scanner, min_bytes: int, elapsed: float) -> None:
    root_total = s.dirs[""].total
    files = [f for f in s.files if s._size_of(f) >= min_bytes]
    files.sort(key=lambda f: (-s._size_of(f), f.relpath))
    out = {
        "root": s.root,
        "mode": "apparent" if s.apparent else "allocated",
        "totals": {
            "files": len([f for f in s.files if f.kind == "file"]),
            "symlinks": len([f for f in s.files if f.kind == "symlink"]),
            "directories": len(s.dirs),
            "size": root_total,
        },
        "elapsed_s": round(elapsed, 3),
        "warnings": s.warnings,
        "files": [
            {"relpath": f.relpath, "kind": f.kind, "apparent": f.apparent,
             "allocated": f.allocated, "hard_links": f.hard_links,
             "mtime": f.mtime}
            for f in files
        ],
        "dirs": [
            {"relpath": d.relpath or ".", "total": d.total}
            for d in sorted(s.dirs.values(), key=lambda d: (-d.total, d.relpath))
        ],
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


def emit_csv(s: Scanner, min_bytes: int) -> None:
    files = [f for f in s.files if s._size_of(f) >= min_bytes]
    files.sort(key=lambda f: (-s._size_of(f), f.relpath))
    w = csv.writer(sys.stdout)
    w.writerow(["kind", "relpath", "size", "hard_links", "mtime"])
    for f in files:
        w.writerow([f.kind, f.relpath, s._size_of(f), f.hard_links,
                     f"{f.mtime:.0f}"])


# -- CLI --------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI entry. Exit codes: 0 success, 2 usage/input/snapshot errors
    (always with a one-line stderr message, never a traceback).

    Flow: parse -> fast paths that need no scan (--diff, --mounts) ->
    validate root/exclude-file (clobber check for --snapshot fails fast
    BEFORE the walk) -> scan -> dispatch to exactly one emitter (interactive
    > duplicates > recent > by-type > by-owner > exports > table) ->
    optionally write the snapshot. Emitters are pure functions of the scan."""
    ap = argparse.ArgumentParser(prog="diskanalyze", description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=".",
                    help="directory to scan (default: .)")
    ap.add_argument("--version", action="version",
                    version=f"diskanalyze {__version__}")
    ap.add_argument("--top", type=int, default=15, help="number of top files to show (default 15)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="worker threads for the walk (default 1 = serial; 0 = cpu "
                         "count). WARNING: >1 is currently SLOWER (GIL-bound walk — "
                         "measured 1.8s vs 5.4s on /usr with 4 threads); keep 1 "
                         "until a process-based redesign lands")
    ap.add_argument("--progress", action="store_true",
                    help="show live progress (entries/s, elapsed) on stderr")
    ap.add_argument("--tree", action="store_true", help="render the directory tree with size bars")
    ap.add_argument("--depth", type=int, default=0, help="max tree levels below root (0 = unlimited)")
    ap.add_argument("--bar-width", type=int, default=24, help="bar width in cells for --tree (default 24)")
    ap.add_argument("--min-bytes", type=int, default=0, help="hide files smaller than this")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                    help="skip entries whose NAME matches GLOB (repeatable)")
    ap.add_argument("--apparent", action="store_true",
                    help="use apparent sizes (st_size) instead of allocated bytes")
    ap.add_argument("--follow", action="store_true",
                    help="descend into symlinked directories (cycle-safe)")
    ap.add_argument("--one-fs", action="store_true",
                    help="alias for the default: skip other filesystems "
                         "(kept for backwards compatibility)")
    ap.add_argument("--cross-fs", action="store_true",
                    help="descend into other devices/mounts like du (the old "
                         "default); do NOT use when scanning /")
    ap.add_argument("--exclude-file", metavar="PATH",
                    help="read exclude patterns from a file (one per line, "
                         "# comments ignored)")
    ap.add_argument("--duplicates", action="store_true",
                    help="find hard-link groups and identical-content groups "
                         "(group-by-size then sha256; see --min-size)")
    ap.add_argument("--min-size", type=int, default=1,
                    help="--duplicates: ignore files smaller than this (default 1; "
                         "use 0 to include empty files)")
    ap.add_argument("--recent-days", type=float, metavar="D",
                    help="list the largest files modified within D days")
    ap.add_argument("--tree-csv", action="store_true",
                    help="export the per-directory rollup as CSV")
    ap.add_argument("--tree-json", action="store_true",
                    help="export the per-directory rollup as JSON")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--csv", action="store_true",
                    help="CSV dump of the file list (note: filenames beginning "
                         "with = + - @ are exported verbatim — spreadsheet apps "
                         "may treat them as formulas; open via import wizard)")
    ap.add_argument("--snapshot", metavar="FILE",
                    help="after the scan, save a snapshot to FILE for later "
                         "--diff (refuses to overwrite; see --force; file is 0600)")
    ap.add_argument("--force", action="store_true",
                    help="allow --snapshot to overwrite an existing file")
    ap.add_argument("--diff", metavar=("OLD_SNAPSHOT", "NEW_SNAPSHOT"), nargs=2,
                    help="compare two saved snapshots (no live scan): shows "
                         "grown/shrunk dirs and added/removed/changed files")
    ap.add_argument("--by-type", action="store_true",
                    help="roll up bytes by file extension (top types by size)")
    ap.add_argument("--by-owner", action="store_true",
                    help="roll up bytes per uid/gid (who is filling the disk)")
    ap.add_argument("--mounts", action="store_true",
                    help="df-style overview of every real filesystem, sorted "
                         "by use%% (no scan needed)")
    ap.add_argument("--prom", action="store_true",
                    help="print Prometheus exposition format to stdout "
                         "(redirect into the node_exporter textfile-collector "
                         "dir; for periodic use prefer --prom-out, which "
                         "writes atomically so a scrape never reads a partial "
                         "file)")
    ap.add_argument("--prom-out", metavar="FILE",
                    help="write Prometheus exposition format to FILE "
                         "atomically (temp + rename, mode 0644); overwrites "
                         "on each run — designed for cron/systemd-timer "
                         "refresh into the textfile collector directory")
    ap.add_argument("--interactive", action="store_true",
                    help="ncdu-style curses drill-down of the scan "
                         "(↑↓/jk move, Enter descend, h up, q quit)")
    args = ap.parse_args(argv)

    # --diff and --mounts work without a live scan
    if args.diff is not None:
        return emit_diff(os.path.abspath(args.diff[0]),
                         os.path.abspath(args.diff[1]), args.top)
    if args.mounts:
        return emit_mounts()

    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    # fail fast on a snapshot clobber BEFORE the (possibly long) scan
    if args.snapshot and os.path.exists(args.snapshot) and not args.force:
        print(f"error: snapshot target exists: {args.snapshot}  "
              f"(use --force to overwrite)", file=sys.stderr)
        return 2

    if args.exclude_file:
        try:
            with open(args.exclude_file) as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        args.exclude.append(line)
        except OSError as e:
            print(f"error: cannot read exclude file: {e}", file=sys.stderr)
            return 2

    t0 = time.monotonic()
    s = Scanner(root, follow=args.follow, apparent=args.apparent,
                excludes=args.exclude, one_fs=not args.cross_fs)
    if args.duplicates:
        s.track_inodes = True
    if args.by_owner:
        s.track_owner = True   # uid/gid recording only pays its cost on this path
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else args.jobs
    s.scan(jobs=jobs, progress=args.progress)
    elapsed = time.monotonic() - t0

    if args.interactive:
        rc = run_interactive(s, args.top, args.min_bytes)
    elif args.duplicates:
        emit_duplicates(s, args.min_size)
    elif args.recent_days is not None:
        emit_recent(s, args.recent_days, args.top)
    elif args.by_type:
        emit_by_type(s, args.top, args.min_bytes)
    elif args.by_owner:
        emit_by_owner(s, args.top)
    elif args.prom:
        emit_prom(s, elapsed, args.top)
    elif args.prom_out:
        write_prom(s, args.prom_out, elapsed, args.top)
    elif args.tree_csv:
        emit_tree_csv(s)
    elif args.tree_json:
        emit_tree_json(s)
    elif args.json:
        emit_json(s, args.min_bytes, elapsed)
    elif args.csv:
        emit_csv(s, args.min_bytes)
    elif args.tree:
        emit_tree(s, args.depth, args.bar_width)
    else:
        emit_table(s, args.top, args.min_bytes)
    if args.snapshot:
        write_snapshot(s, args.snapshot, elapsed, force=args.force)
    return rc if args.interactive else 0


if __name__ == "__main__":
    sys.exit(main())
