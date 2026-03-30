#!/bin/bash
PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "$PLUGIN_DIR/.venv/bin/activate"
exec "$PLUGIN_DIR/.venv/bin/python3" "$PLUGIN_DIR/servers/calendar_server.py" "$@"
