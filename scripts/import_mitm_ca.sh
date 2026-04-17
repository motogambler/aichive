#!/usr/bin/env bash
set -euo pipefail

# Bash-only helper to import mitmproxy CA into the CurrentUser Windows Root store
# Uses cmd.exe to invoke certutil so you don't have to open PowerShell.

CA_PATH_HOST="$(pwd)/mitmproxy-ca.pem"
if [ ! -f "$CA_PATH_HOST" ]; then
  echo "mitmproxy-ca.pem not found in repo. Run 'make get-ca' or 'make run-in-docker' first." >&2
  exit 1
fi

# Convert to Windows path
CA_WIN_PATH=$(cygpath -w "$CA_PATH_HOST")
echo "Importing $CA_WIN_PATH into CurrentUser Trusted Root..."

cmd.exe /c "certutil -user -addstore Root \"$CA_WIN_PATH\""

echo "Import complete. You may need to restart affected applications."
