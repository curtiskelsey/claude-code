#!/usr/bin/env bash
# Summarize tool usage from Claude Code audit logs.
#
# Usage:
#   summarize_usage.sh              # Today's usage
#   summarize_usage.sh 7            # Last 7 days
#   summarize_usage.sh 1 sessions   # Today with session breakdown

set -euo pipefail

DAYS="${1:-1}"
MODE="${2:-summary}"
AUDIT_DIR="${HOME}/.claude"

# Collect audit files for the requested date range
files=()
for i in $(seq 0 $((DAYS - 1))); do
  if [[ "$OSTYPE" == "darwin"* ]]; then
    date_str=$(date -v-"${i}d" +%Y-%m-%d)
  else
    date_str=$(date -d "-${i} days" +%Y-%m-%d)
  fi
  f="${AUDIT_DIR}/tool-audit-${date_str}.jsonl"
  if [[ -f "$f" ]]; then
    files+=("$f")
  fi
done

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No audit files found for the last ${DAYS} day(s)."
  exit 0
fi

echo "=== Claude Code Tool Usage (last ${DAYS} day(s)) ==="
echo ""

# Total entries
total=$(cat "${files[@]}" | wc -l | tr -d ' ')
echo "Total tool calls: ${total}"

# Unique sessions
sessions=$(jq -r '.session' "${files[@]}" | sort -u | wc -l | tr -d ' ')
echo "Unique sessions:  ${sessions}"

# Failures
failures=$(jq -r 'select(.success == false) | .tool' "${files[@]}" 2>/dev/null | wc -l | tr -d ' ')
echo "Failures:         ${failures}"
echo ""

# Tool frequency
echo "--- Tool Frequency ---"
jq -r '.tool' "${files[@]}" | sort | uniq -c | sort -rn | head -20

if [[ "$MODE" == "sessions" ]]; then
  echo ""
  echo "--- Sessions ---"
  for session in $(jq -r '.session' "${files[@]}" | sort -u); do
    short="${session:0:8}"
    count=$(jq -r "select(.session == \"${session}\") | .tool" "${files[@]}" | wc -l | tr -d ' ')
    first=$(jq -r "select(.session == \"${session}\") | .timestamp" "${files[@]}" | head -1)
    last=$(jq -r "select(.session == \"${session}\") | .timestamp" "${files[@]}" | tail -1)
    echo "  ${short}..  ${count} calls  (${first} - ${last})"
  done
fi
