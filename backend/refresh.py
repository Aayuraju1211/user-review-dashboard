"""
The whole refresh in one command:

  python backend/refresh.py                 ingest -> label new -> build -> tests -> alerts
  python backend/refresh.py --skip-ingest   reuse the current reviews.json (e.g. for a local check)
  python backend/refresh.py --no-email      everything except sending the alert email

Stops at the first failure and puts every data file back as it was, so the dashboard is never half updated.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
DATA = ROOT / "data"
TOUCHED = [DATA / "reviews.json", DATA / "reviews.csv", DATA / "changes.json", DATA / "ingest_meta.json",
           DATA / "labels.json", DATA / "alerts_pending.json", DATA / "alerts_log.json",
           ROOT / "web" / "data" / "dashboard.json"]
MAX_DELETIONS = 20  # more than this in one run looks like a broken pull, not real deletions


class Stop(Exception):
    pass


def run(name: str, *args: str) -> None:
    print(f"\n== {name} ==", flush=True)
    if subprocess.run([sys.executable, *args], cwd=ROOT).returncode != 0:
        raise Stop(f"{name} failed")


def active_count() -> int:
    path = DATA / "reviews.json"
    return sum(1 for r in json.loads(path.read_text(encoding="utf-8")) if not r.get("deleted_at")) if path.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()

    backup = {p: p.read_bytes() if p.exists() else None for p in TOUCHED}
    try:
        if not args.skip_ingest:
            before = active_count()
            run("Pull reviews", "backend/ingest.py")
            gone = before - active_count()
            if gone > MAX_DELETIONS:
                raise Stop(f"{gone} reviews disappeared in one pull; that looks like a broken fetch, not real deletions")
        run("Label new reviews", "backend/label_new.py")
        run("Build dashboard", "backend/build.py")
        run("Tests", "-m", "unittest", "discover", "-s", "backend/tests")
        if not args.no_email:
            log = DATA / "alerts_log.json"
            sent_before = log.read_bytes() if log.exists() else None
            run("Alert email", "backend/alerts.py")
            if (log.read_bytes() if log.exists() else None) != sent_before:
                run("Rebuild with alert status", "backend/build.py")
    except Stop as e:
        for p, data in backup.items():
            if data is None:
                p.unlink(missing_ok=True)
            else:
                p.write_bytes(data)
        print(f"\nRefresh stopped: {e}. All data files were put back as they were; nothing changed.")
        return 1
    print("\nRefresh complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
