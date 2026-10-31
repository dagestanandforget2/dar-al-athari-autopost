"""
Sends owner alerts from the scheduled tasks to your Telegram DMs.

A scheduled task (or anything else) adds a text file to notify/ and pushes it.
The push starts .github/workflows/notify.yml, which runs this script: every
notify/*.txt file is sent to TELEGRAM_OWNER_ID as a DM from the bot, then moved
to notify/sent/ so it is never sent twice.

Use notify.sh to create the file:   ./notify.sh "Blog draft is ready for your approval"
"""

import glob
import os
import shutil
import subprocess

import requests

API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
OWNER = os.environ["TELEGRAM_OWNER_ID"].strip()

files = sorted(glob.glob("notify/*.txt"))
if not files:
    print("Nothing to send.")
    raise SystemExit(0)

failed = 0
for path in files:
    text = open(path, encoding="utf-8").read().strip()[:4000] or "(empty alert)"
    r = requests.post(f"{API}/sendMessage", timeout=60, data={
        "chat_id": OWNER,
        "text": text,
        "disable_web_page_preview": "true",
    }).json()
    if r.get("ok"):
        shutil.move(path, os.path.join("notify/sent", os.path.basename(path)))
        print("sent", path)
    else:
        failed += 1
        print("FAILED", path, r)

subprocess.run(["git", "add", "-A", "notify"], check=True)
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
    subprocess.run(["git", "commit", "-q", "-m", "notify: sent alerts [skip ci]"], check=True)
    for _ in range(3):
        if subprocess.run(["git", "push", "-q"]).returncode == 0:
            break
        subprocess.run(["git", "pull", "-q", "--rebase"], check=True)

raise SystemExit(1 if failed else 0)
