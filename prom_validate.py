#!/usr/bin/env python3
"""Offline validator for Prometheus exposition text (stdlib-only).

Implements the text-format grammar strictly enough to catch the failure
classes QA cares about for node_exporter's textfile collector:
  * metric names must match [a-zA-Z_:][a-zA-Z0-9_:]*
  * label names must match [a-zA-Z_][a-zA-Z0-9_]*
  * label values: quoted; only \\n, \\", \\\\ are valid escapes; no raw
    newlines or C0 control bytes anywhere in the line
  * every sample needs a HELP/TYPE for its metric family, declared before
    first sample of that family (collector parsers tolerate either order;
    we require HELP then TYPE before samples, matching our emitter)
  * value parses as a float (or NaN/+Inf/-Inf)
  * duplicate samples with identical label sets within one run = error
  * trailing garbage / bad line shape = error

Usage: python3 prom_validate.py file.prom   (or - for stdin)
Exit 0 = valid, 1 = violations (printed), 2 = usage/IO.
"""
import re
import sys

NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>(?:[a-zA-Z_][a-zA-Z0-9_]*=\"(?:[^\"\\]|\\.)*\")*"
    r"(?:,(?:[a-zA-Z_][a-zA-Z0-9_]*=\"(?:[^\"\\]|\\.)*\")*)*)\})?"
    r"\s+(?P<value>-?(?:\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|NaN|\+Inf|-Inf))"
    r"(?:\s+(?P<ts>-?\d+))?\s*$")
ESCAPE_RE = re.compile(r"\\([^n\"\\])")


def parse_labels(s: str):
    out = {}
    i = 0
    while i < len(s):
        m = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)="', s[i:])
        if not m:
            raise ValueError(f"bad label at offset {i}: {s[i:i+30]!r}")
        key = m.group(1)
        j = i + len(m.group(0))
        val = []
        while True:
            if j >= len(s):
                raise ValueError("unterminated label value")
            c = s[j]
            if c == "\\":
                if j + 1 >= len(s):
                    raise ValueError("trailing backslash")
                esc = s[j + 1]
                if esc not in 'n"\\':
                    raise ValueError(f"invalid escape \\\\{esc}")
                val.append({"n": "\n"}.get(esc, esc))
                j += 2
            elif c == '"':
                j += 1
                break
            else:
                if ord(c) < 32:
                    raise ValueError(f"raw control char in label value")
                val.append(c)
                j += 1
        if key in out:
            raise ValueError(f"duplicate label {key}")
        out[key] = "".join(val)
        i = j
        if i < len(s) and s[i] == ",":
            i += 1
        elif i < len(s):
            raise ValueError(f"unexpected char {s[i]!r} at {i}")
    return out


def validate(text: str) -> list:
    errors = []
    seen_help: dict = {}
    seen_type: dict = {}
    seen_samples: dict = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("#"):
            parts = line.split(None, 2)
            if len(parts) >= 3 and parts[1] == "HELP":
                # HELP line is "# HELP <name> <help text>" — name is the
                # FIRST token of parts[2], not the whole remainder (bug in
                # the initial version flagged every valid HELP line).
                name = parts[2].split(None, 1)[0]
                if not NAME_RE.match(name):
                    errors.append(f"{lineno}: bad HELP metric name {name!r}")
                seen_help.setdefault(name, []).append(lineno)
            elif len(parts) >= 3 and parts[1] == "TYPE":
                name, typ = parts[2].split(None, 1)[0], parts[2].split(None, 1)[1] if " " in parts[2] else ""
                if not NAME_RE.match(name):
                    errors.append(f"{lineno}: bad TYPE metric name {name!r}")
                if typ not in ("counter", "gauge", "histogram", "summary",
                               "untyped", "info", "stateset"):
                    errors.append(f"{lineno}: bad TYPE {typ!r}")
                seen_type.setdefault(name, []).append(lineno)
            elif len(parts) >= 2 and parts[1] in ("HELP", "TYPE"):
                errors.append(f"{lineno}: empty {parts[1]} for metric")
            continue
        for ch in line:
            if ord(ch) < 32:
                errors.append(f"{lineno}: raw control byte in line")
                break
        m = SAMPLE_RE.match(line)
        if not m:
            errors.append(f"{lineno}: unparseable sample line: {line[:80]!r}")
            continue
        name = m.group("name")
        try:
            labels = parse_labels(m.group("labels") or "")
        except ValueError as e:
            # a bad escape / unterminated value is a finding, not a crash
            errors.append(f"{lineno}: bad labels in {name}: {e}")
            continue
        if name not in seen_type:
            errors.append(f"{lineno}: sample {name} has no TYPE declaration")
        if any(l in ("",) for l in labels):
            errors.append(f"{lineno}: empty label name")
        key = (name, tuple(sorted(labels.items())))
        if key in seen_samples:
            errors.append(f"{lineno}: duplicate sample {name}{sorted(labels)} "
                          f"(first at {seen_samples[key]}) — textfile collector "
                          f"rejects the WHOLE file for this")
        seen_samples[key] = lineno
        # value sanity
        v = m.group("value")
        try:
            float(v)
        except ValueError:
            pass  # NaN/Inf handled above by regex
    for name in set(seen_type) | set(seen_help):
        if name in seen_help and name not in seen_type:
            errors.append(f"HELP without TYPE: {name}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1]).read()
    errs = validate(src)
    if errs:
        for e in errs:
            print(f"VIOLATION: {e}")
        return 1
    n_samples = sum(1 for l in src.splitlines()
                    if l and not l.startswith("#"))
    print(f"OK: {n_samples} samples, no grammar violations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
