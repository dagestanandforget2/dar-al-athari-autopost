#!/usr/bin/env bash
# Usage: ./notify.sh "message text"
# Queues a Telegram DM to the owner. The push starts the "Telegram alerts" workflow,
# which sends it within about a minute.
set -euo pipefail
cd "$(dirname "$0")"
msg="${1:?usage: ./notify.sh \"message\"}"
f="notify/$(date -u +%Y%m%d-%H%M%S)-$RANDOM.txt"
printf '%s\n' "$msg" > "$f"
git add "$f"
git -c user.name="dar-al-athari-tasks" -c user.email="autopost-bot@users.noreply.github.com" commit -q -m "notify: queue alert"
for i in 1 2 3 4; do
  git push -q origin HEAD:main && { echo "Alert queued: $f"; exit 0; }
  git pull -q --rebase origin main || true
  sleep 3
done
echo "Could not push the alert" >&2
exit 1
