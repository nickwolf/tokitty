#!/usr/bin/env bash
# Run the Linux bundle's --self-check in a bare ubuntu:22.04 container, the
# nearest thing to a user's clean machine (spec decision 8).
#
#   freeze/clean_system_check.sh <extracted-dir>
#
# <extracted-dir> holds the tokitty/ directory from the Linux archive. Needs
# docker and a bundle built against glibc 2.35 or older. Two passes:
#   1. with libegl1 (the one library the README asks for): every check passes.
#   2. without it: exit non-zero, and the failed checks are exactly qt, qt_root
#      and tk_root, so a missing libegl1 shows up as the Qt checks and nothing else.
# Exits non-zero with a message on any mismatch.
set -u

if [ $# -ne 1 ] || [ ! -x "$1/tokitty/tokitty" ]; then
  echo "usage: $0 <extracted-dir> (containing tokitty/tokitty)" >&2
  exit 2
fi
bundle="$(cd "$1" && pwd)"
image="${CLEAN_SYSTEM_IMAGE:-ubuntu:22.04}"
base_packages="xvfb xauth ca-certificates"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

# $1 = label, $2 = extra apt packages. Writes $out/$1.json and $out/$1.rc.
run_pass() {
  docker run --rm -v "$bundle:/b:ro" "$image" bash -c "
    set -u
    apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $base_packages $2 >/dev/null 2>&1 || { echo 'apt-get failed' >&2; exit 99; }
    cd /b/tokitty
    # xvfb-run sends the program's stderr to stdout, so the redirect goes
    # inside it: stdout must hold the report alone, as an updater reads it.
    TOKITTY_SELF_CHECK_TK=1 xvfb-run -a sh -c './tokitty --self-check 2>/tmp/err'
    rc=\$?
    tail -c 600 /tmp/err >&2
    exit \$rc
  " >"$out/$1.json" 2>"$out/$1.err"
  echo $? >"$out/$1.rc"
}

# $1 = label. Prints the sorted failed check names, one per line.
failed_checks() {
  python3 -I -c '
import json, sys
data = json.load(open(sys.argv[1]))
for name, check in sorted(data["checks"].items()):
    if not check.get("ok"):
        print(name)
' "$out/$1.json"
}

fail() {
  echo "clean_system_check: FAIL: $1" >&2
  exit 1
}

run_pass with-libegl1 libegl1
rc="$(cat "$out/with-libegl1.rc")"
cat "$out/with-libegl1.json"
if [ "$rc" != 0 ]; then
  cat "$out/with-libegl1.err" >&2
  fail "with libegl1 the self-check exited $rc, expected 0. Failed checks: $(failed_checks with-libegl1 2>&1 | tr '\n' ' ')"
fi
# Exit 0 alone is not a report: require the JSON and the window checks in it.
python3 -I -c '
import json, sys
data = json.load(open(sys.argv[1]))
checks = data["checks"]
missing = [n for n in ("qt", "qt_root", "tk_root") if n not in checks]
bad = sorted(n for n, c in checks.items() if not c.get("ok"))
if missing or bad or data.get("toolkits") != ["qt"]:
    sys.exit("missing %s, failed %s, toolkits %s" % (missing, bad, data.get("toolkits")))
' "$out/with-libegl1.json" || fail "with libegl1 the self-check did not print a complete passing report"
echo "clean_system_check: pass 1 ok (self-check passes with libegl1)"

run_pass without-libegl1 ""
rc="$(cat "$out/without-libegl1.rc")"
cat "$out/without-libegl1.json"
if [ "$rc" = 0 ] || [ "$rc" = 99 ]; then
  cat "$out/without-libegl1.err" >&2
  fail "without libegl1 the self-check exited $rc, expected a non-zero self-check failure (99 means apt failed)"
fi
got="$(failed_checks without-libegl1 2>&1 | tr '\n' ' ')"
want="qt qt_root tk_root "
if [ "$got" != "$want" ]; then
  fail "without libegl1 the failed checks were [${got}], expected [${want}]"
fi
echo "clean_system_check: pass 2 ok (without libegl1 only qt, qt_root and tk_root fail)"
