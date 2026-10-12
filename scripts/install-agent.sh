#!/usr/bin/env bash
set -euo pipefail
if [[ "$(id -u)" == 0 ]]; then
  echo 'Install as your GNOME desktop user, not root.' >&2
  exit 1
fi
sentinel_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$HOME/.config/sentinel/config.json" ]]; then
  echo 'Enroll the agent first; see README.md.' >&2
  exit 1
fi
if [[ ! -x "$sentinel_root/agent/target/release/sentinel-agent" ]]; then
  echo 'Build the release agent first.' >&2
  exit 1
fi
install -d -m 700 "$HOME/.config/sentinel"
install -d "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.local/share/applications"
install -m 755 "$sentinel_root/agent/target/release/sentinel-agent" "$HOME/.local/bin/sentinel-agent"
install -m 644 "$sentinel_root/agent/packaging/sentinel-agent.service" "$HOME/.config/systemd/user/sentinel-agent.service"
install -m 644 "$sentinel_root/agent/packaging/sentinel.desktop" "$HOME/.local/share/applications/sentinel.desktop"
systemctl --user daemon-reload
systemctl --user enable --now sentinel-agent.service
echo 'Installed. Run systemctl --user status sentinel-agent.service'
echo 'For boot monitoring before desktop login, optionally enable user lingering as documented.'
