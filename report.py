"""
Passes automation run reports on to the Shelf Locator dashboard.

A scheduled task adds a JSON file to reports/ (with ./report.sh) and pushes it. The push
starts .github/workflows/report.yml, which runs this script: every reports/*.json file is
POSTed to the dashboard, then moved to reports/sent/ so it is never sent twice.
"""

import glob
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

URL = "https://shelf-locator.vercel.app/api/automations/run"
# The stored secret can carry an invisible byte-order mark or line ending; strip them.
SECRET = os.environ.get("AUTOMATION_SECRET", "").replace("﻿", "").strip()

files = sorted(glob.glob("reports/*.json"))
if not files:
    print("Nothing to report.")
    raise SystemExit(0)
if not SECRET:
    print("AUTOMATION_SECRET is not set; leaving reports queued.")
    raise SystemExit(1)

failed = 0
for path in files:
    try:
        data = json.load(open(path, encoding="utf-8"))
        body = json.dumps({"automation": str(data["automation"]),
                           "status": str(data["status"]),
                           "details": str(data.get("details", ""))[:300]}).encode()
    except (ValueError, KeyError) as e:
        print("BAD FILE", path, e)       # unreadable: move it aside so it doesn't block others
        shutil.move(path, os.path.join("reports/sent", "bad-" + os.path.basename(path)))
        continue
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + SECRET})
    try:
        status = urllib.request.urlopen(req, timeout=20).status
        print("reported", path, status)
        shutil.move(path, os.path.join("reports/sent", os.path.basename(path)))
    except urllib.error.HTTPError as e:
        print("REJECTED", path, e.code)
        if e.code in (400, 422):          # the dashboard will never accept it; don't retry forever
            shutil.move(path, os.path.join("reports/sent", "rejected-" + os.path.basename(path)))
        else:
            failed += 1
    except Exception as e:                # network trouble: keep it queued for the next run
        failed += 1
        print("FAILED", path, e)

subprocess.run(["git", "add", "-A", "reports"], check=True)
if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
    subprocess.run(["git", "commit", "-q", "-m", "report: sent [skip ci]"], check=True)
    for _ in range(3):
        if subprocess.run(["git", "push", "-q"]).returncode == 0:
            break
        subprocess.run(["git", "pull", "-q", "--rebase"], check=True)

raise SystemExit(1 if failed else 0)
