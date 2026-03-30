#!/usr/bin/env bash
# Log failed tool usage to a daily JSONL audit file.
# Receives hook event JSON on stdin from Claude Code.

AUDIT_DIR="${HOME}/.claude"
AUDIT_FILE="${AUDIT_DIR}/tool-audit-$(date +%Y-%m-%d).jsonl"

jq -c '{timestamp: (now | todate), tool: .tool_name, session: .session_id, error: .error, success: false}' \
  >> "${AUDIT_FILE}"
