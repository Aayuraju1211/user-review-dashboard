"""
Label new reviews with the LLM.

  python backend/label_new.py              label every active review that has no label yet
  python backend/label_new.py --dry-run    print proposed labels, write nothing
  python backend/label_new.py --limit 20   label at most 20 reviews this run

Every label is validated before it is accepted. Anything that fails is dropped; a review with nothing
valid left stays unlabelled (still counted in the rating, shown as waiting). Hand labels are never touched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
import llm
import prompt
from labelling import TAXONOMY, derive_sentiment, norm

ROOT = Path(__file__).resolve().parent.parent
SENTIMENTS = {"positive", "mixed", "negative"}
SENT_NAMES = {"pos": "positive", "mix": "mixed", "neg": "negative"}


def haystack(r: dict) -> str:
    return norm((r.get("review_title") or "") + " " + (r.get("review_text") or ""))


def _quoted(ev, hay: str) -> bool:
    return isinstance(ev, str) and len(norm(ev)) >= 2 and norm(ev) in hay


_known_names: dict[str, str] | None = None


def competitor_name(name: str) -> str:
    """Reuse the spelling already on the dashboard (e.g. 'chatgpt' -> 'ChatGPT') so counts don't split."""
    global _known_names
    if _known_names is None:
        path = ROOT / "data" / "labels.json"
        labels = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        _known_names = {c["name"].lower().replace(" ", ""): c["name"]
                        for lab in labels.values() if lab["labelled_by"] == "hand-v1" for c in lab["competitors"]}
    name = name.strip()
    return _known_names.get(name.lower().replace(" ", ""), name)


def validate(raw: dict, review: dict) -> tuple[dict | None, list[str]]:
    """Return (clean label or None, list of problems found). Never invents anything."""
    hay, problems = haystack(review), []
    themes = TAXONOMY["themes"]

    mentions, seen = [], set()
    for m in raw.get("mentions") or []:
        if not isinstance(m, dict):
            continue
        theme, ev = m.get("theme"), m.get("evidence")
        if theme not in themes:
            problems.append(f"unknown theme {theme!r}")
        elif not _quoted(ev, hay):
            problems.append(f"{theme}: evidence not in review {ev!r}")
        elif theme not in seen:
            seen.add(theme)
            mentions.append({"theme": theme, "evidence": ev})

    critical = raw.get("critical")
    if critical:
        if not isinstance(critical, dict) or critical.get("category") not in TAXONOMY["critical_categories"]:
            problems.append(f"bad critical {critical!r}"); critical = None
        elif not _quoted(critical.get("evidence"), hay):
            problems.append("critical evidence not in review"); critical = None
        else:
            critical = {"category": critical["category"], "evidence": critical["evidence"]}
    else:
        critical = None

    segments = []
    for s in raw.get("segments") or []:
        if s in TAXONOMY["segments"] and s not in segments:
            segments.append(s)
        elif s not in TAXONOMY["segments"]:
            problems.append(f"unknown segment {s!r}")

    competitors = []
    for c in raw.get("competitors") or []:
        if (isinstance(c, dict) and str(c.get("name") or "").strip()
                and c.get("context") in TAXONOMY["competitor_contexts"] and _quoted(c.get("evidence"), hay)):
            competitors.append({"name": competitor_name(c["name"]), "context": c["context"], "evidence": c["evidence"]})
        else:
            problems.append(f"bad competitor {c!r}")

    low_context = raw.get("low_context") is True
    if mentions:
        low_context = False
    elif low_context and critical:
        problems.append("low-context review cannot be critical")
        return None, problems
    elif not low_context:
        problems.append("no valid mentions")
        return None, problems

    sentiment = raw.get("sentiment")
    if sentiment not in SENTIMENTS:
        kinds = {themes[m["theme"]]["kind"] for m in mentions}
        sentiment = SENT_NAMES[derive_sentiment(review["rating"], kinds)]

    return {"mentions": mentions, "low_context": low_context, "rating_mismatch": raw.get("rating_mismatch") is True,
            "critical": critical, "segments": segments, "competitors": competitors, "sentiment": sentiment}, problems


def label_reviews(todo: list[dict], log=print) -> tuple[dict, dict]:
    """Label reviews in batches. Returns (accepted {id: label}, rejected {id: problems})."""
    accepted, rejected = {}, {}
    for n, batch in enumerate(llm.batches(todo), 1):
        by_id = {r["review_id"]: r for r in batch}
        try:
            raw = llm.label_batch(batch)
        except llm.LLMError as e:
            log(f"  batch {n}: {e}")
            for rid in by_id:
                rejected[rid] = ["LLM call failed"]
            continue
        answered = set()
        for item in raw:
            rid = item.get("review_id")
            if rid not in by_id or rid in answered:
                continue
            answered.add(rid)
            label, problems = validate(item, by_id[rid])
            if label:
                label["labelled_by"] = f"model:{item['_model']}@{prompt.PROMPT_VERSION}"
                accepted[rid] = label
                if problems:
                    rejected.setdefault(rid, []).extend(f"dropped: {p}" for p in problems)
            else:
                rejected[rid] = problems
        for rid in set(by_id) - answered:
            rejected[rid] = ["model returned no label"]
        log(f"  batch {n}: {sum(1 for r in by_id if r in accepted)}/{len(by_id)} accepted")
    return accepted, rejected


def save_labels(path: Path, labels: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    reviews = json.loads((args.data_dir / "reviews.json").read_text(encoding="utf-8"))
    labels_path = args.data_dir / "labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    todo = sorted((r for r in reviews if not r.get("deleted_at") and r["review_id"] not in labels),
                  key=lambda r: (r["posted_at_utc"], r["review_id"]))
    if args.limit:
        todo = todo[:args.limit]
    if not todo:
        print("label_new: no new reviews to label")
        return 0
    config.require_llm()
    print(f"label_new: {len(todo)} new review(s), models {llm.models()}")

    accepted, rejected = label_reviews(todo)

    for rid, problems in rejected.items():
        state = "labelled with fixes" if rid in accepted else "left unlabelled"
        print(f"  {rid} {state}: {'; '.join(problems)[:300]}")

    if args.dry_run:
        print(json.dumps(accepted, ensure_ascii=False, indent=1))
        print(f"dry run: {len(accepted)} would be labelled, {len(todo) - len(accepted)} left unlabelled; nothing written")
        return 0

    current = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    added = 0
    for rid, lab in accepted.items():
        if current.get(rid, {}).get("labelled_by") == "hand-v1":
            continue
        current[rid] = lab
        added += 1
    save_labels(labels_path, current)
    print(f"label_new: {added} labelled, {len(todo) - len(accepted)} left unlabelled (shown as waiting)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
