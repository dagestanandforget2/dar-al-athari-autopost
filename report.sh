#!/usr/bin/env bash
# Usage: ./report.sh <automation> <success|failed|skipped> "short details"
# Example: ./report.sh daily_video success "Noon TikTok video published"
# Tells the Shelf Locator dashboard how an automation run went. The push starts the
# "Dashboard reports" workflow, which holds the secret and sends it within about a minute.
# This repo is public: never put names, emails, tokens or customer data in the details.
set -euo pipefail
cd "$(dirname "$0")"
automation="${1:?usage: ./report.sh <automation> <success|failed|skipped> \"details\"}"
status="${2:?status must be success, failed or skipped}"
details="${3:-}"
case "$status" in success|failed|skipped) ;; *) echo "status must be success, failed or skipped" >&2; exit 2;; esac
[[ "$automation" =~ ^[a-z][a-z0-9_]{1,39}$ ]] || { echo "automation must be lowercase letters, digits and underscores" >&2; exit 2; }
f="reports/$(date -u +%Y%m%d-%H%M%S)-$RANDOM.json"
AUTOMATION="$automation" STATUS="$status" DETAILS="$details" python3 -c '
import json, os, sys
json.dump({"automation": os.environ["AUTOMATION"], "status": os.environ["STATUS"],
           "details": os.environ["DETAILS"][:300]}, sys.stdout, ensure_ascii=False)
' > "$f"
git add "$f"
git -c user.name="dar-al-athari-tasks" -c user.email="autopost-bot@users.noreply.github.com" commit -q -m "report: queue $automation $status"
for i in 1 2 3 4; do
  git push -q origin HEAD:main && { echo "Report queued: $f"; exit 0; }
  git pull -q --rebase origin main || true
  sleep 3
done
echo "Could not push the report" >&2
exit 1
