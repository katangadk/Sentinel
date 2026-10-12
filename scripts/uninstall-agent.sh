#!/usr/bin/env bash
set -euo pipefail
systemctl --user disable --now sentinel-agent.service || true
for sentinel_file in "$HOME/.local/bin/sentinel-agent" "$HOME/.config/systemd/user/sentinel-agent.service" "$HOME/.local/share/applications/sentinel.desktop"; do
  if [[ -f "$sentinel_file" ]]; then rm -- "$sentinel_file"; fi
done
systemctl --user daemon-reload
echo 'Service removed. Revoke the laptop in the dashboard to invalidate credentials.'
echo 'Private config/state were retained. To remove them: rm -r -- "$HOME/.config/sentinel"'
echo 'If you enabled lingering only for Sentinel: sudo loginctl disable-linger "$USER"'
echo 'Remove any Sentinel GeoClue allowlist stanza and optional journal group membership yourself.'
