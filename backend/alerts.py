"""
Email critical Google Play reviews that have no developer reply.

  python backend/alerts.py             send (if configured) and log to data/alerts_log.json
  python backend/alerts.py --dry-run   print the email instead of sending it

Reads data/alerts_pending.json (written by build.py). Sends nothing when there are 0 reviews.
Unanswered reviews are sent again on every run until they get a reply.
"""
from __future__ import annotations

import argparse
import html
import json
import smtplib
import sys
from collections import defaultdict
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _grouped(reviews: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for r in sorted(reviews, key=lambda r: -r["waiting_days"]):
        groups[r["category"]].append(r)
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


def _link() -> str:
    return config.DASHBOARD_URL.rstrip("/") + "/#support" if config.DASHBOARD_URL else ""


def plain_body(p: dict) -> str:
    lines = [f"{p['count']} critical Google Play review(s) have no reply yet.", ""]
    for cat, rows in _grouped(p["reviews"]).items():
        lines += [f"{cat} ({len(rows)})", "-" * (len(cat) + 4)]
        for r in rows:
            again = " (sent before, still unanswered)" if r["reminder"] else ""
            lines += [f"{r['reviewer']} · {r['stars']} stars · {r['date']} · waiting {r['waiting_days']} days{again}",
                      f"Trigger: \"{r['evidence']}\"", r["text"], ""]
    if _link():
        lines.append(f"Open the Support page: {_link()}")
    lines.append("Reply on Google Play Console to stop these reminders.")
    return "\n".join(lines)


def html_body(p: dict) -> str:
    e = html.escape
    parts = [f"<p><b>{p['count']}</b> critical Google Play review(s) have no reply yet.</p>"]
    for cat, rows in _grouped(p["reviews"]).items():
        parts.append(f"<h3 style='margin:18px 0 6px'>{e(cat)} ({len(rows)})</h3>")
        for r in rows:
            text = e(r["text"])
            ev = e(r["evidence"])
            i = text.lower().find(ev.lower())
            if i >= 0:
                text = f"{text[:i]}<mark>{text[i:i + len(ev)]}</mark>{text[i + len(ev):]}"
            again = " · sent before, still unanswered" if r["reminder"] else ""
            parts.append(f"<div style='border-left:3px solid #b42318;padding:4px 10px;margin:8px 0'>"
                         f"<div style='color:#555;font-size:13px'>{e(r['reviewer'])} · {r['stars']} stars · {r['date']} · "
                         f"waiting {r['waiting_days']} days{again}</div><div>{text}</div></div>")
    if _link():
        parts.append(f"<p><a href='{e(_link())}'>Open the Support page</a></p>")
    parts.append("<p style='color:#555;font-size:13px'>Reply on Google Play Console to stop these reminders.</p>")
    return "<div style='font-family:Arial,sans-serif;font-size:14px;line-height:1.5'>" + "".join(parts) + "</div>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pending_path = DATA / "alerts_pending.json"
    if not pending_path.exists():
        print("alerts: no alerts_pending.json; run build.py first")
        return 1
    p = json.loads(pending_path.read_text(encoding="utf-8"))
    if not p["count"]:
        print("alerts: no critical reviews waiting for a reply; nothing to send")
        return 0

    if args.dry_run:
        print(f"To: {', '.join(config.ALERT_RECIPIENTS) or '(ALERT_RECIPIENTS not set)'}\nSubject: {p['subject']}\n")
        print(plain_body(p))
        return 0
    if not config.email_configured():
        print("alerts: email not configured; skipping (set ALERT_RECIPIENTS, SMTP_USER and SMTP_PASSWORD)")
        return 0

    msg = EmailMessage()
    msg["Subject"] = p["subject"]
    msg["From"] = config.ALERT_FROM
    msg["To"] = ", ".join(config.ALERT_RECIPIENTS)
    msg.set_content(plain_body(p))
    msg.add_alternative(html_body(p), subtype="html")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=60) as s:
            s.starttls()
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        print(f"alerts: sending failed ({e.__class__.__name__}); check SMTP_USER and SMTP_PASSWORD (a Gmail app password)")
        return 1

    log_path = DATA / "alerts_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log += [{"review_id": r["review_id"], "sent_at": now} for r in p["reviews"]]
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"alerts: sent {p['count']} review(s) to {len(config.ALERT_RECIPIENTS)} recipient(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
