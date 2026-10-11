"""Every model call Nibble makes: a hosted model, then a local one, then Gemini,
using whichever of the three are configured.

Gemini does the jobs that need judgment: reading a receipt photo, deciding
which store listings are the same product, placing a headline on the map, and
turning cited research into plain language. All arithmetic stays in Python.

Sentry's Google Gen AI integration traces these calls automatically.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Optional

import httpx
import sentry_sdk
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from . import config, obs

_client: genai.Client | None = None
_model: str | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _is_busy(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(w in text for w in ("503", "unavailable", "overloaded", "high demand", "429", "resource_exhausted",
                                   "deadline", "500 internal"))


def _is_unknown_model(exc: Exception) -> bool:
    text = str(exc).lower()
    return "not found" in text or "not_found" in text or "404" in text or "is not supported" in text


_local_down_until = 0.0  # after a failed connection, skip the local model for a minute


async def local_status() -> str:
    """'live', 'not running', 'model missing' or 'off' for the local model."""
    if not config.LOCAL_LIVE:
        return "off"
    try:
        async with httpx.AsyncClient(timeout=2) as http:
            tags = (await http.get(f"{config.OLLAMA_URL}/api/tags")).json()
        names = {m.get("name", "") for m in tags.get("models", [])}
        want = config.LOCAL_MODEL
        return "live" if want in names or f"{want}:latest" in names or any(n.split(":")[0] == want for n in names) else "model missing"
    except Exception:
        return "not running"


async def _local(text: str, schema: type[BaseModel], system: str, label: str, temperature: float,
                 image: tuple[bytes, str] | None) -> Any:
    """One structured call to the local model through Ollama's chat endpoint."""
    message: dict = {"role": "user", "content": text}
    if image:
        message["images"] = [base64.b64encode(image[0]).decode()]
    body = {
        "model": config.LOCAL_MODEL, "stream": False, "keep_alive": "30m",
        "format": schema.model_json_schema(),  # Ollama constrains the reply to this JSON schema
        "options": {"temperature": temperature},
        "messages": [{"role": "system", "content": system + " Reply with JSON only."}, message],
    }
    start = time.perf_counter()
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {config.LOCAL_MODEL}") as span:
        span.set_data("gen_ai.operation.name", "chat")
        span.set_data("gen_ai.provider.name", "ollama")
        span.set_data("gen_ai.request.model", config.LOCAL_MODEL)
        span.set_data("gen_ai.request.temperature", temperature)
        try:
            async with httpx.AsyncClient(timeout=config.LOCAL_TIMEOUT_S) as http:
                resp = await http.post(f"{config.OLLAMA_URL}/api/chat", json=body)
                resp.raise_for_status()
                data = resp.json()
            parsed = schema.model_validate_json(data["message"]["content"])
        except Exception as exc:
            span.set_status("internal_error")
            span.set_data("error.type", type(exc).__name__)
            obs.log("local", f"local.{label}", (time.perf_counter() - start) * 1000, False,
                    f"{config.LOCAL_MODEL}: {type(exc).__name__}: {exc}")
            raise
        span.set_data("gen_ai.response.model", data.get("model") or config.LOCAL_MODEL)
        if isinstance(data.get("prompt_eval_count"), int):
            span.set_data("gen_ai.usage.input_tokens", data["prompt_eval_count"])
        if isinstance(data.get("eval_count"), int):
            span.set_data("gen_ai.usage.output_tokens", data["eval_count"])
        obs.log("local", f"local.{label}", (time.perf_counter() - start) * 1000, True, config.LOCAL_MODEL)
        return parsed


_hosted_down_until = 0.0  # after a rate limit or a rejected key, skip the hosted model for a while
_hosted_loose: set[str] = set()  # schemas this provider would not take as a strict JSON schema


def _json_text(raw: str) -> str:
    """The JSON object inside a reply that may be wrapped in a code fence."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    a, b = raw.find("{"), raw.rfind("}")
    return raw[a:b + 1] if a != -1 and b > a else raw


async def _hosted(text: str, schema: type[BaseModel], system: str, label: str, temperature: float,
                  image: tuple[bytes, str] | None) -> Any:
    """One structured call to an OpenAI-compatible chat endpoint (Groq, Cerebras, OpenRouter...).

    It first asks for the reply to be held to the JSON schema. Providers or models
    that reject that get the schema in the prompt and plain JSON mode instead."""
    model, name = config.LLM_MODEL, schema.__name__
    spec = schema.model_json_schema()
    content: Any = text
    if image:
        content = [{"type": "text", "text": text},
                   {"type": "image_url", "image_url": {"url": f"data:{image[1]};base64,{base64.b64encode(image[0]).decode()}"}}]
    strict = {
        "model": model, "temperature": temperature,
        "messages": [{"role": "system", "content": system + " Reply with one JSON object only."},
                     {"role": "user", "content": content}],
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": spec}},
    }
    if "gpt-oss" in model:
        strict["reasoning_effort"] = "low"  # keeps these reasoning models quick and inside free token limits
    loose = {
        "model": model, "temperature": temperature,
        "messages": [{"role": "system", "content": f"{system} Reply with one JSON object only, matching this JSON schema:\n{json.dumps(spec)}"},
                     {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    host = httpx.URL(config.LLM_BASE_URL).host
    start = time.perf_counter()
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        span.set_data("gen_ai.operation.name", "chat")
        span.set_data("gen_ai.provider.name", host)
        span.set_data("gen_ai.request.model", model)
        span.set_data("gen_ai.request.temperature", temperature)
        try:
            async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as http:
                data = None
                for body in ([loose] if name in _hosted_loose else [strict, loose]):
                    resp = await http.post(f"{config.LLM_BASE_URL}/chat/completions", json=body,
                                           headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
                    if resp.status_code in (400, 422) and body is strict:
                        _hosted_loose.add(name)
                        continue
                    if resp.status_code >= 400:
                        raise RuntimeError(f"{resp.status_code} from {host}: {resp.text[:200]}")
                    data = resp.json()
                    break
            parsed = schema.model_validate_json(_json_text(data["choices"][0]["message"]["content"]))
        except Exception as exc:
            span.set_status("internal_error")
            span.set_data("error.type", type(exc).__name__)
            obs.log("hosted", f"hosted.{label}", (time.perf_counter() - start) * 1000, False,
                    f"{model}: {type(exc).__name__}: {exc}")
            raise
        usage = data.get("usage") or {}
        span.set_data("gen_ai.response.model", data.get("model") or model)
        if isinstance(usage.get("prompt_tokens"), int):
            span.set_data("gen_ai.usage.input_tokens", usage["prompt_tokens"])
        if isinstance(usage.get("completion_tokens"), int):
            span.set_data("gen_ai.usage.output_tokens", usage["completion_tokens"])
        obs.log("hosted", f"hosted.{label}", (time.perf_counter() - start) * 1000, True, model)
        return parsed


async def _generate(text: str, schema: type[BaseModel], system: str, label: str, temperature: float = 0.2,
                    image: tuple[bytes, str] | None = None) -> Any:
    """Structured call. Order: the hosted model, the local model, then Gemini,
    skipping any that is not configured. On Gemini, a busy model (503, 429) is
    retried once and then the next model is tried; an unknown model name moves
    straight to the next."""
    global _model, _local_down_until, _hosted_down_until
    hosted_ok = config.HOSTED_LIVE and time.time() >= _hosted_down_until
    if image and not (config.LLM_VISION and image[1].startswith("image/")):
        hosted_ok = False
    if hosted_ok:
        try:
            return await _hosted(text, schema, system, label, temperature, image)
        except Exception as exc:
            note = str(exc)
            if note.startswith(("401", "403")):
                _hosted_down_until = time.time() + 600  # the key was rejected
            elif note.startswith("429") or isinstance(exc, httpx.TransportError):
                _hosted_down_until = time.time() + 20  # rate limited or unreachable
            if not (config.LOCAL_LIVE or config.GEMINI_LIVE):
                raise
    local_ok = config.LOCAL_LIVE and time.time() >= _local_down_until
    if image and not (config.LOCAL_VISION and image[1].startswith("image/")):
        local_ok = False  # PDFs, or a text-only local model
    if local_ok:
        try:
            return await _local(text, schema, system, label, temperature, image)
        except Exception as exc:
            if isinstance(exc, httpx.TransportError):
                # Not reachable, or too slow to be useful: leave it alone for a while.
                _local_down_until = time.time() + (120 if isinstance(exc, httpx.TimeoutException) else 60)
            if not config.GEMINI_LIVE:
                raise
    if not config.GEMINI_LIVE:
        raise RuntimeError("No model is available: none of the configured models answered and no Gemini key is set")

    contents: Any = text
    if image:
        contents = [types.Part.from_bytes(data=image[0], mime_type=image[1]), text]
    names: list[str] = []
    for name in [_model, config.GEMINI_MODEL, *config.GEMINI_FALLBACK_MODELS]:
        if name and name not in names:
            names.append(name)
    last: Exception | None = None
    for name in names:
        for attempt in (1, 2):
            start = time.perf_counter()
            try:
                resp = await client().aio.models.generate_content(
                    model=name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
                _model = name
                obs.log("gemini", f"gemini.{label}", (time.perf_counter() - start) * 1000, True, name)
                if resp.parsed is not None:
                    return resp.parsed
                return schema.model_validate(json.loads(resp.text or "{}"))
            except Exception as exc:
                last = exc
                obs.log("gemini", f"gemini.{label}", (time.perf_counter() - start) * 1000, False, f"{name}: {exc}")
                if _is_busy(exc) and attempt == 1:
                    await asyncio.sleep(1.5)
                    continue  # same model, one more try
                if _is_busy(exc) or _is_unknown_model(exc):
                    break  # next model
                raise  # a real error (bad key, bad request): do not hide it
    assert last is not None
    raise last


# ------------------------------------------------------------------- receipt


class ReceiptLine(BaseModel):
    raw: str = Field(description="The line exactly as printed on the receipt")
    item: str = Field(description="Plain shopping-list name a person would search for, e.g. 'large eggs 12 count'")
    quantity: float = Field(description="Units bought; 1 if not printed")
    price_paid: Optional[float] = Field(default=None, description="Total paid for this line in dollars")


class Receipt(BaseModel):
    store: Optional[str] = None
    date: Optional[str] = None
    lines: list[ReceiptLine]
    total: Optional[float] = None


async def parse_receipt(image: bytes, mime_type: str) -> Receipt:
    system = (
        "You read grocery receipts. Return only purchasable product lines. Skip tax, "
        "subtotal, payment, coupons, bag fees and loyalty lines. Expand store abbreviations "
        "into a plain product name. If a price is unreadable leave it null. Never invent lines."
    )
    return await _generate("Extract this receipt.", Receipt, system, "receipt", 0.0, image=(image, mime_type))


# ------------------------------------------------------------ offer matching


class Pick(BaseModel):
    group: int = Field(description="Index of the store group")
    choice: int = Field(description="Index of the best comparable listing in that group, or -1 if none is the requested product")
    pack_quantity: Optional[float] = Field(default=None, description="Amount in the pack as a number, e.g. 12, 64, 1.5")
    pack_unit: Optional[str] = Field(default=None, description="One of: count, oz, fl oz, lb, gal, L, kg, g")
    note: Optional[str] = Field(default=None, description="At most 8 words, only if the match needs a caveat")


class ItemPicks(BaseModel):
    item: int = Field(description="Index of the shopping-list item")
    picks: list[Pick]


class MatchResult(BaseModel):
    items: list[ItemPicks]


async def match_offers(payload: list[dict], persona_text: str) -> MatchResult:
    """payload: [{item, name, groups:[{group, store, listings:[{i, name, price, size}]}]}]"""
    system = (
        "You compare grocery listings across stores. For each shopping-list item and each store "
        "group, choose the one listing a careful shopper would accept as the same everyday product: "
        "the standard size, not a multipack, bulk case, accessory, or a different product that only "
        "shares a word. Honor the shopper's preferences. If nothing in a group is the requested "
        "product, return -1 for that group. Read the pack size from the listing name; do not guess."
    )
    prompt = f"Shopper preferences: {persona_text or 'none stated'}\n\nListings:\n{json.dumps(payload)}"
    return await _generate(prompt, MatchResult, system, "match", 0.0)


# ----------------------------------------------------------------- news tags


class NewsTag(BaseModel):
    index: int
    place: Optional[str] = Field(default=None, description="The main real-world place this story is about; null if it has none")
    lat: Optional[float] = None
    lng: Optional[float] = None
    category: str = Field(description="One of: markets, food, energy, shipping, trade, weather, labor, tech, other")
    wallet_impact: str = Field(description="One plain sentence on what this could mean for a household's costs, using only the headline and description. Say 'No direct household effect stated.' if none.")
    groceries: list[str] = Field(description="Grocery staples plausibly affected, lowercase, at most 3")


class NewsTags(BaseModel):
    tags: list[NewsTag]


async def tag_news(headlines: list[dict]) -> NewsTags:
    system = (
        "You place news on a map for a household-finance app. Use only the text given. "
        "Do not add facts, numbers, or predictions. No investment advice."
    )
    compact = [{"index": i, "title": h["title"], "description": h.get("description", "")[:300]} for i, h in enumerate(headlines)]
    return await _generate(json.dumps(compact), NewsTags, system, "news_tags", 0.1)


# ------------------------------------------------------------ why narration


class Located(BaseModel):
    index: int
    lat: float
    lng: float


class WhyNarration(BaseModel):
    plain_summary: str = Field(description="Two or three sentences a shopper would understand. Only restate what the research says.")
    shopper_tip: str = Field(description="One practical, low-risk shopping suggestion, e.g. a substitute or timing")
    locations: list[Located] = Field(description="Coordinates for each driver's location_name, by driver index")


async def narrate_why(item: str, research: dict) -> WhyNarration:
    system = (
        "You explain grocery prices to a shopper. Restate only what the research JSON says; "
        "add no new causes, numbers, or forecasts. Give coordinates for each driver's named place. "
        "No investment advice."
    )
    return await _generate(f"Item: {item}\nResearch:\n{json.dumps(research)[:12000]}", WhyNarration, system, "why_narration", 0.2)


# ------------------------------------------------------------------ persona


class PersonaTags(BaseModel):
    tags: list[str] = Field(description="Short lowercase habits visible in the items, e.g. 'buys organic', 'store brands', 'plant milk'. At most 6.")


async def learn_persona(items: list[str], known: list[str]) -> PersonaTags:
    system = (
        "From a shopping list, name buying habits that are directly visible in the product names. "
        "Do not infer health, religion, income, family, or anything personal. Habits only."
    )
    return await _generate(f"Known habits: {known}\nItems: {items}", PersonaTags, system, "persona", 0.0)


# -------------------------------------------------------------------- router


class Route(BaseModel):
    intent: str = Field(description="Exactly one of: basket, season, why, invest, learn, ask")
    items: list[str] = Field(description="For basket: each thing to buy as a short searchable name, with the size if the person gave one. Otherwise an empty list.")
    topic: str = Field(description="For why: the grocery item. For invest or learn: the subject. For ask: the question. For season: empty string.")


async def route(text: str) -> Route:
    system = (
        "You route one sentence typed into a household-finance app. basket = they list groceries to price. "
        "season = they ask what is in season or what to buy now. why = they ask why a grocery item costs what "
        "it costs. invest = they ask about markets, stocks, sectors, or what an event means for investors. "
        "learn = they want a finance concept explained or want flashcards. ask = any other question."
    )
    return await _generate(text[:400], Route, system, "route", 0.0)


# -------------------------------------------------------------------- season


class SeasonItem(BaseModel):
    name: str = Field(description="The produce item, lowercase, e.g. 'apples'")
    why_now: str = Field(description="At most 12 words, taken from the text, on why it is good or cheaper now")
    list_name: str = Field(description="How to write it on a shopping list so a store search finds it, e.g. 'gala apples 3 lb bag'")


class Season(BaseModel):
    items: list[SeasonItem] = Field(description="Up to 8 items the text says are in season")


async def season_items(answer_text: str, place: str, month: str) -> Season:
    system = (
        "From the text, list produce that is in season. Use only items the text names. "
        "Do not add items from your own knowledge."
    )
    return await _generate(f"Place: {place}. Month: {month}.\nText:\n{answer_text[:4000]}", Season, system, "season", 0.0)


# ----------------------------------------------------------------- flashcards


class Card(BaseModel):
    term: str = Field(description="The finance concept, 1 to 4 words, e.g. 'Tariff pass-through'")
    front: str = Field(description="A question a curious beginner would ask, at most 14 words")
    back: str = Field(description="The answer in plain words, at most 45 words, no jargon left unexplained")
    example: str = Field(description="One sentence tying the concept to the given headline or text, using only facts stated there")
    headline_index: int = Field(description="Index of the headline this card draws on, or -1 if it draws on the background text")
    quiz_question: str = Field(description="A multiple-choice question testing the concept")
    quiz_options: list[str] = Field(description="Exactly 3 short options")
    quiz_answer: int = Field(description="Index 0 to 2 of the correct option")


class Deck(BaseModel):
    cards: list[Card]


async def flashcards(headlines: list[dict], topic: str, background: str) -> Deck:
    system = (
        "You write finance flashcards for a beginner, built from today's news. Each card teaches one concept "
        "that a headline illustrates (inflation, tariffs, futures, supply shocks, interest rates, unit price...). "
        "Examples must use only facts in the headline or background text. Teach how things work. "
        "Never recommend buying or selling anything."
    )
    compact = [{"index": i, "title": h.get("title"), "description": (h.get("description") or "")[:240]} for i, h in enumerate(headlines)]
    prompt = (f"Write 5 cards{' about ' + topic if topic else ''}.\nHeadlines:\n{json.dumps(compact)}\n"
              f"Background text:\n{background[:3000] or '(none)'}")
    return await _generate(prompt, Deck, system, "flashcards", 0.4)


class StepPlace(BaseModel):
    step_index: int = Field(description="Index of the step this place belongs to")
    place: str = Field(description="A real city, region or country where this step happens, e.g. 'Texas, United States'")
    latitude: float
    longitude: float


class Term(BaseModel):
    term: str = Field(description="A finance term the lesson uses or illustrates, 1 to 4 words")
    meaning: str = Field(description="Its plain meaning in at most 25 words")


class LessonExtras(BaseModel):
    places: list[StepPlace] = Field(description="One entry per step")
    concepts: list[Term] = Field(description="Exactly 3 terms")
    quiz_question: str = Field(description="One multiple-choice question that tests the reasoning in the lesson, not a number")
    quiz_options: list[str] = Field(description="Exactly 3 short options")
    quiz_answer: int = Field(description="Index 0 to 2 of the correct option")
    quiz_why: str = Field(description="One sentence on why that option is right, using only the lesson text")


async def lesson_extras(lesson: dict) -> LessonExtras:
    """Map places, words to know and one check question for a lesson the research agent wrote."""
    system = (
        "You help a beginner study a short investing lesson. Use only what the lesson says. For each step, name "
        "the real place on Earth where it happens so it can be shown on a map. Pick three finance terms and "
        "explain them in plain words. Write one question that checks the reader followed the reasoning. "
        "Never recommend buying or selling anything."
    )
    compact = {
        "story": lesson.get("original", {}).get("title"),
        "what_happened": lesson.get("what_happened"),
        "why_it_matters": lesson.get("why_it_matters"),
        "steps": [{"index": i, "step": s.get("what"), "place_hint": s.get("place")} for i, s in enumerate(lesson.get("chain") or [])],
        "affected": [{"name": a.get("name"), "effect": a.get("effect"), "because": a.get("because")} for a in lesson.get("affected") or []],
    }
    return await _generate(json.dumps(compact), LessonExtras, system, "lesson_extras", 0.2)
