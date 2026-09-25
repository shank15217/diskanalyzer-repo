#!/usr/bin/env bash
# Rebuild the diskanalyze golden fixture (deterministic sizes, planted edge cases).
# Usage: make_fixture.sh [target-dir]   (default: /tmp/diskanalyzer-fixture)
#
# Contents:
#   big/                      a.bin 10,000,000 | b.bin 5,000,000 | c.bin 1,000,000
#   med/                      d.bin 700,000    | e.bin 300,000
#   dir with spaces/          "weird file.name" 123,456
#   uni/                      café-データ.bin  55,555
#   hard/                     a_orig.bin 4,096 + b_link.bin (hard link, same inode)
#   empty/                    (no entries)
#   links/                    s1 -> s2, s2 -> s1 (broken loop), sfile -> ../big/a.bin
#   locked/                   secret.bin 10,000, dir chmod 000 (permission-denied test)
#
# Non-root users can read everything except locked/ (world-readable fixture).
set -euo pipefail
ROOT="${1:-/tmp/diskanalyzer-fixture}"
rm -rf "$ROOT"
mkdir -p "$ROOT"/big "$ROOT"/med "$ROOT"/hard "$ROOT"/empty "$ROOT"/links "$ROOT"/locked \
         "$ROOT"/"dir with spaces" "$ROOT"/uni

dd if=/dev/zero of="$ROOT/big/a.bin"             bs=1000000 count=10 status=none  # 10,000,000
dd if=/dev/zero of="$ROOT/big/b.bin"             bs=500000  count=10 status=none  #  5,000,000
dd if=/dev/zero of="$ROOT/big/c.bin"             bs=100000  count=10 status=none  #  1,000,000
dd if=/dev/zero of="$ROOT/med/d.bin"             bs=70000   count=10 status=none  #   700,000
dd if=/dev/zero of="$ROOT/med/e.bin"             bs=30000   count=10 status=none  #   300,000
dd if=/dev/zero of="$ROOT/dir with spaces/weird file.name" bs=123456 count=1 status=none # 123,456
dd if=/dev/zero of="$ROOT/uni/café-データ.bin"   bs=55555   count=1  status=none  #    55,555
dd if=/dev/zero of="$ROOT/hard/a_orig.bin"       bs=4096    count=1  status=none  #     4,096
ln "$ROOT/hard/a_orig.bin" "$ROOT/hard/b_link.bin"
ln -s s2 "$ROOT/links/s1"
ln -s s1 "$ROOT/links/s2"
ln -s ../big/a.bin "$ROOT/links/sfile"
dd if=/dev/zero of="$ROOT/locked/secret.bin"     bs=10000   count=1  status=none  #    10,000

chmod -R a+rX "$ROOT"
chmod 000 "$ROOT/locked"
echo "fixture ready at $ROOT"
