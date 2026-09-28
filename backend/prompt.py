"""
Labelling prompt, built from taxonomy.json and hand-labelled examples so it always matches the rulebook.
Bump PROMPT_VERSION whenever the wording, rules or examples change; it is recorded in labelled_by.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY = json.loads((ROOT / "backend" / "taxonomy.json").read_text(encoding="utf-8"))

PROMPT_VERSION = "p2"

# Hand-labelled reviews used as worked examples. The benchmark leaves these out.
EXAMPLE_IDS = [
    "67bab82d-61e1-4c25-a06f-14b5d6141d9f",  # 4 stars: praise plus two problems
    "8b150331-5efe-4ce2-bdc0-d59d07403bab",  # asked-for feature that is a known gap -> problem theme
    "e2d42bfe-bf60-4a0e-a299-cef745d9636d",  # Hinglish, Hindi-medium segment
    "13007336977",                           # Hinglish, iPad segment
    "00a02cbd-a43f-4a58-a5f0-234a27ddab3a",  # low context
    "9ebba57b-91e9-427b-92dc-0c957680fbeb",  # short but specific, not low context
    "13589246069",                           # critical: wrong charge
    "7afee58c-307d-441f-aa56-d2ef467001a7",  # critical: locked out
    "14328851913",                           # critical: paid feature not delivered
    "12945370380",                           # rating mismatch
    "c29fa833-685e-41ae-be7f-a23135b1346f",  # competitors
    "b8f3367e-1dc2-4aa9-bf3f-4f12b6e2788d",  # request plus working-professional segment
]


def review_input(r: dict) -> dict:
    return {"review_id": r["review_id"], "stars": r["rating"], "title": r.get("review_title") or "",
            "text": " ".join((r.get("review_text") or "").split())}


def label_output(rid: str, lab: dict) -> dict:
    return {"review_id": rid, "mentions": lab["mentions"], "low_context": lab["low_context"],
            "rating_mismatch": lab["rating_mismatch"], "critical": lab["critical"],
            "segments": lab["segments"], "competitors": lab["competitors"], "sentiment": lab["sentiment"]}


def _themes_block() -> str:
    areas = TAXONOMY["areas"]
    out = []
    for kind, heading in (("problem", "PROBLEMS (something is wrong or missing)"),
                          ("request", "REQUESTS (new features or offerings asked for)"),
                          ("praise", "PRAISE (what users value)")):
        out.append(f"\n{heading}")
        for tid, t in TAXONOMY["themes"].items():
            if t["kind"] != kind:
                continue
            line = f"- {tid}: {t['name']} [{areas.get(t['area'], t['area'])}]. {t['definition']}"
            if t.get("excludes"):
                line += f" Not for: {t['excludes']}"
            if t.get("also_request"):
                line += " (Use this even when phrased as a request.)"
            out.append(line)
    return "\n".join(out)


def _keys_block(name: str) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in TAXONOMY[name].items())


def _examples_block(reviews_by_id: dict, labels: dict) -> str:
    parts = []
    for i, rid in enumerate(EXAMPLE_IDS, 1):
        if rid not in reviews_by_id or rid not in labels:
            continue
        parts.append(f"Example {i}\nInput: {json.dumps(review_input(reviews_by_id[rid]), ensure_ascii=False)}\n"
                     f"Output: {json.dumps(label_output(rid, labels[rid]), ensure_ascii=False)}")
    return "\n\n".join(parts)


def system_prompt(reviews_by_id: dict, labels: dict) -> str:
    return f"""You label app-store reviews of SuperKalam, a UPSC (Indian civil services exam) preparation app.

TASK
For each review, find every distinct point it makes. Each point is a "mention": pick exactly one theme id from the list below and copy the exact words from the review that show it as "evidence".

EVIDENCE RULES (most important)
- Evidence must be copied character for character from the review title or text: a short phrase, usually 3 to 12 words. Do not fix spelling, translate, shorten with "...", or join words from different sentences.
- Never write evidence the review does not contain. A label with invented evidence is thrown away.

THEMES (use only these ids)
{_themes_block()}

RULES
- One mention per point. Do not tag the same point with two themes. A review can have several mentions.
- A problem that users also ask for as a feature (marked "use this even when phrased as a request") is labelled with that problem theme, never a request theme.
- Only label what the review actually says. If no theme clearly fits a point, leave that point out. Never guess or stretch a definition.
- Generic praise of the whole app ("super helpful app", "best app for UPSC", "very useful" with nothing more) is not a theme on its own. Tag a praise theme only when the review names what is good about it.
- low_context = true when the review says nothing specific: general praise or complaint with no feature, content, price or problem named ("best app", "very helpful for UPSC", "bad", "👍"). A low-context review has no mentions. A short review that names something specific ("app not opening", "questions only in English") is NOT low context.
- If low_context is false, there must be at least one mention.
- critical: decided by what the review says, not the star rating. Set it only when the user reports one of these, otherwise null:
{_keys_block("critical_categories")}
  critical = {{"category": "<key>", "evidence": "<exact words>"}}.
  - locked_out: the user cannot open or use the app at all (won't open, stuck on the logo, blocks access, won't run on their device). Crashes, lag or glitches where the app still works after a restart are NOT critical.
  - paid_feature_missing: the user says they paid, subscribed or purchased something, and what they paid for is missing, not delivered, changed from what was promised, not working, or they were not told about changes to it. Paying users complaining about the paid product itself count here even when the tone is calm.
  - wrong_charge, refund, paid_no_access: money taken wrongly or automatically, a refund not received, or paid but the plan is not activated.
  - support_failed: the user contacted support and got no answer or no fix.
- rating_mismatch = true only when the stars clearly contradict the text (5 stars with only complaints, 1 star with only praise).
- segments: only when the reviewer states it about themselves or their device. Keys:
{_keys_block("segments")}
- competitors: other apps, platforms, coaching institutes or ChatGPT that the review compares against. "name" is the competitor as written, "context" is one of:
{_keys_block("competitor_contexts")}
  and "evidence" is the exact words.
- sentiment: "positive", "mixed" or "negative", judged from the text. Praise plus a problem or request is usually "mixed".
- Hindi, Hinglish and mixed-language reviews are labelled the same way; evidence stays in the original words and script.

OUTPUT
Return JSON: {{"labels": [ ... ]}} with exactly one object per input review, in the same order, each with:
review_id, mentions [{{theme, evidence}}], low_context, rating_mismatch, critical (object or null), segments [], competitors [{{name, context, evidence}}], sentiment.

WORKED EXAMPLES (labelled by a human; follow the same judgement)

{_examples_block(reviews_by_id, labels)}
"""


def user_prompt(batch: list[dict]) -> str:
    items = [review_input(r) for r in batch]
    return "Label these reviews:\n" + json.dumps(items, ensure_ascii=False, indent=0)


def response_schema() -> dict:
    """Gemini response schema: constrains theme ids and keys to the taxonomy."""
    s = lambda **k: {"type": "STRING", **k}
    ev = s(description="exact words copied from the review")
    return {
        "type": "OBJECT",
        "properties": {"labels": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {
                "review_id": s(),
                "mentions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                    "theme": s(enum=list(TAXONOMY["themes"])), "evidence": ev}, "required": ["theme", "evidence"]}},
                "low_context": {"type": "BOOLEAN"},
                "rating_mismatch": {"type": "BOOLEAN"},
                "critical": {"type": "OBJECT", "nullable": True, "properties": {
                    "category": s(enum=list(TAXONOMY["critical_categories"])), "evidence": ev},
                    "required": ["category", "evidence"]},
                "segments": {"type": "ARRAY", "items": s(enum=list(TAXONOMY["segments"]))},
                "competitors": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                    "name": s(), "context": s(enum=list(TAXONOMY["competitor_contexts"])), "evidence": ev},
                    "required": ["name", "context", "evidence"]}},
                "sentiment": s(enum=["positive", "mixed", "negative"]),
            },
            "required": ["review_id", "mentions", "low_context", "rating_mismatch", "critical",
                         "segments", "competitors", "sentiment"],
            "propertyOrdering": ["review_id", "mentions", "low_context", "rating_mismatch", "critical",
                                 "segments", "competitors", "sentiment"],
        }}},
        "required": ["labels"],
    }
