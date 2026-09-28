"""
LLM client. label_batch() sends up to ~10 reviews and returns the model's raw labels (unvalidated).
Provider and model come from config; switching provider is config-only.
"""
from __future__ import annotations

import json
import time

import requests

import config
import prompt

# Tried in order when LLM_MODEL is not set; the next one is used when a model is busy or not available.
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.5-flash-lite"]
OPENAI_COMPAT_URL = "https://api.groq.com/openai/v1/chat/completions"

BATCH_SIZE = 10
CALL_GAP_S = 5          # polite gap between calls to stay under free-tier per-minute limits
RETRIES = 4             # per model, on 429 / 5xx
RETRY_STATUS = {429, 500, 502, 503, 504}

_last_call = 0.0
_context: tuple[dict, dict] | None = None
_skip: set[str] = set()  # models that failed earlier in this run; tried again only if nothing else works


class LLMError(RuntimeError):
    pass


def _examples_context() -> tuple[dict, dict]:
    global _context
    if _context is None:
        reviews = json.loads((prompt.ROOT / "data" / "reviews.json").read_text(encoding="utf-8"))
        labels = json.loads((prompt.ROOT / "data" / "labels.json").read_text(encoding="utf-8"))
        _context = ({r["review_id"]: r for r in reviews}, labels)
    return _context


def _pace() -> None:
    global _last_call
    wait = CALL_GAP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _error_text(r: requests.Response) -> str:
    try:
        return r.json().get("error", {}).get("message", "")[:200]
    except ValueError:
        return r.text[:200]


def _gemini(model: str, system: str, user: str) -> str:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": config.LLM_API_KEY},
        json={"systemInstruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": [{"text": user}]}],
              "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                   "responseSchema": prompt.response_schema()}},
        timeout=180)
    if not r.ok:
        raise _HTTPError(r.status_code, _error_text(r))
    cand = (r.json().get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise _HTTPError(500, f"empty response (finishReason={cand.get('finishReason')})")
    return text


def _openai_compat(model: str, system: str, user: str) -> str:
    r = requests.post(config.get("LLM_BASE_URL", OPENAI_COMPAT_URL),
                      headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                      json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                      timeout=180)
    if not r.ok:
        raise _HTTPError(r.status_code, _error_text(r))
    return r.json()["choices"][0]["message"]["content"]


class _HTTPError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(f"{status}: {msg}")
        self.status = status
        self.msg = msg


PROVIDERS = {"gemini": _gemini, "openai_compat": _openai_compat}


def models() -> list[str]:
    if config.LLM_MODEL:
        return [config.LLM_MODEL]
    if config.LLM_PROVIDER == "gemini":
        return GEMINI_MODELS
    raise LLMError(f"Set LLM_MODEL in .env for provider {config.LLM_PROVIDER!r}.")


def label_batch(reviews: list[dict]) -> list[dict]:
    """Label up to ~10 reviews. Returns one raw label dict per review that the model answered,
    each with "_model" set to "<provider>/<model>". Raises LLMError when every model fails."""
    config.require_llm()
    call = PROVIDERS.get(config.LLM_PROVIDER)
    if not call:
        raise LLMError(f"Unknown LLM_PROVIDER {config.LLM_PROVIDER!r}; use one of {sorted(PROVIDERS)}.")
    system = prompt.system_prompt(*_examples_context())
    user = prompt.user_prompt(reviews)
    failures = []
    order = models()
    order = [m for m in order if m not in _skip] + [m for m in order if m in _skip]
    for model in order:
        last = model == order[-1]
        for attempt in range(RETRIES if last else 2):
            _pace()
            try:
                data = json.loads(call(model, system, user))
            except _HTTPError as e:
                quota_zero = e.status == 429 and "limit: 0" in e.msg
                if e.status in RETRY_STATUS and not quota_zero and attempt < (RETRIES if last else 2) - 1:
                    time.sleep(10 * 2 ** attempt)
                    continue
                failures.append(f"{model}: {e.status} {e.msg[:120]}")
                _skip.add(model)
                break
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                if attempt < (RETRIES if last else 2) - 1:
                    continue
                failures.append(f"{model}: unreadable output ({e.__class__.__name__})")
                break
            except requests.RequestException as e:
                if attempt < (RETRIES if last else 2) - 1:
                    time.sleep(10 * 2 ** attempt)
                    continue
                failures.append(f"{model}: network error ({e.__class__.__name__})")
                break
            _skip.discard(model)
            items = data.get("labels", data if isinstance(data, list) else [])
            for it in items:
                if isinstance(it, dict):
                    it["_model"] = f"{config.LLM_PROVIDER}/{model}"
            return [it for it in items if isinstance(it, dict)]
    raise LLMError("All models failed: " + " | ".join(failures))


def batches(items: list, size: int = BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]
