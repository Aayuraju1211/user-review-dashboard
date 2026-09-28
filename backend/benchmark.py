"""
Quality gate: run the LLM on a stratified sample of hand-labelled reviews (writes no labels) and compare.

  python backend/benchmark.py            ~60 reviews -> data/labelling/benchmark.json
  python backend/benchmark.py --n 20     smaller sample

Gate: theme F1 >= 0.75 and critical recall = 100%. If it fails, improve the prompt or examples;
don't lower the gate.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import label_new
import llm
import prompt
from labelling import TAXONOMY

ROOT = Path(__file__).resolve().parent.parent
GATE_F1 = 0.75
GATE_CRITICAL_RECALL = 1.0


def sample(reviews: dict, labels: dict, n: int, seed: int = 11) -> list[str]:
    rng = random.Random(seed)
    pool = [rid for rid, lab in labels.items()
            if lab["labelled_by"] == "hand-v1" and rid in reviews and rid not in prompt.EXAMPLE_IDS]
    kinds = lambda rid: {TAXONOMY["themes"][m["theme"]]["kind"] for m in labels[rid]["mentions"]}
    picked: list[str] = []

    def take(cands, k):
        cands = sorted(c for c in cands if c not in picked)
        picked.extend(rng.sample(cands, min(k, len(cands))))

    take([r for r in pool if labels[r]["critical"]], 99)                                   # every critical
    take([r for r in pool if reviews[r]["rating"] <= 3 and not labels[r]["low_context"]], round(n * .2))
    take([r for r in pool if len(labels[r]["mentions"]) >= 2], round(n * .2))
    take([r for r in pool if labels[r]["low_context"]], round(n * .15))
    take([r for r in pool if labels[r]["competitors"] or labels[r]["segments"]], round(n * .1))
    take([r for r in pool if "request" in kinds(r)], round(n * .1))
    take(pool, n - len(picked))
    return picked


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()
    config.require_llm()

    reviews = {r["review_id"]: r for r in json.loads((ROOT / "data" / "reviews.json").read_text(encoding="utf-8"))}
    labels = json.loads((ROOT / "data" / "labels.json").read_text(encoding="utf-8"))
    ids = sample(reviews, labels, args.n)
    print(f"benchmark: {len(ids)} hand-labelled reviews, models {llm.models()}, prompt {prompt.PROMPT_VERSION}")

    got, rejected = label_new.label_reviews([reviews[i] for i in ids])

    tp = fp = fn = 0
    crit_tp = crit_fn = crit_fp = crit_cat = 0
    lc_agree = 0
    rows = []
    for rid in ids:
        hand, llm_lab = labels[rid], got.get(rid)
        h = {m["theme"] for m in hand["mentions"]}
        g = {m["theme"] for m in llm_lab["mentions"]} if llm_lab else set()
        tp += len(h & g); fp += len(g - h); fn += len(h - g)
        hc, gc = hand["critical"], llm_lab and llm_lab["critical"]
        if hc:
            crit_tp += bool(gc); crit_fn += not gc
            crit_cat += bool(gc and gc["category"] == hc["category"])
        elif gc:
            crit_fp += 1
        lc_agree += bool(llm_lab) and llm_lab["low_context"] == hand["low_context"]
        if h != g or bool(hc) != bool(gc) or not llm_lab:
            rows.append({"review_id": rid, "stars": reviews[rid]["rating"],
                         "text": ((reviews[rid].get("review_title") or "") + " " + reviews[rid]["review_text"]).strip()[:300],
                         "hand": sorted(h) or (["low_context"] if hand["low_context"] else []),
                         "llm": (sorted(g) or (["low_context"] if llm_lab["low_context"] else [])) if llm_lab else ["unlabelled"],
                         "hand_critical": hc and hc["category"], "llm_critical": gc and gc["category"],
                         "problems": rejected.get(rid, [])})

    themes = prf(tp, fp, fn)
    crit_recall = crit_tp / (crit_tp + crit_fn) if crit_tp + crit_fn else 1.0
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": prompt.PROMPT_VERSION,
        "models_used": sorted({lab["labelled_by"] for lab in got.values()}),
        "sample_size": len(ids),
        "labelled": len(got),
        "left_unlabelled": len(ids) - len(got),
        "themes": themes,
        "critical": {"hand": crit_tp + crit_fn, "found": crit_tp, "recall": round(crit_recall, 3),
                     "same_category": crit_cat, "false_alarms": crit_fp},
        "low_context_agreement": round(lc_agree / len(ids), 3),
        "gate": {"theme_f1_min": GATE_F1, "critical_recall_min": GATE_CRITICAL_RECALL,
                 "passed": themes["f1"] >= GATE_F1 and crit_recall >= GATE_CRITICAL_RECALL},
        "disagreements": rows,
    }
    out = ROOT / "data" / "labelling" / "benchmark.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"themes    precision {themes['precision']:.2f}  recall {themes['recall']:.2f}  F1 {themes['f1']:.2f}  (gate {GATE_F1})")
    print(f"critical  found {crit_tp}/{crit_tp + crit_fn}, same category {crit_cat}, false alarms {crit_fp}")
    print(f"low-context agreement {report['low_context_agreement']:.0%}; unlabelled {report['left_unlabelled']}")
    print(f"gate {'PASSED' if report['gate']['passed'] else 'FAILED'}; {len(rows)} disagreements -> {out}")
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
