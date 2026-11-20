#!/usr/bin/env bash
# Usage: ./report.sh <automation> <status> "short details"
# Example: ./report.sh daily_video success "Noon TikTok video published"
# Tells the Shelf Locator dashboard how an automation run went. The push starts the
# "Dashboard reports" workflow, which holds the secret and sends it within about a minute.
#
# Status: success, failed or skipped. For trial_reels only: waiting, scheduled, published,
# failed or skipped, and two optional texts passed as environment variables:
#   SUMMARY="how the reels are performing (up to 1,500 characters)"
#   CHANGE="what the formula changed to and why (up to 500 characters)"
#   SUMMARY="..." CHANGE="..." ./report.sh trial_reels waiting "Batch for Nov 20 sent"
#
# This repo is public: never put names, emails, tokens or customer data in any of the text.
set -euo pipefail
cd "$(dirname "$0")"
automation="${1:?usage: ./report.sh <automation> <status> \"details\"}"
status="${2:?status is required}"
details="${3:-}"
[[ "$automation" =~ ^[a-z][a-z0-9_]{1,39}$ ]] || { echo "automation must be lowercase letters, digits and underscores" >&2; exit 2; }
if [ "$automation" = "trial_reels" ]; then
  case "$status" in waiting|scheduled|published|failed|skipped) ;; *) echo "trial_reels status must be waiting, scheduled, published, failed or skipped" >&2; exit 2;; esac
else
  case "$status" in success|failed|skipped) ;; *) echo "status must be success, failed or skipped" >&2; exit 2;; esac
  if [ -n "${SUMMARY:-}" ] || [ -n "${CHANGE:-}" ]; then echo "SUMMARY and CHANGE are only for trial_reels" >&2; exit 2; fi
fi
f="reports/$(date -u +%Y%m%d-%H%M%S)-$RANDOM.json"
AUTOMATION="$automation" STATUS="$status" DETAILS="$details" python3 -c '
import json, os, sys
d = {"automation": os.environ["AUTOMATION"], "status": os.environ["STATUS"],
     "details": os.environ["DETAILS"][:300]}
s, c = os.environ.get("SUMMARY", "").strip(), os.environ.get("CHANGE", "").strip()
if s: d["summary"] = s[:1500]
if c: d["change"] = c[:500]
json.dump(d, sys.stdout, ensure_ascii=False)
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
