"""Publish the telemetry dashboards to Firebase Hosting.

    python deploy_dashboard.py            # build site/public and deploy
    python deploy_dashboard.py --build    # build only (preview: open site/public/index.html)

The site holds the newest day's dashboard at / and every day at /<date>/.
Firebase settings live in site/ (firebase.json, .firebaserc). The site is
PUBLIC: anyone with the link can see it.
Needs the Firebase CLI (npm i -g firebase-tools) and `firebase login`.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import telemetry as tm

SITE = Path(__file__).parent / "site"
PUBLIC = SITE / "public"


def build():
    days = sorted(p.name for p in tm.PROCESSED.iterdir() if (p / "dashboard.html").exists())
    if not days:
        sys.exit("no dashboards yet - run telemetry.py and analysis.py first")
    if PUBLIC.exists():
        shutil.rmtree(PUBLIC)
    PUBLIC.mkdir(parents=True)
    for d in days:
        (PUBLIC / d).mkdir()
        shutil.copy(tm.PROCESSED / d / "dashboard.html", PUBLIC / d / "index.html")
    shutil.copy(tm.PROCESSED / days[-1] / "dashboard.html", PUBLIC / "index.html")
    print(f"built {len(days)} day(s), newest {days[-1]} -> {PUBLIC}")


def main():
    build()
    if "--build" in sys.argv:
        return
    # shell=True so Windows finds firebase.cmd
    subprocess.run("firebase deploy --only hosting", cwd=SITE, shell=True, check=True)


if __name__ == "__main__":
    main()
