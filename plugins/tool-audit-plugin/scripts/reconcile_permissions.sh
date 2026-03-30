#!/usr/bin/env bash
# Reconcile tool audit logs with settings.json permission rules.
#
# Cross-references actual tool usage against allow/deny rules to identify:
#   - Covered:   tool calls matching an allow rule (auto-approved)
#   - Prompted:  tool calls matching no rule (required user approval)
#   - Stale:     allow rules with zero matches (candidates for removal)
#   - Denied:    failed calls by tool name
#   - Suggested: new allow rules based on frequently-prompted commands
#
# Usage:
#   reconcile_permissions.sh              # Last 7 days, global settings
#   reconcile_permissions.sh 30           # Last 30 days
#   reconcile_permissions.sh 7 /path/to/settings.json
#
# Compatible with bash 3.2+ (macOS default).

set -euo pipefail

DAYS="${1:-7}"
SETTINGS="${2:-${HOME}/.claude/settings.json}"
AUDIT_DIR="${HOME}/.claude"

# ── Validate inputs ──────────────────────────────────────────────────────────
if [[ ! -f "$SETTINGS" ]]; then
  echo "Settings file not found: ${SETTINGS}"
  exit 1
fi

# ── Collect audit files ──────────────────────────────────────────────────────
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

# ── Temp files ───────────────────────────────────────────────────────────────
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

SIGS="$tmpdir/sigs"           # count\tsignature
ALLOW="$tmpdir/allow"         # one rule per line
DENY="$tmpdir/deny"           # one rule per line
COVERED="$tmpdir/covered"     # count\tsignature\trule
PROMPTED="$tmpdir/prompted"   # count\tsignature
STALE="$tmpdir/stale"         # one rule per line
MATCHED="$tmpdir/matched"     # rules that matched (one per line, may repeat)

# ── Extract permission rules ────────────────────────────────────────────────
jq -r '.permissions.allow[]? // empty' "$SETTINGS" 2>/dev/null > "$ALLOW" || true
jq -r '.permissions.deny[]? // empty'  "$SETTINGS" 2>/dev/null > "$DENY"  || true

# ── Build unique tool signatures with counts ─────────────────────────────────
# Bash entries become "Bash(command)"; others just use tool name.
# Skip failed entries (no params). Tolerates malformed JSON via fromjson?.
# Collapse newlines in Bash commands (multiline python scripts etc.) into spaces
# so each audit entry produces exactly one signature line.
cat "${files[@]}" | jq -R -r '
  fromjson? //empty |
  if .success == false then empty
  elif .tool == "Bash" and .params.command then
    "Bash(" + (.params.command | gsub("\n"; " ") | gsub("  +"; " ")) + ")"
  else .tool
  end
' 2>/dev/null | sort | uniq -c | sort -rn > "$SIGS"

total_calls=$(awk '{s+=$1} END {print s+0}' "$SIGS")
total_sigs=$(wc -l < "$SIGS" | tr -d ' ')

# ── Classify each signature ─────────────────────────────────────────────────
> "$COVERED"
> "$PROMPTED"
> "$MATCHED"

while IFS= read -r line; do
  count=$(echo "$line" | awk '{print $1}')
  # Strip leading whitespace and count to get the signature
  sig=$(echo "$line" | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//')

  matched_allow=""
  matched_deny=""

  # Check allow rules
  while IFS= read -r rule; do
    [[ -z "$rule" ]] && continue
    # Glob match: unquoted RHS enables pattern matching
    # shellcheck disable=SC2053
    if [[ "$sig" == $rule ]]; then
      matched_allow="$rule"
      echo "$rule" >> "$MATCHED"
      break
    fi
  done < "$ALLOW"

  # Check deny rules if not already allowed
  if [[ -z "$matched_allow" ]]; then
    while IFS= read -r rule; do
      [[ -z "$rule" ]] && continue
      # shellcheck disable=SC2053
      if [[ "$sig" == $rule ]]; then
        matched_deny="$rule"
        break
      fi
    done < "$DENY"
  fi

  if [[ -n "$matched_allow" ]]; then
    printf '%s\t%s\t%s\n' "$count" "$sig" "$matched_allow" >> "$COVERED"
  elif [[ -n "$matched_deny" ]]; then
    # Deny-matched calls that still succeeded — unusual but track them
    printf '%s\t%s\t%s\n' "$count" "$sig" "$matched_deny" >> "$COVERED"
  else
    printf '%s\t%s\n' "$count" "$sig" >> "$PROMPTED"
  fi
done < "$SIGS"

# ── Stale rules ─────────────────────────────────────────────────────────────
> "$STALE"
while IFS= read -r rule; do
  [[ -z "$rule" ]] && continue
  if ! grep -qxF "$rule" "$MATCHED" 2>/dev/null; then
    echo "$rule" >> "$STALE"
  fi
done < "$ALLOW"

# ── Failures summary ────────────────────────────────────────────────────────
failure_count=$(cat "${files[@]}" | jq -R -r '
  fromjson? //empty | select(.success == false) | .tool
' 2>/dev/null | wc -l | tr -d ' ')

# ═════════════════════════════════════════════════════════════════════════════
# OUTPUT REPORT
# ═════════════════════════════════════════════════════════════════════════════

if [[ "$OSTYPE" == "darwin"* ]]; then
  start_date=$(date -v-"$((DAYS - 1))d" +%Y-%m-%d)
  end_date=$(date +%Y-%m-%d)
else
  start_date=$(date -d "-$((DAYS - 1)) days" +%Y-%m-%d)
  end_date=$(date +%Y-%m-%d)
fi

echo "=== Permission Reconciliation Report ==="
echo "Period: ${start_date} to ${end_date} (${DAYS} days)"
echo "Settings: ${SETTINGS}"
echo "Audit files found: ${#files[@]}"
echo "Total tool calls analyzed: ${total_calls}"
echo "Unique tool signatures: ${total_sigs}"
echo ""

# ── Covered by Allow Rules ───────────────────────────────────────────────────
covered_count=$(awk -F'\t' '{s+=$1} END {print s+0}' "$COVERED")
echo "--- Covered by Allow Rules (${covered_count} calls, auto-approved) ---"
if [[ -s "$COVERED" ]]; then
  # Aggregate by rule: sum counts and collect examples per rule using awk
  awk -F'\t' '
    {
      rule = $3
      count = $1
      sig = $2
      rule_count[rule] += count
      if (!(rule in rule_example_count)) rule_example_count[rule] = 0
      if (rule_example_count[rule] < 3) {
        rule_example_count[rule]++
        examples[rule, rule_example_count[rule]] = sig
      }
    }
    END {
      # Sort by count descending — collect into array and sort
      n = 0
      for (r in rule_count) {
        n++
        sorted_rules[n] = r
        sorted_counts[n] = rule_count[r]
      }
      # Simple insertion sort
      for (i = 2; i <= n; i++) {
        key_c = sorted_counts[i]
        key_r = sorted_rules[i]
        j = i - 1
        while (j >= 1 && sorted_counts[j] < key_c) {
          sorted_counts[j+1] = sorted_counts[j]
          sorted_rules[j+1] = sorted_rules[j]
          j--
        }
        sorted_counts[j+1] = key_c
        sorted_rules[j+1] = key_r
      }
      for (i = 1; i <= n; i++) {
        r = sorted_rules[i]
        printf "  %dx  Rule: %s\n", sorted_counts[i], r
        for (e = 1; e <= rule_example_count[r]; e++) {
          ex = examples[r, e]
          if (length(ex) > 80) ex = substr(ex, 1, 77) "..."
          printf "         e.g. %s\n", ex
        }
      }
    }
  ' "$COVERED"
else
  echo "  (none)"
fi
echo ""

# ── User-Prompted ────────────────────────────────────────────────────────────
prompted_count=$(awk -F'\t' '{s+=$1} END {print s+0}' "$PROMPTED")
echo "--- User-Prompted (${prompted_count} calls, required manual approval) ---"
if [[ -s "$PROMPTED" ]]; then
  while IFS=$'\t' read -r count sig; do
    if [[ ${#sig} -gt 80 ]]; then
      sig="${sig:0:77}..."
    fi
    printf '  %sx  %s\n' "$count" "$sig"
  done < "$PROMPTED"
else
  echo "  (none — all calls matched a permission rule)"
fi
echo ""

# ── Stale Allow Rules ───────────────────────────────────────────────────────
stale_count=$(wc -l < "$STALE" | tr -d ' ')
echo "--- Stale Allow Rules (${stale_count} rules with zero matches) ---"
if [[ -s "$STALE" ]]; then
  while IFS= read -r rule; do
    echo "  ${rule}"
  done < "$STALE"
  echo ""
  echo "  These rules had no matching tool calls in the period."
  echo "  Consider removing them, or extend the date range to check."
else
  echo "  (none — all allow rules matched at least one tool call)"
fi
echo ""

# ── Failed Tool Calls ───────────────────────────────────────────────────────
echo "--- Failed Tool Calls (${failure_count} total) ---"
if [[ "$failure_count" -gt 0 ]]; then
  echo "  Note: Failed entries lack command parameters in audit logs."
  echo "  Cannot definitively match to deny rules."
  echo ""
  cat "${files[@]}" | jq -R -r '
    fromjson? //empty | select(.success == false) | .tool
  ' 2>/dev/null | sort | uniq -c | sort -rn | while read -r fcount ftool; do
    echo "  ${fcount}x  ${ftool}"
  done
else
  echo "  (none)"
fi
echo ""

# ── Suggested New Allow Rules ────────────────────────────────────────────────
echo "--- Suggested New Allow Rules ---"

# Build suggestions from frequently-prompted Bash commands
SUGGESTIONS="$tmpdir/suggestions"
> "$SUGGESTIONS"

# Extract base commands from prompted Bash entries and aggregate
if [[ -s "$PROMPTED" ]]; then
  awk -F'\t' '
    $2 ~ /^Bash\(/ {
      # Extract base command (first word inside Bash(...))
      cmd = $2
      sub(/^Bash\(/, "", cmd)
      sub(/\)$/, "", cmd)
      # Get first word
      split(cmd, parts, " ")
      base = parts[1]
      counts[base] += $1
    }
    END {
      for (b in counts) {
        if (counts[b] >= 3) {
          printf "%d\tBash(%s *)\n", counts[b], b
        }
      }
    }
  ' "$PROMPTED" | sort -rn > "$SUGGESTIONS"
fi

# Filter out suggestions that duplicate existing allow rules
has_suggestions=false
if [[ -s "$SUGGESTIONS" ]]; then
  while IFS=$'\t' read -r scount srule; do
    already_covered=false
    while IFS= read -r existing; do
      [[ -z "$existing" ]] && continue
      if [[ "$srule" == "$existing" ]]; then
        already_covered=true
        break
      fi
    done < "$ALLOW"
    if [[ "$already_covered" == false ]]; then
      has_suggestions=true
      printf '  "%s" — would cover %s prompted calls\n' "$srule" "$scount"
    fi
  done < "$SUGGESTIONS"
fi

# Non-Bash tools prompted frequently
if [[ -s "$PROMPTED" ]]; then
  while IFS=$'\t' read -r count sig; do
    if [[ "$sig" != Bash\(* ]] && [[ $count -ge 3 ]]; then
      has_suggestions=true
      printf '  "%s" — would cover %s prompted calls\n' "$sig" "$count"
    fi
  done < "$PROMPTED"
fi

if [[ "$has_suggestions" == false ]]; then
  echo "  (no high-frequency prompted tools to suggest)"
fi
echo ""

echo "=== End of Report ==="
