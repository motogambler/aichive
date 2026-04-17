#!/usr/bin/env bash
set -euo pipefail

# Bash-only helper to import mitmproxy CA into the CurrentUser Windows Root store
# Uses cmd.exe to invoke certutil so you don't have to open PowerShell.

CA_PATH_HOST="$(pwd)/mitmproxy-ca.pem"
if [ ! -f "$CA_PATH_HOST" ]; then
  echo "mitmproxy-ca.pem not found in repo. Run 'make get-ca' or 'make run-in-docker' first." >&2
  exit 1
fi

# Try a few Windows-hosted environments: MSYS/Cygwin, WSL, or native PowerShell
echo "Importing mitmproxy CA into CurrentUser Trusted Root..."

if command -v cygpath >/dev/null 2>&1; then
  # Git Bash / MSYS / Cygwin
  CA_WIN_PATH=$(cygpath -w "$CA_PATH_HOST")
  echo "Detected MSYS/Cygwin. Using certutil via cmd.exe with path: $CA_WIN_PATH"
  cmd.exe /c "certutil -user -addstore Root \"$CA_WIN_PATH\""
  echo "Import complete. Restart affected applications if needed."
  exit 0
fi

# WSL: prefer wslpath -> Windows path and cmd.exe available in PATH
if [ -f /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null && command -v wslpath >/dev/null 2>&1; then
  CA_WIN_PATH=$(wslpath -w "$CA_PATH_HOST")
  echo "Detected WSL. Using certutil via cmd.exe with path: $CA_WIN_PATH"
  cmd.exe /c "certutil -user -addstore Root \"$CA_WIN_PATH\""
  echo "Import complete. Restart affected applications if needed."
  exit 0
fi

# Native Windows from Git Bash/MinGW might not have cygpath; try powershell.exe
if command -v powershell.exe >/dev/null 2>&1; then
  CA_PS_PATH="$CA_PATH_HOST"
  echo "Detected PowerShell. Using Import-Certificate with path: $CA_PS_PATH"
  powershell.exe -NoProfile -Command "Import-Certificate -FilePath '${CA_PS_PATH//'\'/'\\'}' -CertStoreLocation 'Cert:\\CurrentUser\\Root'"
  echo "Import complete. Restart affected applications if needed."
  exit 0
fi

echo "Could not auto-detect a Windows environment to import the CA."
echo "If you're on Windows, run one of these commands manually:"
echo "  cmd.exe /c \"certutil -user -addstore Root C:\\path\\to\\mitmproxy-ca.pem\""
echo "  OR run PowerShell as a user and run: Import-Certificate -FilePath C:\\path\\to\\mitmproxy-ca.pem -CertStoreLocation 'Cert:\\CurrentUser\\Root'"
echo "On macOS / Linux you must add the CA to your system or browser trust store manually (varies by distro/browser)."
exit 2
