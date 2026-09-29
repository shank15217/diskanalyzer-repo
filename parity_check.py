#!/usr/bin/env python3
"""v1.4 view-parity harness: stdout 'Top directories' == --prom
diskanalyze_dir_bytes == --json dir rollups, byte-for-byte, across roots
and flag combos. Exits 0 iff every combo passes."""
import subprocess, re, sys, json, importlib.util

spec = importlib.util.spec_from_file_location("da", "diskanalyzer.py")
da = importlib.util.module_from_spec(spec)
sys.modules["da"] = da
spec.loader.exec_module(da)


def run(root, extra=()):
    plain = subprocess.run(["python3", "diskanalyzer.py", root, *extra],
                           capture_output=True, text=True).stdout
    prom = subprocess.run(["python3", "diskanalyzer.py", root, "--prom", *extra],
                          capture_output=True, text=True).stdout
    js = json.loads(subprocess.run(["python3", "diskanalyzer.py", root, "--json", *extra],
                                   capture_output=True, text=True).stdout)
    return plain, prom, js


fails = 0
roots = ["/tmp/diskanalyzer-fixture", "/tmp/prom-edge", "/usr"]
combos = [(), ("--top", "5"), ("--apparent",), ("--top", "0"),
          ("--top", "1"), ("--jobs", "4")]
for root in roots:
    for extra in combos:
        plain, prom, js = run(root, extra)
        t = 15
        if "--top" in extra:
            t = int(extra[extra.index("--top") + 1])
        # oracle: --json dir rollups (relpath '.' = root), depth-1, tool's sort
        children = [(d["relpath"], d["total"]) for d in js["dirs"]
                    if d["relpath"] != "." and "/" not in d["relpath"]]
        children.sort(key=lambda kv: (-kv[1], kv[0]))
        exp = children[:max(t, 0)]
        # prom rows: un-escape label, strip root prefix
        pref = js["root"].rstrip("/") + "/"
        rows = re.findall(r'diskanalyze_dir_bytes\{path="((?:[^"\\]|\\.)*)"',
                          prom)
        vals = [int(m) for m in re.findall(
            r'diskanalyze_dir_bytes\{path="(?:[^"\\]|\\.)*"(?:,mode="[^"]*")?\}\s+(\d+)',
            prom)]
        # compare in the RAW (escaped) space: expected label = the tool's own
        # escape of root_prefix+relpath (escaping is idempotent-safe here)
        exp_raw = [(da._prom_escape_label(pref + rp), v) for rp, v in exp]
        got_raw = list(zip(rows, vals))
        if got_raw != exp_raw:
            fails += 1
            print(f"PROM!=JSON  {root} {extra}\n  prom={got_raw[:3]}\n  exp ={exp_raw[:3]}")
        # stdout rows
        if "Top directories" in plain:
            tbl = plain.split("Top directories")[1].split("Top ")[0]
            std = re.findall(r"^\s+([\d.,]+ [KMGT]?i?B)\s+(.+?)/$", tbl, re.M)
            exp_std = [(da.human(sz), rp) for rp, sz in exp]
            if std != exp_std:
                fails += 1
                print(f"STDOUT!=EXP {root} {extra}\n  std={std[:3]}\n  exp ={exp_std[:3]}")
        elif t != 0:
            fails += 1
            print(f"MISSING SECTION {root} {extra}")
        # prom with top 0 must emit no dir_bytes samples at all
        if t == 0 and rows:
            fails += 1
            print(f"TOP0 NOT EMPTY {root} {extra}")
print("FAILS:", fails)
sys.exit(1 if fails else 0)
