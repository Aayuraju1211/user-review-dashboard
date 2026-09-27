"""
Review ingestion layer for the review intelligence pipeline.

Pulls every publicly available review for an app from:
  - Google Play (via google-play-scraper, paginated to exhaustion, newest first)
  - Apple App Store (via the public customer-reviews RSS JSON feed, several storefronts)

and upserts them into one master store (data/reviews.json + reviews.csv), logging
additions, edits, rating changes, new developer replies and deletions to data/changes.json.

Full pull (default, recommended: the whole history takes ~10 seconds and catches edits):
    python backend/ingest.py

Seed the master store from an earlier raw pull, without fetching:
    python backend/ingest.py --seed-from output/<file>.json

Incremental pull (stop once we hit reviews we already have; no deletion detection):
    python backend/ingest.py --since-date 2026-09-01

Adding a new source later = write a fetch_<source>() that returns List[dict]
in the UNIFIED_FIELDS shape and register it in SOURCES.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests
from google_play_scraper import Sort
from google_play_scraper.constants.element import ElementSpecs
from google_play_scraper.constants.request import Formats
# Private helper: the public reviews() wrapper swallows request errors and returns
# token=None, which is indistinguishable from "no more pages". Calling the page
# fetcher directly lets us retry failures instead of silently truncating the pull.
# Pinned to google-play-scraper==1.2.7 in requirements.txt for this reason.
from google_play_scraper.features.reviews import _fetch_review_items


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

APP_NAME = "superkalam"
PLAY_PACKAGE_ID = "com.superkalam"
APPSTORE_APP_ID = "6747128599"  # SuperKalam: Crack UPSC IAS (bundle com.superkalam)

# Play reviews are partitioned by language, not reviewer country, so we pull each
# language the app's audience plausibly writes in and dedupe by review id.
PLAY_LANGUAGES = ["en", "hi"]
PLAY_COUNTRY = "in"
PLAY_PAGE_SIZE = 200

# Apple caps each storefront at 10 pages x 50 reviews. Storefronts with no
# reviews just return an empty feed, so casting a wider net is cheap.
APPSTORE_COUNTRIES = ["in", "us", "gb", "ae", "ca", "au", "sg", "sa", "qa", "kw", "om", "bh", "np", "nz", "de"]
APPSTORE_MAX_PAGES = 10

REQUEST_DELAY_S = 0.5  # politeness delay between successful requests
MAX_RETRIES = 5
BACKOFF_BASE_S = 2.0

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"      # master store: reviews.json, changes.json
RAW_DIR = ROOT_DIR / "output"     # optional raw snapshots of individual pulls

UNIFIED_FIELDS = [
    "review_id",
    "platform",
    "country",               # storefront the review came from (App Store); null for Play
    "language",              # language feed the review was fetched from (Play); null for App Store
    "reviewer_name",
    "rating",
    "review_title",
    "review_text",
    "date_posted",           # YYYY-MM-DD, UTC
    "time_posted",           # HH:MM:SS, UTC
    "posted_at_utc",         # full ISO-8601 timestamp, UTC (use this for sorting/diffing)
    "app_version",
    "helpful_count",
    "has_developer_reply",
    "developer_reply_text",
    "developer_reply_date",  # ISO-8601, UTC
    "fetched_at_utc",
]

PLATFORM_PLAY = "Google Play"
PLATFORM_APPSTORE = "App Store"


# ---------------------------------------------------------------------------
# Incremental-pull support
# ---------------------------------------------------------------------------

@dataclass
class SinceFilter:
    """Stop condition for incremental pulls. Both sources are read newest-first,
    so we stop paginating at the first review that is older than `date` or
    matches `review_id` (a review we already have)."""
    date: Optional[datetime] = None
    review_id: Optional[str] = None

    def reached(self, review_id: str, posted_at: Optional[datetime]) -> bool:
        if self.review_id and review_id == self.review_id:
            return True
        if self.date and posted_at and posted_at < self.date:
            return True
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class RetryableError(Exception):
    pass


def with_retry(fn: Callable, *args, label: str = "", **kwargs):
    """Call fn with exponential backoff + jitter. Retries on any exception."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - network libs raise a wide variety
            if attempt == MAX_RETRIES:
                raise
            delay = BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 1)
            print(f"  [retry] {label} attempt {attempt}/{MAX_RETRIES} failed ({type(e).__name__}: {e}); "
                  f"sleeping {delay:.1f}s")
            time.sleep(delay)


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    # Naive datetimes (google-play-scraper uses datetime.fromtimestamp) are local time.
    return dt.astimezone(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def empty_record() -> Dict:
    return {f: None for f in UNIFIED_FIELDS}


def none_if_blank(v):
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


# ---------------------------------------------------------------------------
# Google Play
# ---------------------------------------------------------------------------

def _fetch_play_page(lang: str, country: str, token: Optional[str]):
    url = Formats.Reviews.build(lang=lang, country=country)
    try:
        return _fetch_review_items(
            url, PLAY_PACKAGE_ID, Sort.NEWEST.value, PLAY_PAGE_SIZE, None, None, token
        )
    except TypeError:
        # Play returns a null review list when a language feed has no reviews at all;
        # the library chokes parsing it. That's a valid empty result, not a failure.
        return [], None


def fetch_play_store(since: Optional[SinceFilter] = None) -> List[Dict]:
    since = since or SinceFilter()
    fetched_at = iso(datetime.now(timezone.utc))
    by_id: Dict[str, Dict] = {}

    for lang in PLAY_LANGUAGES:
        token = None
        page = 0
        lang_count = 0
        stop = False
        while not stop:
            page += 1
            items, next_token = with_retry(
                _fetch_play_page, lang, PLAY_COUNTRY, token, label=f"play lang={lang} page={page}"
            )
            for raw in items:
                r = {k: spec.extract_content(raw) for k, spec in ElementSpecs.Review.items()}
                posted = to_utc(r.get("at"))
                if since.reached(r["reviewId"], posted):
                    stop = True
                    break
                lang_count += 1
                if r["reviewId"] in by_id:
                    continue
                replied = to_utc(r.get("repliedAt"))
                reply_text = none_if_blank(r.get("replyContent"))
                rec = empty_record()
                rec.update(
                    review_id=r["reviewId"],
                    platform=PLATFORM_PLAY,
                    country=None,
                    language=lang,
                    reviewer_name=r.get("userName"),
                    rating=r.get("score"),
                    review_title=None,
                    review_text=r.get("content"),
                    date_posted=posted.date().isoformat() if posted else None,
                    time_posted=posted.time().isoformat(timespec="seconds") if posted else None,
                    posted_at_utc=iso(posted),
                    app_version=r.get("reviewCreatedVersion"),
                    helpful_count=r.get("thumbsUpCount"),
                    has_developer_reply=reply_text is not None,
                    developer_reply_text=reply_text,
                    developer_reply_date=iso(replied),
                    fetched_at_utc=fetched_at,
                )
                by_id[r["reviewId"]] = rec

            print(f"  play lang={lang} page={page}: +{len(items)} (lang total {lang_count}, unique {len(by_id)})")
            # The library signals the last page with None or a non-string token.
            if not items or not isinstance(next_token, str):
                break
            token = next_token
            time.sleep(REQUEST_DELAY_S)

    return list(by_id.values())


# ---------------------------------------------------------------------------
# Apple App Store
# ---------------------------------------------------------------------------

_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (review-intelligence ingestion)"


def _fetch_appstore_page(country: str, page: int) -> Optional[List[Dict]]:
    """Returns the page's review entries, or None if the page is past the feed's end."""
    url = (f"https://itunes.apple.com/{country}/rss/customerreviews/"
           f"page={page}/id={APPSTORE_APP_ID}/sortby=mostrecent/json")
    resp = _session.get(url, timeout=30)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RetryableError(f"HTTP {resp.status_code}")
    if resp.status_code == 400:
        return None  # Apple returns 400 beyond page 10
    resp.raise_for_status()
    entries = resp.json().get("feed", {}).get("entry", [])
    if isinstance(entries, dict):  # single-entry feeds come back as an object
        entries = [entries]
    # Guard against the legacy format where entry[0] is app metadata, not a review.
    return [e for e in entries if "im:rating" in e]


def _label(entry: Dict, key: str) -> Optional[str]:
    v = entry.get(key)
    return v.get("label") if isinstance(v, dict) else None


def fetch_app_store(since: Optional[SinceFilter] = None) -> List[Dict]:
    since = since or SinceFilter()
    fetched_at = iso(datetime.now(timezone.utc))
    by_id: Dict[str, Dict] = {}

    for country in APPSTORE_COUNTRIES:
        country_count = 0
        for page in range(1, APPSTORE_MAX_PAGES + 1):
            entries = with_retry(_fetch_appstore_page, country, page, label=f"appstore {country} page={page}")
            if not entries:
                break
            stop = False
            for e in entries:
                rid = _label(e, "id")
                # "updated" is the review's last-modified time; the feed has no separate created time.
                posted = to_utc(datetime.fromisoformat(_label(e, "updated"))) if _label(e, "updated") else None
                if since.reached(rid, posted):
                    stop = True
                    break
                country_count += 1
                if rid in by_id:
                    continue
                rating = _label(e, "im:rating")
                votes = _label(e, "im:voteSum")
                rec = empty_record()
                rec.update(
                    review_id=rid,
                    platform=PLATFORM_APPSTORE,
                    country=country,
                    language=None,
                    reviewer_name=_label(e.get("author", {}), "name"),
                    rating=int(rating) if rating else None,
                    review_title=_label(e, "title"),
                    review_text=_label(e, "content"),
                    date_posted=posted.date().isoformat() if posted else None,
                    time_posted=posted.time().isoformat(timespec="seconds") if posted else None,
                    posted_at_utc=iso(posted),
                    app_version=_label(e, "im:version"),
                    helpful_count=int(votes) if votes is not None else None,
                    # The public RSS feed does not expose developer responses at all, so
                    # these are unknown (null), not "no reply".
                    has_developer_reply=None,
                    developer_reply_text=None,
                    developer_reply_date=None,
                    fetched_at_utc=fetched_at,
                )
                by_id[rid] = rec
            print(f"  appstore {country} page={page}: +{len(entries)} (storefront total {country_count}, unique {len(by_id)})")
            if stop:
                break
            time.sleep(REQUEST_DELAY_S)

    return list(by_id.values())


# ---------------------------------------------------------------------------
# Orchestration / output
# ---------------------------------------------------------------------------

SOURCES: Dict[str, Callable[[Optional[SinceFilter]], List[Dict]]] = {
    PLATFORM_PLAY: fetch_play_store,
    PLATFORM_APPSTORE: fetch_app_store,
}


MASTER_FIELDS = UNIFIED_FIELDS + ["first_seen_at", "last_seen_at", "deleted_at"]
# Fields whose change after first sight is worth logging (edits, rating changes, new replies).
TRACKED_FIELDS = ["rating", "review_title", "review_text", "app_version", "has_developer_reply",
                  "developer_reply_text", "developer_reply_date"]
APPSTORE_FEED_CAP = 500  # Apple serves at most this many reviews per storefront


def load_json(path: Path, default):
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)  # atomic swap so a crash never leaves a half-written master


def change_type(field: str, old, new) -> str:
    if field == "has_developer_reply" and not old and new:
        return "reply_added"
    if field in ("developer_reply_text", "developer_reply_date"):
        return "reply_changed" if old else "reply_added"
    if field == "rating":
        return "rating_changed"
    return "edited"


def merge_into_master(master: Dict[str, Dict], fetched: List[Dict], run_at: str,
                      full_pull: bool) -> List[Dict]:
    """Upsert fetched reviews into the master store (keyed by review id) and return change events.

    Deletions are only inferred on a full pull, and for the App Store only where the
    storefront's feed was not capped (a capped feed hides old reviews without deleting them).
    """
    events: List[Dict] = []
    seen = set()
    for rec in fetched:
        rid = rec["review_id"]
        seen.add(rid)
        prev = master.get(rid)
        if prev is None:
            master[rid] = {**rec, "first_seen_at": run_at, "last_seen_at": run_at, "deleted_at": None}
            events.append({"review_id": rid, "platform": rec["platform"], "type": "added", "detected_at": run_at})
            continue
        for field in TRACKED_FIELDS:
            if prev.get(field) != rec.get(field):
                # has_developer_reply None -> None on App Store is not a change; skip null-to-null noise
                if prev.get(field) in (None, "") and rec.get(field) in (None, ""):
                    continue
                events.append({"review_id": rid, "platform": rec["platform"], "type": change_type(field, prev.get(field), rec.get(field)),
                               "field": field, "old": prev.get(field), "new": rec.get(field), "detected_at": run_at})
        if prev.get("deleted_at"):
            events.append({"review_id": rid, "platform": rec["platform"], "type": "restored", "detected_at": run_at})
        master[rid] = {**prev, **rec, "first_seen_at": prev.get("first_seen_at") or run_at, "last_seen_at": run_at, "deleted_at": None}

    if full_pull:
        store_counts: Dict[str, int] = {}
        for rec in fetched:
            if rec["platform"] == PLATFORM_APPSTORE:
                store_counts[rec["country"]] = store_counts.get(rec["country"], 0) + 1
        fetched_platforms = {r["platform"] for r in fetched}
        for rid, rec in master.items():
            if rid in seen or rec.get("deleted_at") or rec["platform"] not in fetched_platforms:
                continue
            if rec["platform"] == PLATFORM_APPSTORE and store_counts.get(rec["country"], 0) >= APPSTORE_FEED_CAP:
                continue
            rec["deleted_at"] = run_at
            events.append({"review_id": rid, "platform": rec["platform"], "type": "deleted", "detected_at": run_at})
    return events


def write_master(master: Dict[str, Dict], data_dir: Path) -> List[Dict]:
    records = sorted(master.values(), key=lambda r: r["posted_at_utc"] or "", reverse=True)
    save_json(data_dir / "reviews.json", records)
    # CSV export for spreadsheets; utf-8-sig so Excel renders Hindi and emoji correctly
    with (data_dir / "reviews.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    return records


def print_summary(records: List[Dict]) -> None:
    print("\n" + "=" * 60)
    print(f"SUMMARY  ({len(records)} reviews total)")
    print("=" * 60)
    for platform in SOURCES:
        rows = [r for r in records if r["platform"] == platform]
        dates = sorted(r["date_posted"] for r in rows if r["date_posted"])
        replied = sum(1 for r in rows if r["has_developer_reply"] is True)
        reply_known = any(r["has_developer_reply"] is not None for r in rows)
        print(f"\n{platform}")
        print(f"  reviews pulled   : {len(rows)}")
        print(f"  date range       : {dates[0]} -> {dates[-1]}" if dates else "  date range       : n/a")
        print(f"  developer replies: {replied}" if reply_known or not rows
              else "  developer replies: unknown (not exposed by this source)")
        if platform == PLATFORM_APPSTORE and rows:
            by_country: Dict[str, int] = {}
            for r in rows:
                by_country[r["country"]] = by_country.get(r["country"], 0) + 1
            print(f"  by storefront    : {by_country}")
        if platform == PLATFORM_PLAY and rows:
            by_lang: Dict[str, int] = {}
            for r in rows:
                by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
            print(f"  by language feed : {by_lang}")


def parse_args():
    p = argparse.ArgumentParser(description="Ingest app reviews from Google Play and the App Store.")
    p.add_argument("--since-date", help="Only keep reviews on/after this date (YYYY-MM-DD, UTC)")
    p.add_argument("--since-play-id", help="Stop Play pagination at this already-seen review id")
    p.add_argument("--since-appstore-id", help="Stop App Store pagination at this already-seen review id")
    p.add_argument("--platforms", nargs="+", choices=list(SOURCES), default=list(SOURCES))
    p.add_argument("--data-dir", type=Path, default=DATA_DIR)
    p.add_argument("--seed-from", type=Path,
                   help="Load reviews from an earlier raw pull (JSON) instead of fetching. Used to seed the master store.")
    p.add_argument("--save-raw", action="store_true", help="Also keep a timestamped copy of this pull in output/")
    return p.parse_args()


def main():
    args = parse_args()
    since_date = (datetime.fromisoformat(args.since_date).replace(tzinfo=timezone.utc)
                  if args.since_date else None)
    since_by_platform = {
        PLATFORM_PLAY: SinceFilter(date=since_date, review_id=args.since_play_id),
        PLATFORM_APPSTORE: SinceFilter(date=since_date, review_id=args.since_appstore_id),
    }
    incremental = bool(since_date or args.since_play_id or args.since_appstore_id)
    run_at = datetime.now(timezone.utc).isoformat()

    if args.seed_from:
        fetched = load_json(args.seed_from, [])
        run_at = max((r.get("fetched_at_utc") or run_at) for r in fetched) if fetched else run_at
        print(f"Seeding from {args.seed_from} ({len(fetched)} reviews)")
    else:
        fetched = []
        for platform in args.platforms:
            print(f"\nFetching {platform} ...")
            fetched.extend(SOURCES[platform](since_by_platform.get(platform)))
        if args.save_raw:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            save_json(RAW_DIR / f"{APP_NAME}_reviews_{'incremental' if incremental else 'full'}_{stamp}.json", fetched)

    master = {r["review_id"]: r for r in load_json(args.data_dir / "reviews.json", [])}
    events = merge_into_master(master, fetched, run_at, full_pull=not incremental)
    records = write_master(master, args.data_dir)
    changes = load_json(args.data_dir / "changes.json", [])
    save_json(args.data_dir / "changes.json", changes + events)
    save_json(args.data_dir / "ingest_meta.json", {"last_run_at": run_at, "fetched": len(fetched), "mode": "seed" if args.seed_from else ("incremental" if incremental else "full")})

    print_summary([r for r in records if not r.get("deleted_at")])
    by_type: Dict[str, int] = {}
    for e in events:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    print(f"\nChanges this run: {by_type or 'none'}")
    print(f"Master store: {args.data_dir / 'reviews.json'} ({len(records)} reviews)")


if __name__ == "__main__":
    main()
