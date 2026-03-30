---
name: reconcile-permissions
description: Cross-reference tool audit logs with settings.json to optimize allow/deny permission rules
arguments:
  - name: days
    description: Number of days to look back (default 7)
    required: false
  - name: settings
    description: "Path to settings.json (default: ~/.claude/settings.json)"
    required: false
---

Reconcile tool audit logs against your permission rules to find optimization opportunities.

## Parameters
- **Days**: Look back {{ days | default: "7" }} day(s)
- **Settings file**: {{ settings | default: "~/.claude/settings.json" }}

## Instructions

1. Run the reconciliation script:
   ```bash
   bash ${CLAUDE_PLUGIN_ROOT}/scripts/reconcile_permissions.sh {{ days | default: "7" }} "{{ settings | default: "~/.claude/settings.json" }}"
   ```

2. Present the output to the user, organized into these sections:

   **Covered by Allow Rules** — Tool calls that matched an existing allow rule and were auto-approved. Shows which rules are actively being used, with example commands.

   **User-Prompted** — Tool calls that matched NO rule, so the user had to approve each one. These are candidates for new allow rules. Sorted by frequency.

   **Stale Allow Rules** — Allow rules that had zero matching tool calls in the audit period. These may be outdated and candidates for removal (suggest extending the date range first).

   **Failed Tool Calls** — Failures by tool name. Note: failed entries lack command parameters in audit logs, so they cannot be definitively matched to deny rules.

   **Suggested New Allow Rules** — Auto-generated rule suggestions based on frequently prompted commands (3+ occurrences). These are ready to paste into settings.json.

3. After presenting the report, offer to help the user:
   - Add suggested rules to their settings.json
   - Remove stale rules
   - Create custom rules for specific prompted commands
   - Adjust the date range for a broader or narrower view
