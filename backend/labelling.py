"""
Hand-labelling helpers.

  python backend/labelling.py dump      -> data/labelling/queue.txt + index.json (unlabelled reviews, numbered)
  python backend/labelling.py compile   -> data/labels.json from data/labelling/batch_*.txt

Batch line format (one review per line, '#' starts a comment):

  <idx> | theme="evidence"; theme="evidence" | FLAGS

FLAGS (space separated, all optional):
  LC                               low context: says nothing specific
  MM                               star rating contradicts the text
  S:pos|mix|neg                    override the derived sentiment
  CR:category="evidence"           critical issue (category from taxonomy.critical_categories)
  SEG:seg1,seg2                    user segments stated in the text
  COMP:Name/context="evidence"     competitor mention (context from taxonomy.competitor_contexts)

Evidence must be copied from the review text; compile fails if it isn't found there.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WORK = DATA / "labelling"
TAXONOMY = json.loads((Path(__file__).parent / "taxonomy.json").read_text(encoding="utf-8"))


def norm(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def reviews():
    return json.loads((DATA / "reviews.json").read_text(encoding="utf-8"))


def dump():
    labels = json.loads((DATA / "labels.json").read_text(encoding="utf-8")) if (DATA / "labels.json").exists() else {}
    rows = sorted((r for r in reviews() if r["review_id"] not in labels and not r.get("deleted_at")),
                  key=lambda r: (r["posted_at_utc"], r["review_id"]))
    WORK.mkdir(parents=True, exist_ok=True)
    start = 1 + max([int(k) for k in json.loads((WORK / "index.json").read_text())] or [0]) if (WORK / "index.json").exists() else 1
    index = json.loads((WORK / "index.json").read_text()) if (WORK / "index.json").exists() else {}
    known = set(index.values())
    lines = []
    i = start
    for r in rows:
        if r["review_id"] in known:
            idx = next(k for k, v in index.items() if v == r["review_id"])
        else:
            idx = str(i); index[idx] = r["review_id"]; i += 1
        text = " ".join((r["review_text"] or "").split())
        title = f"[{r['review_title']}] " if r.get("review_title") else ""
        lines.append(f"{idx}|{r['rating']}|{'P' if r['platform'] == 'Google Play' else 'A'}|{title}{text}")
    (WORK / "index.json").write_text(json.dumps(index, indent=0), encoding="utf-8")
    (WORK / "queue.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(lines)} reviews to label -> {WORK / 'queue.txt'}")


LINE = re.compile(r"^\s*(\d+)\s*\|(.*?)\|(.*)$")
PAIR = re.compile(r'([a-z_]+)\s*=\s*"([^"]*)"')


def derive_sentiment(rating: int, kinds: set) -> str:
    if "problem" in kinds:
        return "mix" if "praise" in kinds else "neg"
    if rating >= 4:
        return "mix" if ("request" in kinds and "praise" not in kinds and rating == 4) else "pos"
    return "mix" if rating == 3 else "neg"


def compile_labels():
    index = json.loads((WORK / "index.json").read_text())
    by_id = {r["review_id"]: r for r in reviews()}
    themes = TAXONOMY["themes"]
    out = json.loads((DATA / "labels.json").read_text(encoding="utf-8")) if (DATA / "labels.json").exists() else {}
    errors = []
    for batch in sorted(WORK.glob("batch_*.txt")):
        for n, raw in enumerate(batch.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0].rstrip() if not raw.strip().startswith("#") else ""
            if not line.strip():
                continue
            m = LINE.match(line)
            if not m:
                errors.append(f"{batch.name}:{n} unparseable: {raw}"); continue
            idx, ment, flags = m.group(1), m.group(2), m.group(3)
            rid = index.get(idx)
            if not rid:
                errors.append(f"{batch.name}:{n} unknown index {idx}"); continue
            rev = by_id[rid]
            hay = norm((rev.get("review_title") or "") + " " + (rev.get("review_text") or ""))
            mentions = []
            for theme, ev in PAIR.findall(ment):
                if theme not in themes:
                    errors.append(f"{batch.name}:{n} idx {idx}: unknown theme {theme}")
                elif norm(ev) not in hay:
                    errors.append(f"{batch.name}:{n} idx {idx}: evidence not in text: {ev!r}")
                else:
                    mentions.append({"theme": theme, "evidence": ev})
            label = {"mentions": mentions, "low_context": False, "rating_mismatch": False, "critical": None,
                     "segments": [], "competitors": [], "labelled_by": "hand-v1"}
            override = None
            for tok in re.findall(r'[A-Z]+:[^\s"=]+(?:="[^"]*")?|LC|MM', flags):
                if tok == "LC": label["low_context"] = True
                elif tok == "MM": label["rating_mismatch"] = True
                elif tok.startswith("S:"): override = tok[2:]
                elif tok.startswith("SEG:"):
                    segs = tok[4:].split(",")
                    bad = [s for s in segs if s not in TAXONOMY["segments"]]
                    if bad: errors.append(f"{batch.name}:{n} idx {idx}: unknown segment {bad}")
                    label["segments"] = [s for s in segs if s in TAXONOMY["segments"]]
                elif tok.startswith("CR:"):
                    cm = re.match(r'CR:([a-z_]+)="([^"]*)"', tok)
                    if not cm or cm.group(1) not in TAXONOMY["critical_categories"]:
                        errors.append(f"{batch.name}:{n} idx {idx}: bad critical flag {tok}")
                    elif norm(cm.group(2)) not in hay:
                        errors.append(f"{batch.name}:{n} idx {idx}: critical evidence not in text")
                    else:
                        label["critical"] = {"category": cm.group(1), "evidence": cm.group(2)}
                elif tok.startswith("COMP:"):
                    cm = re.match(r'COMP:([^/]+)/([a-z_]+)="([^"]*)"', tok)
                    if not cm or cm.group(2) not in TAXONOMY["competitor_contexts"]:
                        errors.append(f"{batch.name}:{n} idx {idx}: bad competitor flag {tok}")
                    else:
                        label["competitors"].append({"name": cm.group(1).replace("_", " "), "context": cm.group(2), "evidence": cm.group(3)})
            kinds = {themes[m_["theme"]]["kind"] for m_ in mentions}
            if not mentions and not label["low_context"]:
                errors.append(f"{batch.name}:{n} idx {idx}: no mentions and not marked LC")
            if mentions and label["low_context"]:
                errors.append(f"{batch.name}:{n} idx {idx}: low-context review cannot have mentions")
            label["sentiment"] = {"pos": "positive", "mix": "mixed", "neg": "negative"}[override or derive_sentiment(rev["rating"], kinds)]
            out[rid] = label
    if errors:
        print("\n".join(errors)); print(f"\n{len(errors)} error(s); labels.json not written."); sys.exit(1)
    (DATA / "labels.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"labels.json: {len(out)} reviews labelled")


def spot_check(n_low=10, n_multi=12, n_lc=8, seed=7):
    """Write a sample of labelled reviews to data/labelling/spot_check.csv for a human to verify."""
    import csv
    import random
    random.seed(seed)
    labels = json.loads((DATA / "labels.json").read_text(encoding="utf-8"))
    rows = [(r, labels[r["review_id"]]) for r in reviews() if r["review_id"] in labels]
    low = [x for x in rows if x[0]["rating"] <= 3 and not x[1]["low_context"]]
    multi = [x for x in rows if len(x[1]["mentions"]) >= 2 and x[0]["rating"] >= 4]
    lc = [x for x in rows if x[1]["low_context"]]
    pick = random.sample(low, min(n_low, len(low))) + random.sample(multi, min(n_multi, len(multi))) + random.sample(lc, min(n_lc, len(lc)))
    themes, crit = TAXONOMY["themes"], TAXONOMY["critical_categories"]
    with (WORK / "spot_check.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["#", "Stars", "Store", "Review", "Themes (evidence)", "Critical", "Says nothing specific", "Sentiment", "Correct? (yes/no)", "What should it be?"])
        for i, (r, lab) in enumerate(pick, 1):
            text = (r["review_title"] + ". " if r.get("review_title") else "") + r["review_text"]
            tags = "; ".join(f'{themes[m["theme"]]["name"]} ({m["evidence"]})' for m in lab["mentions"])
            w.writerow([i, r["rating"], r["platform"], text, tags, crit[lab["critical"]["category"]] if lab["critical"] else "",
                        "yes" if lab["low_context"] else "", lab["sentiment"], "", ""])
    print(f"{len(pick)} reviews -> {WORK / 'spot_check.csv'}")


if __name__ == "__main__":
    {"dump": dump, "compile": compile_labels, "spotcheck": spot_check}[sys.argv[1] if len(sys.argv) > 1 else "dump"]()
