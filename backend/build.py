"""
Build the dashboard data from the master store and labels. No AI here: every number is
computed from data/reviews.json + data/labels.json, so nothing can be invented.

    python backend/build.py                 # as of today (UTC)
    python backend/build.py --as-of 2026-09-28

Writes web/data/dashboard.json and data/alerts_pending.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WEB_DATA = ROOT / "web" / "data"
TAX = json.loads((Path(__file__).parent / "taxonomy.json").read_text(encoding="utf-8"))
THEMES, RULES = TAX["themes"], TAX["rules"]
PLAY = "Google Play"


def d(s: str) -> date:
    return date.fromisoformat(s[:10])


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


# --------------------------------------------------------------------------- inputs

def prepare(as_of: date):
    raw = [r for r in load("reviews.json", []) if not r.get("deleted_at")]
    labels = load("labels.json", {})
    per_day = Counter(r["date_posted"] for r in raw)
    spike_days = {day for day, n in per_day.items() if n >= RULES["spike_day_min_reviews"]}
    avg_per_day = len(raw) / max(1, len(per_day))
    reviews = []
    for r in raw:
        lab = labels.get(r["review_id"])
        themes = sorted({m["theme"] for m in lab["mentions"]}) if lab else []
        reply = "Unknown" if r["platform"] != PLAY else ("Replied" if r["has_developer_reply"] else "No reply")
        reply_hours = None
        if r.get("developer_reply_date") and r.get("posted_at_utc"):
            reply_hours = (datetime.fromisoformat(r["developer_reply_date"]) - datetime.fromisoformat(r["posted_at_utc"])).total_seconds() / 3600
        reviews.append({
            "id": r["review_id"], "who": r["reviewer_name"], "stars": r["rating"], "date": r["date_posted"],
            "store": r["platform"], "version": r.get("app_version"), "title": r.get("review_title"),
            "text": r.get("review_text") or "", "helpful": r.get("helpful_count") or 0,
            "reply": reply, "reply_text": r.get("developer_reply_text"), "reply_hours": reply_hours,
            "labelled": lab is not None, "labelled_by": lab["labelled_by"] if lab else None,
            "mentions": lab["mentions"] if lab else [], "themes": themes,
            "sentiment": lab["sentiment"] if lab else None, "low_context": bool(lab and lab["low_context"]),
            "mismatch": bool(lab and lab["rating_mismatch"]), "critical": lab["critical"] if lab else None,
            "segments": lab["segments"] if lab else [], "competitors": lab["competitors"] if lab else [],
            "spike": r["date_posted"] in spike_days,
        })
    reviews.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    spikes = []
    for day in sorted(spike_days, reverse=True):
        rows = [x for x in reviews if x["date"] == day]
        texts = Counter(" ".join(x["text"].lower().split())[:80] for x in rows if len(x["text"]) > 40)
        spikes.append({"day": day, "n": len(rows), "times_normal": round(len(rows) / avg_per_day, 1),
                       "stars": dict(Counter(x["stars"] for x in rows)), "near_identical": sum(c for c in texts.values() if c > 1)})
    return reviews, spikes, avg_per_day


# --------------------------------------------------------------------------- aggregates

def avg(rows):
    return round(sum(r["stars"] for r in rows) / len(rows), 2) if rows else None


def pick_quotes(rows, theme, k=3):
    """Most useful evidence: prefer helpful-voted, then recent, one per reviewer."""
    scored = []
    for r in rows:
        ev = next((m["evidence"] for m in r["mentions"] if m["theme"] == theme), None)
        if ev:
            scored.append((r["helpful"], r["date"], r, ev))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out, seen = [], set()
    for _, _, r, ev in scored:
        if r["who"] in seen:
            continue
        seen.add(r["who"])
        out.append({"review_id": r["id"], "evidence": ev})
        if len(out) == k:
            break
    return out


def theme_stats(rows, as_of: date, update_start: date):
    t90, t180 = as_of - timedelta(days=RULES["trend_window_days"]), as_of - timedelta(days=2 * RULES["trend_window_days"])
    by_theme = defaultdict(list)
    for r in rows:
        for t in r["themes"]:
            by_theme[t].append(r)
    out = []
    for tid, rs in by_theme.items():
        meta = THEMES[tid]
        dates = sorted(r["date"] for r in rs)
        months = {x[:7] for x in dates}
        unhappy = sum(1 for r in rs if r["stars"] <= 3)
        phr = Counter(m["evidence"] for r in rs for m in r["mentions"] if m["theme"] == tid)
        item = {
            "id": tid, "kind": meta["kind"], "name": meta["name"], "area": meta["area"],
            "type": meta.get("type"), "sub": meta.get("sub"), "severity": meta.get("severity"),
            "also_request": meta.get("also_request", False), "rtype": meta.get("rtype"), "reason": meta.get("reason"),
            "definition": meta.get("definition"),
            "n": len(rs), "cur90": sum(1 for r in rs if d(r["date"]) > t90),
            "prev90": sum(1 for r in rs if t180 < d(r["date"]) <= t90),
            "first": dates[0], "last": dates[-1], "months": len(months),
            "recurring": len(months) >= RULES["recurring_min_months"], "new": d(dates[0]) >= update_start,
            "avg_rating": avg(rs), "unhappy_pct": round(100 * unhappy / len(rs)),
            "quotes": pick_quotes(rs, tid), "phrasings": [p for p, _ in phr.most_common(4)],
            "review_ids": [r["id"] for r in rs],
        }
        if meta["kind"] == "request" or (meta["kind"] == "problem" and meta.get("also_request")):
            if len(rs) < RULES["priority_min_reviews"]:
                item["priority"] = "Too few to tell"
            else:
                item["priority"] = "Deal-breaker" if item["unhappy_pct"] >= RULES["dealbreaker_unhappy_pct"] else "Nice-to-have"
        out.append(item)
    out.sort(key=lambda x: (-x["n"], x["name"]))
    return out


def kind_counts(rows):
    c = Counter()
    for r in rows:
        for k in {THEMES[t]["kind"] for t in r["themes"]}:
            c[k] += 1
    return c


def breakdowns(rows):
    def group(key_fn, label_fn=lambda k: k, order=None):
        g = defaultdict(list)
        for r in rows:
            for k in key_fn(r):
                g[k].append(r)
        res = []
        for k, rs in g.items():
            kc = Counter()
            for r in rs:
                for t in r["themes"]:
                    if key_fn is area_keys and THEMES[t]["area"] != k:
                        continue
                    kc[THEMES[t]["kind"]] += 0  # ensure key
                kinds_here = {THEMES[t]["kind"] for t in r["themes"] if key_fn is not area_keys or THEMES[t]["area"] == k}
                for kk in kinds_here:
                    kc[kk] += 1
            res.append({"key": k, "label": label_fn(k), "reviews": len(rs), "problems": kc["problem"],
                        "requests": kc["request"], "praise": kc["praise"], "avg_rating": avg(rs)})
        return sorted(res, key=order or (lambda x: (-x["problems"], -x["reviews"])))

    def area_keys(r):
        return sorted({THEMES[t]["area"] for t in r["themes"]})

    def version_key(v):
        return tuple(int(p) for p in v.split(".")) if v and v[0].isdigit() else (0,)

    areas = group(area_keys, lambda k: TAX["areas"][k])
    versions = group(lambda r: [r["version"] or "Unknown"], lambda k: k if k == "Unknown" else "v" + k,
                     order=lambda x: tuple(-p for p in version_key(x["key"])))
    segments = group(lambda r: r["segments"], lambda k: TAX["segments"][k])
    return {"area": areas, "version": versions, "segment": segments,
            "totals": {"reviews": len(rows), "avg_rating": avg(rows),
                       "mixed_or_negative_pct": round(100 * sum(1 for r in rows if r["sentiment"] in ("mixed", "negative")) / len(rows)) if rows else 0}}


def version_details(rows):
    first_seen, first_themes = {}, defaultdict(list)
    theme_first = {}
    for r in sorted(rows, key=lambda x: x["date"]):
        v = r["version"]
        if v and v not in first_seen:
            first_seen[v] = r["date"]
        for t in r["themes"]:
            if t not in theme_first and v:
                theme_first[t] = v
                if THEMES[t]["kind"] != "praise":
                    first_themes[v].append(t)
    return {v: {"first_seen": first_seen[v], "first_themes": first_themes.get(v, [])} for v in first_seen}


def segments_detail(rows, themes_by_id):
    out = []
    for seg, label in TAX["segments"].items():
        rs = [r for r in rows if seg in r["segments"]]
        if not rs:
            continue
        pc, rc = Counter(), Counter()
        for r in rs:
            for t in r["themes"]:
                if THEMES[t]["kind"] == "problem": pc[t] += 1
                elif THEMES[t]["kind"] == "request": rc[t] += 1
        out.append({"key": seg, "label": label, "reviews": len(rs), "avg_rating": avg(rs),
                    "top_problems": [THEMES[t]["name"] for t, _ in pc.most_common(2)],
                    "top_requests": [THEMES[t]["name"] for t, _ in rc.most_common(2)]})
    return sorted(out, key=lambda x: -x["reviews"])


def competitors(rows):
    g = defaultdict(list)
    for r in rows:
        for c in r["competitors"]:
            g[c["name"]].append((r, c))
    return sorted([{"name": n, "mentions": len(v), "contexts": dict(Counter(c["context"] for _, c in v)),
                    "quotes": [{"review_id": r["id"], "evidence": c["evidence"], "context": c["context"]} for r, c in v[:4]]}
                   for n, v in g.items()], key=lambda x: -x["mentions"])


def critical(rows, as_of: date, alert_log):
    out = []
    for r in rows:
        if not r["critical"]:
            continue
        waiting = (as_of - d(r["date"])).days if r["reply"] == "No reply" else None
        if r["store"] != PLAY:
            alert = "Not alerted: App Store replies aren't public"
        elif r["reply"] == "Replied":
            alert = "No alert needed"
        else:
            sent = [a for a in alert_log if a["review_id"] == r["id"]]
            alert = f"Alerted {sent[-1]['sent_at'][:10]}" if sent else "Queued for next alert"
        out.append({"review_id": r["id"], "category": r["critical"]["category"],
                    "category_label": TAX["critical_categories"][r["critical"]["category"]],
                    "evidence": r["critical"]["evidence"], "store": r["store"], "date": r["date"],
                    "reply": r["reply"], "reply_hours": r["reply_hours"], "waiting_days": waiting, "alert": alert})
    return sorted(out, key=lambda x: (-(x["waiting_days"] or -1), x["date"]), reverse=False)


def reply_stats(rows):
    play = [r for r in rows if r["store"] == PLAY]
    crit = [r for r in play if r["critical"]]
    hrs = [r["reply_hours"] for r in play if r["reply"] == "Replied" and r["reply_hours"] is not None and r["reply_hours"] >= 0]
    rate = lambda rs: round(100 * sum(1 for r in rs if r["reply"] == "Replied") / len(rs)) if rs else None
    return {"play_reviews": len(play), "reply_rate": rate(play), "critical_reply_rate": rate(crit),
            "median_reply_days": round(statistics.median(hrs) / 24, 1) if hrs else None}


def monthly(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["date"][:7]].append(r)
    out = []
    for m in sorted(g):
        rs = g[m]
        kc = kind_counts(rs)
        s = Counter(r["sentiment"] for r in rs)
        out.append({"month": m, "reviews": len(rs), "avg_rating": avg(rs), "positive": s["positive"], "mixed": s["mixed"],
                    "negative": s["negative"], "problem": kc["problem"], "request": kc["request"], "praise": kc["praise"],
                    "critical": sum(1 for r in rs if r["critical"]), "spike_reviews": sum(1 for r in rs if r["spike"])})
    return out


def fmt_day(x: date) -> str:
    return f"{x.day} {x.strftime('%b')} {x.year}"


def updates(rows, as_of: date, n_windows=6):
    """Fortnightly updates, newest first. Summary text is filled from computed facts only."""
    w = RULES["update_window_days"]
    out = []
    theme_first = {}
    for r in sorted(rows, key=lambda x: x["date"]):
        for t in r["themes"]:
            theme_first.setdefault(t, d(r["date"]))
    ver_first = {}
    for r in sorted(rows, key=lambda x: x["date"]):
        if r["version"]:
            ver_first.setdefault(r["version"], d(r["date"]))
    for k in range(n_windows):
        end = as_of - timedelta(days=w * k)            # exclusive
        start, pstart = end - timedelta(days=w), end - timedelta(days=2 * w)
        cur = [r for r in rows if start <= d(r["date"]) < end]
        prev = [r for r in rows if pstart <= d(r["date"]) < start]
        tc, tp = Counter(t for r in cur for t in r["themes"]), Counter(t for r in prev for t in r["themes"])
        non_praise = lambda t: THEMES[t]["kind"] != "praise"
        new_themes = sorted([t for t in tc if start <= theme_first[t] < end and non_praise(t)], key=lambda t: -tc[t])
        rising = sorted([t for t in tc if non_praise(t) and tc[t] > tp[t] and t not in new_themes], key=lambda t: tp[t] - tc[t])[:3]
        falling = sorted([t for t in tp if non_praise(t) and tp[t] > tc[t]], key=lambda t: tc[t] - tp[t])[:3]
        new_vers = sorted([v for v, f in ver_first.items() if start <= f < end])
        crit = [r for r in cur if r["critical"]]
        lc = sum(1 for r in cur if r["low_context"])
        notable = sorted(crit, key=lambda r: r["date"], reverse=True)[:1]
        rest = sorted([r for r in cur if r not in notable and not r["low_context"]], key=lambda r: (r["helpful"], len(r["mentions"]), r["date"]), reverse=True)
        notable += rest[:3 - len(notable)]
        a, pa = avg(cur), avg(prev)
        s = []
        if len(cur) == len(prev):
            s.append(f"{len(cur)} new reviews, the same as the previous fortnight.")
        else:
            s.append(f"{len(cur)} new reviews, {'up' if len(cur) > len(prev) else 'down'} from {len(prev)} in the previous fortnight.")
        if a is not None and pa is not None:
            s.append(f"Written-review rating {a:.2f}, {'up from' if a > pa else 'down from'} {pa:.2f}." if a != pa else f"Written-review rating unchanged at {a:.2f}.")
        if crit:
            cats = Counter(TAX["critical_categories"][r["critical"]["category"]].lower() for r in crit)
            unanswered = sum(1 for r in crit if r["reply"] == "No reply")
            replied = sum(1 for r in crit if r["reply"] == "Replied")
            status = (f"{unanswered} still without a reply." if unanswered
                      else "The developer replied." if replied == len(crit)
                      else f"{replied} answered; reply status unknown for {len(crit) - replied} App Store review{'s' if len(crit) - replied != 1 else ''}.")
            s.append(f"{len(crit)} critical review{'s' if len(crit) > 1 else ''} ({', '.join(cats)}). {status}")
        else:
            s.append("No critical reviews.")
        if new_themes:
            s.append("Reported for the first time: " + ", ".join(THEMES[t]["name"] for t in new_themes[:3]) + ".")
        else:
            s.append("Nothing was reported for the first time.")
        if rising:
            s.append("Mentioned more than before: " + ", ".join(f"{THEMES[t]['name']} ({tc[t]}, was {tp[t]})" for t in rising) + ".")
        for v in new_vers:
            vr = [r for r in rows if r["version"] == v]
            s.append(f"Version {v} first appeared on {fmt_day(ver_first[v])}: {len(vr)} review{'s' if len(vr) != 1 else ''} so far, average {avg(vr):.1f}.")
        if cur:
            s.append(f"{lc} of {len(cur)} reviews say nothing specific.")
        out.append({"id": f"u{k}", "start": start.isoformat(), "end": (end - timedelta(days=1)).isoformat(),
                    "label": f"{start.day} {start.strftime('%b')} to {(end - timedelta(days=1)).day} {(end - timedelta(days=1)).strftime('%b %Y')}",
                    "reviews": len(cur), "prev_reviews": len(prev), "avg_rating": a, "prev_avg_rating": pa,
                    "critical": len(crit), "new_themes": new_themes,
                    "rising": [{"id": t, "cur": tc[t], "prev": tp[t]} for t in rising],
                    "falling": [{"id": t, "cur": tc[t], "prev": tp[t]} for t in falling],
                    "new_versions": new_vers, "notable": [r["id"] for r in notable], "summary": s})
    return out


def view(rows, as_of):
    update_start = as_of - timedelta(days=RULES["update_window_days"])
    t90 = as_of - timedelta(days=RULES["trend_window_days"])
    themes = theme_stats(rows, as_of, update_start)
    s = Counter(r["sentiment"] for r in rows)
    return {
        "totals": {"reviews": len(rows), "avg_rating": avg(rows), "stars": {str(k): sum(1 for r in rows if r["stars"] == k) for k in range(1, 6)},
                   "sentiment": dict(s), "low_context": sum(1 for r in rows if r["low_context"]),
                   "stores": dict(Counter(r["store"] for r in rows)),
                   "first_date": min((r["date"] for r in rows), default=None), "last_date": max((r["date"] for r in rows), default=None)},
        "monthly": monthly(rows),
        "themes": themes,
        "breakdowns": {"all": breakdowns(rows), "last90": breakdowns([r for r in rows if d(r["date"]) > t90])},
        "versions": version_details(rows),
        "segments": segments_detail(rows, {t["id"]: t for t in themes}),
        "competitors": competitors(rows),
        "reply": reply_stats(rows),
        "updates": updates(rows, as_of),
    }


def alerts_pending(rows, as_of, alert_log):
    pending = []
    for r in rows:
        if r["critical"] and r["store"] == PLAY and r["reply"] == "No reply":
            prior = [a for a in alert_log if a["review_id"] == r["id"]]
            pending.append({"review_id": r["id"], "reviewer": r["who"], "stars": r["stars"], "date": r["date"],
                            "category": TAX["critical_categories"][r["critical"]["category"]], "evidence": r["critical"]["evidence"],
                            "text": r["text"], "waiting_days": (as_of - d(r["date"])).days, "reminder": bool(prior)})
    return {"generated_for": as_of.isoformat(), "count": len(pending),
            "subject": f"SuperKalam reviews: {len(pending)} critical review{'s' if len(pending) != 1 else ''} without a reply",
            "reviews": pending}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="Build date (YYYY-MM-DD). Defaults to today, UTC.")
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    reviews, spikes, avg_per_day = prepare(as_of)
    alert_log = load("alerts_log.json", [])
    ingest = load("ingest_meta.json", {})
    unlabelled = sum(1 for r in reviews if not r["labelled"])
    by = Counter(r["labelled_by"] for r in reviews if r["labelled"])
    dashboard = {
        "meta": {"app": "SuperKalam", "as_of": as_of.isoformat(), "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "data_fetched_at": ingest.get("last_run_at"), "unlabelled": unlabelled, "labelled_by": dict(by),
                 "avg_reviews_per_day": round(avg_per_day, 1), "rules": RULES},
        "taxonomy": {k: TAX[k] for k in ("areas", "critical_categories", "segments", "competitor_contexts", "praise_reasons")},
        "reviews": [{k: v for k, v in r.items() if k not in ("reply_hours",)} for r in reviews],
        "spike_days": spikes,
        "critical": critical(reviews, as_of, alert_log),
        "views": {"all": view(reviews, as_of), "no_spikes": view([r for r in reviews if not r["spike"]], as_of)},
    }
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / "dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pend = alerts_pending(reviews, as_of, alert_log)
    (DATA / "alerts_pending.json").write_text(json.dumps(pend, ensure_ascii=False, indent=1), encoding="utf-8")
    v = dashboard["views"]["all"]
    print(f"Built dashboard as of {as_of}: {v['totals']['reviews']} reviews, {len(v['themes'])} themes, "
          f"{len(dashboard['critical'])} critical, {len(spikes)} spike days, {unlabelled} unlabelled, {pend['count']} alert(s) pending")


if __name__ == "__main__":
    main()
