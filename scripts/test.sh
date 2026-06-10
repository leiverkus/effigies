#!/usr/bin/env bash
# Local test runner — mirrors .github/workflows/ci.yml
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== bash syntax =="
for f in run.sh pipeline/*.sh scripts/*.sh; do bash -n "$f" && echo "  ok $f"; done

echo "== python compile =="
for f in helpers/*.py tests/*.py; do python3 -m py_compile "$f" && echo "  ok $f"; done

echo "== options.json =="
python3 -c "import json; n=len(json.load(open('options.json'))); print(f'  ok {n} options')"

echo "== unit tests =="
for t in tests/test_*.py; do echo "-- $t"; python3 "$t"; done

echo
echo "all checks passed"
