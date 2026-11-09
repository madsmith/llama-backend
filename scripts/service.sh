#!/usr/bin/env bash
# Register / unregister llama-manager as a background service.
#
#   macOS:  per-user LaunchAgent  (~/Library/LaunchAgents), starts at login
#   Linux:  systemd system unit   (/etc/systemd/system), starts at boot,
#           runs as the invoking user; needs sudo
#
# Usage: scripts/service.sh register|unregister|status
# Env:   LLAMA_MANAGER_HOST (default 127.0.0.1), LLAMA_MANAGER_PORT (default 8000),
#        LLAMA_MANAGER_ARGS (extra CLI args, e.g. "--verbose")
#
# The service runs this repo's .venv/bin/llama-manager with the repo as its
# working directory, since server_config.json and frontend/dist live here.
# The current shell's PATH is baked into the service so llama-server (when
# llama_server_path is empty) resolves the same way it does interactively;
# re-register after moving llama-server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$ROOT/.venv/bin/llama-manager"
HOST="${LLAMA_MANAGER_HOST:-127.0.0.1}"
PORT="${LLAMA_MANAGER_PORT:-8000}"
EXTRA_ARGS="${LLAMA_MANAGER_ARGS:-}"

LABEL="com.llama-manager.server"
UNIT="llama-manager.service"

die() { echo "error: $*" >&2; exit 1; }

check_built() {
  [[ -x "$BIN" ]] || die "$BIN not found; run 'just build' first"
  [[ -f "$ROOT/frontend/dist/index.html" ]] || die "frontend/dist missing; run 'just build' first"
}

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

# ----- macOS (launchd) -----------------------------------------------------

mac_plist() { echo "$HOME/Library/LaunchAgents/$LABEL.plist"; }
mac_domain() { echo "gui/$(id -u)"; }

mac_register() {
  check_built
  local plist logdir args_xml=""
  plist="$(mac_plist)"
  logdir="$HOME/Library/Logs/llama-manager"
  mkdir -p "$(dirname "$plist")" "$logdir"

  local a
  for a in "$BIN" --host "$HOST" --port "$PORT" $EXTRA_ARGS; do
    args_xml+="        <string>$(xml_escape "$a")</string>"$'\n'
  done

  cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
$args_xml    </array>
    <key>WorkingDirectory</key>
    <string>$(xml_escape "$ROOT")</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$(xml_escape "$PATH")</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>$(xml_escape "$logdir/stdout.log")</string>
    <key>StandardErrorPath</key>
    <string>$(xml_escape "$logdir/stderr.log")</string>
</dict>
</plist>
PLIST

  # Replace any previous registration, then load and start.
  launchctl bootout "$(mac_domain)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$(mac_domain)" "$plist"
  launchctl enable "$(mac_domain)/$LABEL"
  echo "Registered $LABEL -> http://$HOST:$PORT"
  echo "Logs: $logdir"
}

mac_unregister() {
  local plist
  plist="$(mac_plist)"
  launchctl bootout "$(mac_domain)/$LABEL" 2>/dev/null || true
  rm -f "$plist"
  echo "Unregistered $LABEL"
}

mac_status() {
  launchctl print "$(mac_domain)/$LABEL" 2>/dev/null | grep -E '^\s*(state|pid|last exit code) =' \
    || echo "$LABEL is not registered"
}

# ----- Linux (systemd) -----------------------------------------------------

linux_unit_path() { echo "/etc/systemd/system/$UNIT"; }

sudo_cmd() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

linux_register() {
  check_built
  command -v systemctl >/dev/null || die "systemctl not found; systemd is required"
  local user group exec
  user="${SUDO_USER:-$(id -un)}"
  group="$(id -gn "$user")"
  exec="$BIN --host $HOST --port $PORT $EXTRA_ARGS"

  sudo_cmd tee "$(linux_unit_path)" >/dev/null <<UNITFILE
[Unit]
Description=Llama Server Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$user
Group=$group
WorkingDirectory=$ROOT
Environment="PATH=$PATH"
ExecStart=$exec
Restart=on-failure
RestartSec=5
# Stop spawned llama-server children with the manager.
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
UNITFILE

  sudo_cmd systemctl daemon-reload
  sudo_cmd systemctl enable "$UNIT"
  sudo_cmd systemctl restart "$UNIT"
  echo "Registered $UNIT (user $user) -> http://$HOST:$PORT"
  echo "Logs: journalctl -u $UNIT -f"
}

linux_unregister() {
  command -v systemctl >/dev/null || die "systemctl not found"
  sudo_cmd systemctl disable --now "$UNIT" 2>/dev/null || true
  sudo_cmd rm -f "$(linux_unit_path)"
  sudo_cmd systemctl daemon-reload
  echo "Unregistered $UNIT"
}

linux_status() {
  systemctl status "$UNIT" --no-pager 2>/dev/null || echo "$UNIT is not registered or not running"
}

# ----- dispatch --------------------------------------------------------------

case "$(uname -s)" in
  Darwin) os=mac ;;
  Linux) os=linux ;;
  *) die "unsupported OS: $(uname -s)" ;;
esac

case "${1:-}" in
  register) "${os}_register" ;;
  unregister) "${os}_unregister" ;;
  status) "${os}_status" ;;
  *) echo "usage: $0 register|unregister|status" >&2; exit 2 ;;
esac
