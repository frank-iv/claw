#!/bin/bash
set -euo pipefail

STAGE="$1"
REPO_PATH="$2"

cd "$REPO_PATH"

case "$STAGE" in
  tests)
    pytest --json-report --json-report-file=/output/tests.json -q . || true
    ;;
  lint)
    ruff check --output-format=json . > /output/lint.json || true
    ;;
  security)
    bandit -r . -f json -o /output/bandit.json || true
    semgrep --config=auto --json -o /output/semgrep.json . || true
    jq -n --slurpfile b /output/bandit.json --slurpfile s /output/semgrep.json \
      '{bandit: $b[0], semgrep: $s[0]}' > /output/security.json
    ;;
  *)
    echo "{\"error\": \"unknown stage: $STAGE\"}" > /output/error.json
    exit 1
    ;;
esac
