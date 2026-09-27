"""Checks run before the dashboard is published.  python -m unittest discover -s backend/tests"""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

import ingest  # noqa: E402
from labelling import norm  # noqa: E402

TAX = json.loads((BACKEND / "taxonomy.json").read_text(encoding="utf-8"))
REVIEWS = [r for r in json.loads((ROOT / "data" / "reviews.json").read_text(encoding="utf-8")) if not r.get("deleted_at")]
LABELS = json.loads((ROOT / "data" / "labels.json").read_text(encoding="utf-8"))
DASH = json.loads((ROOT / "web" / "data" / "dashboard.json").read_text(encoding="utf-8"))


class Labels(unittest.TestCase):
    def test_every_review_is_labelled(self):
        missing = [r["review_id"] for r in REVIEWS if r["review_id"] not in LABELS]
        self.assertEqual(missing, [], "unlabelled reviews")

    def test_labels_use_known_values(self):
        for rid, lab in LABELS.items():
            for m in lab["mentions"]:
                self.assertIn(m["theme"], TAX["themes"], rid)
            if lab["critical"]:
                self.assertIn(lab["critical"]["category"], TAX["critical_categories"], rid)
            for s in lab["segments"]:
                self.assertIn(s, TAX["segments"], rid)
            self.assertIn(lab["sentiment"], ("positive", "mixed", "negative"), rid)
            self.assertFalse(lab["low_context"] and lab["mentions"], f"{rid}: low-context with mentions")

    def test_evidence_is_quoted_from_the_review(self):
        by_id = {r["review_id"]: r for r in REVIEWS}
        for rid, lab in LABELS.items():
            hay = norm((by_id[rid].get("review_title") or "") + " " + by_id[rid]["review_text"])
            for m in lab["mentions"]:
                self.assertIn(norm(m["evidence"]), hay, f"{rid}: {m['evidence']}")


class Dashboard(unittest.TestCase):
    def test_star_counts_add_up(self):
        for name, v in DASH["views"].items():
            self.assertEqual(sum(v["totals"]["stars"].values()), v["totals"]["reviews"], name)

    def test_spike_view_excludes_exactly_the_spike_reviews(self):
        spikes = sum(1 for r in DASH["reviews"] if r["spike"])
        self.assertEqual(DASH["views"]["all"]["totals"]["reviews"] - spikes, DASH["views"]["no_spikes"]["totals"]["reviews"])
        self.assertEqual(spikes, sum(s["n"] for s in DASH["spike_days"]))

    def test_theme_counts_match_reviews(self):
        c = Counter(t for r in DASH["reviews"] for t in r["themes"])
        for t in DASH["views"]["all"]["themes"]:
            self.assertEqual(t["n"], c[t["id"]], t["id"])
            self.assertEqual(len(t["review_ids"]), t["n"], t["id"])

    def test_quotes_point_at_real_reviews(self):
        ids = {r["id"] for r in DASH["reviews"]}
        for t in DASH["views"]["all"]["themes"]:
            for q in t["quotes"]:
                self.assertIn(q["review_id"], ids)

    def test_update_window_counts(self):
        u = DASH["views"]["all"]["updates"][0]
        n = sum(1 for r in DASH["reviews"] if u["start"] <= r["date"] <= u["end"])
        self.assertEqual(u["reviews"], n)

    def test_each_theme_counted_once_across_fix_and_build(self):
        names = [t["name"] for t in DASH["views"]["all"]["themes"]]
        self.assertEqual(len(names), len(set(names)))


class Ingest(unittest.TestCase):
    def rec(self, rid, **kw):
        base = {f: None for f in ingest.UNIFIED_FIELDS}
        base.update(review_id=rid, platform=ingest.PLATFORM_PLAY, rating=5, review_text="good", posted_at_utc="2026-01-01T00:00:00+00:00", has_developer_reply=False)
        base.update(kw)
        return base

    def test_upsert_logs_changes_and_deletions(self):
        master = {}
        ev = ingest.merge_into_master(master, [self.rec("a"), self.rec("b")], "t1", full_pull=True)
        self.assertEqual(Counter(e["type"] for e in ev), {"added": 2})
        ev = ingest.merge_into_master(master, [self.rec("a", rating=2, has_developer_reply=True, developer_reply_text="hi")], "t2", full_pull=True)
        types = Counter(e["type"] for e in ev)
        self.assertEqual(types["rating_changed"], 1)
        self.assertGreaterEqual(types["reply_added"], 1)
        self.assertEqual(types["deleted"], 1)
        self.assertEqual(master["b"]["deleted_at"], "t2")
        self.assertEqual(master["a"]["first_seen_at"], "t1")

    def test_rerun_is_idempotent(self):
        master = {}
        ingest.merge_into_master(master, [self.rec("a")], "t1", full_pull=True)
        self.assertEqual(ingest.merge_into_master(master, [self.rec("a")], "t2", full_pull=True), [])


if __name__ == "__main__":
    unittest.main()
