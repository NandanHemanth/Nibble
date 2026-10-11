"""The four agents. Each is a pipeline of Nimble tool calls plus Gemini judgment.

  Basket Scout     list + ZIP  -> cheapest basket across stores
  Receipt Reader   photo       -> lines -> Basket Scout -> what you overpaid
  Price Detective  one item    -> cited causes, placed on the globe, and who else is exposed
  News Desk        today       -> headlines pinned to the globe with a household angle
  Signal Scout     a company   -> alternative-data readings with citations
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time

from . import config, demo, gemini, nimble_tools as nimble, obs, persona, world

CACHE_DIR = config.DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(kind: str, key: str):
    return CACHE_DIR / f"{kind}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def cache_get(kind: str, key: str, ttl: int):
    path = _cache_path(kind, key)
    try:
        if time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


def cache_put(kind: str, key: str, value) -> None:
    try:
        _cache_path(kind, key).write_text(json.dumps(value))
    except Exception:
        pass


# ------------------------------------------------------------- unit pricing

_BASE = {  # unit -> (family, multiplier into the family's base unit)
    "count": ("count", 1), "ct": ("count", 1), "each": ("count", 1), "pack": ("count", 1),
    "oz": ("oz", 1), "lb": ("oz", 16), "lbs": ("oz", 16), "g": ("oz", 0.035274), "kg": ("oz", 35.274),
    "fl oz": ("fl oz", 1), "gal": ("fl oz", 128), "gallon": ("fl oz", 128), "l": ("fl oz", 33.814),
    "ml": ("fl oz", 0.033814), "qt": ("fl oz", 32), "pt": ("fl oz", 16),
}
_SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*-?\s*(fl\.?\s?oz|fluid ounces?|ounces?|oz|pounds?|lbs?|gallons?|gal|liters?|l|ml|kg|g|count|ct|pack|quarts?|qt|pints?|pt)\b",
    re.I,
)


def parse_size(text: str) -> tuple[float | None, str | None]:
    """Pack size from a listing name, e.g. '12 Count', '52 fl oz', '1 Gallon'."""
    if not text:
        return None, None
    m = _SIZE_RE.search(text)
    if not m:
        return None, None
    qty = float(m.group(1))
    u = m.group(2).lower().replace(".", "")
    u = {"fluid ounce": "fl oz", "fluid ounces": "fl oz", "floz": "fl oz", "ounce": "oz", "ounces": "oz",
         "pound": "lb", "pounds": "lb", "lbs": "lb", "gallon": "gal", "gallons": "gal", "liter": "l",
         "liters": "l", "ct": "count", "pack": "count", "quart": "qt", "quarts": "qt", "pint": "pt",
         "pints": "pt"}.get(u, u)
    return (qty, u) if qty > 0 else (None, None)


def unit_price(price: float, qty: float | None, unit: str | None) -> tuple[float | None, str | None]:
    if not qty or not unit:
        return None, None
    fam = _BASE.get(unit.lower().strip())
    if not fam:
        return None, None
    base_qty = qty * fam[1]
    return (price / base_qty, fam[0]) if base_qty > 0 else (None, None)


def _family(unit: str | None) -> str | None:
    return _BASE.get((unit or "").lower(), (None, 0))[0]


def _base(qty: float, unit: str | None) -> float:
    return qty * _BASE.get((unit or "").lower(), (None, 1))[1]


# ------------------------------------------------------------ Basket Scout


async def _offers_live(item: str, zip_code: str, agent: str, tick=None) -> tuple[dict[str, list[dict]], list[str]]:
    """Listings for one item from every configured store template, in parallel."""

    async def one(template: str):
        try:
            return await nimble.store_search(template, item, zip_code, agent)
        finally:
            if tick:
                tick()

    results = await asyncio.gather(*(one(t) for t in config.STORE_TEMPLATES), return_exceptions=True)
    groups: dict[str, list[dict]] = {}
    notes: list[str] = []
    for template, res in zip(config.STORE_TEMPLATES, results):
        store = nimble.store_label(template)
        if isinstance(res, Exception):
            notes.append(f"{store}: {'timed out' if isinstance(res, TimeoutError) else type(res).__name__}")
            obs.capture(res)
            continue
        if res:
            groups[store] = res[:8]
    return groups, notes


def _heuristic_pick(item: str, listings: list[dict]) -> int:
    """Without Gemini: the cheapest listing that contains the item's words and,
    when the item names a size, has that size."""
    words = [w for w in re.findall(r"[a-z]+", item.lower())
             if len(w) > 2 and w not in ("count", "pack", "the", "and", "gallon", "ounce", "pound")]
    want_qty, want_unit = parse_size(item)
    best, best_key = -1, None
    for i, o in enumerate(listings):
        name = o["name"].lower()
        hits = sum(1 for w in words if w.rstrip("s") in name)
        if not words or hits < max(1, len(words) - 1):
            continue
        qty, unit = (o.get("pack_quantity"), o.get("pack_unit")) if o.get("pack_quantity") else parse_size(o["name"])
        size_miss = bool(want_qty and qty and (_family(unit) != _family(want_unit)
                                                or abs(_base(qty, unit) - _base(want_qty, want_unit)) > 0.15 * _base(want_qty, want_unit)))
        key = (size_miss, o.get("sponsored", False), -hits, o["price"])
        if best_key is None or key < best_key:
            best, best_key = i, key
    return best


async def basket(items: list[str], zip_code: str, progress=None) -> dict:
    items = [i.strip() for i in items if i and i.strip()][:15]
    agent = "Basket Scout"
    home = await world.locate_zip(zip_code)
    p = persona.load()
    skip = {s.lower() for s in p["skip_stores"]}
    with obs.agent_span(agent, f"Cheapest basket for {items} near {home['zip']}"):
        notes: list[str] = []
        sample = not config.NIMBLE_LIVE
        if sample:
            per_item = [demo.offers_for(i) for i in items]
        else:
            sem = asyncio.Semaphore(5)
            total, done = len(items) * len(config.STORE_TEMPLATES), 0

            def tick() -> None:
                nonlocal done
                done += 1
                if progress:
                    progress(f"{done} of {total} store lookups done", 0)

            if progress:
                progress(f"0 of {total} store lookups done", 0)

            async def one(i: str):
                async with sem:
                    return await _offers_live(i, home["zip"], agent, tick)

            fetched = await asyncio.gather(*(one(i) for i in items))
            per_item = [f[0] for f in fetched]
            notes = sorted({n for f in fetched for n in f[1]})

        per_item = [{s: ls for s, ls in g.items() if s.lower() not in skip} for g in per_item]

        # Which listing in each store is the requested product?
        chosen: list[dict[str, dict]] = [dict() for _ in items]
        used_gemini = False
        if config.llm_live() and not sample and any(per_item):
            payload, index = [], []
            for ii, groups in enumerate(per_item):
                stores = list(groups)
                index.append(stores)
                payload.append({"item": ii, "name": items[ii], "groups": [
                    {"group": gi, "store": s, "listings": [
                        {"i": li, "name": o["name"], "price": o["price"],
                         "size": (f"{o['pack_quantity']:g} {o['pack_unit']}" if o.get("pack_quantity") else None)}
                        for li, o in enumerate(groups[s])]}
                    for gi, s in enumerate(stores)]})
            try:
                if progress:
                    progress("Gemini is matching products across stores", 0)
                match = await gemini.match_offers(payload, persona.as_text(p))
                used_gemini = True
                for ip in match.items:
                    if not 0 <= ip.item < len(items):
                        continue
                    for pick in ip.picks:
                        stores = index[ip.item]
                        if not 0 <= pick.group < len(stores):
                            continue
                        listings = per_item[ip.item][stores[pick.group]]
                        if 0 <= pick.choice < len(listings):
                            o = dict(listings[pick.choice])
                            o["note"] = pick.note
                            if not o.get("pack_quantity"):  # the shelf's own unit price wins over a reading of the name
                                o["pack_quantity"], o["pack_unit"] = pick.pack_quantity, pick.pack_unit
                            chosen[ip.item][stores[pick.group]] = o
            except Exception as exc:
                obs.capture(exc)
                notes.append(f"Gemini matching failed ({type(exc).__name__}); used word matching instead")
        if not used_gemini:
            for ii, groups in enumerate(per_item):
                for store, listings in groups.items():
                    k = 0 if sample else _heuristic_pick(items[ii], listings)
                    if k >= 0:
                        chosen[ii][store] = dict(listings[k])

        # Arithmetic in code: unit prices, winners, totals.
        rows = []
        for ii, picks in enumerate(chosen):
            out = []
            for store, o in picks.items():
                qty, unit = o.get("pack_quantity"), o.get("pack_unit")
                if not qty or not unit:
                    qty, unit = parse_size(o["name"])
                if not qty or not unit:
                    qty, unit = parse_size(o.get("size_hint") or "")
                up, fam = unit_price(o["price"], qty, unit)
                out.append({"store": store, "name": o["name"], "price": o["price"], "url": o.get("url"),
                            "pack_quantity": qty, "pack_unit": unit, "unit_price": round(up, 4) if up else None,
                            "unit_family": fam, "localized": o.get("localized", False), "note": o.get("note"),
                            "source": o.get("source"), "priced_for": o.get("priced_for"),
                            "shelf_unit_price": o.get("size_hint")})
            families = {r["unit_family"] for r in out if r["unit_family"]}
            comparable = len(out) > 1 and len(families) == 1 and all(r["unit_price"] for r in out)
            out.sort(key=(lambda r: r["unit_price"]) if comparable else (lambda r: r["price"]))
            spread = round(max(r["price"] for r in out) - min(r["price"] for r in out), 2) if len(out) > 1 else 0.0
            rows.append({"item": items[ii], "picks": out, "ranked_by": "unit price" if comparable else "shelf price",
                         "spread": spread})

        stores = sorted({r["store"] for row in rows for r in row["picks"]})
        found = [row for row in rows if row["picks"]]
        split_total = round(sum(row["picks"][0]["price"] for row in found), 2)
        by_store = []
        for s in stores:
            prices = [next((r["price"] for r in row["picks"] if r["store"] == s), None) for row in found]
            have = [x for x in prices if x is not None]
            by_store.append({"store": s, "total": round(sum(have), 2), "items_found": len(have),
                             "complete": len(have) == len(found) and bool(found)})
        complete = sorted((b for b in by_store if b["complete"]), key=lambda b: b["total"])
        best_single = complete[0] if complete else None
        worst_single = complete[-1] if complete else None
        result = {
            "sample": sample,
            "home": home,
            "items": rows,
            "missing": [row["item"] for row in rows if not row["picks"]],
            "totals": {
                "split": split_total,
                "by_store": sorted(by_store, key=lambda b: (not b["complete"], b["total"])),
                "best_single_store": best_single,
                "saved_by_splitting": round(best_single["total"] - split_total, 2) if best_single else None,
                "saved_vs_priciest_store": round(worst_single["total"] - split_total, 2) if worst_single else None,
            },
            "matched_by": "gemini" if used_gemini else ("sample" if sample else "word match"),
            "persona": persona.as_text(p),
            "notes": notes,
        }
        if found:
            winners: dict[str, int] = {}
            for row in found:
                winners[row["picks"][0]["store"]] = winners.get(row["picks"][0]["store"], 0) + 1
            persona.remember_basket(items, split_total, result["totals"]["saved_vs_priciest_store"], winners)
        result["patterns"] = persona.patterns()
        return result


# ---------------------------------------------------------- Receipt Reader


async def receipt(image: bytes | None, mime_type: str, zip_code: str, progress=None) -> dict:
    with obs.agent_span("Receipt Reader", "Read a receipt and find where each line is cheaper"):
        sample = not (config.llm_live() and image)
        if sample:
            parsed = demo.SAMPLE_RECEIPT
        else:
            if progress:
                progress("Gemini is reading the receipt", 0)
            parsed = (await gemini.parse_receipt(image, mime_type)).model_dump()
        lines = [l for l in parsed["lines"] if l.get("item")][:15]
        if not lines:
            return {"sample": sample, "receipt_sample": sample, "store": parsed.get("store"), "date": parsed.get("date"),
                    "paid_total": parsed.get("total"), "lines": [], "overpaid": 0.0, "basket": None,
                    "persona": persona.load(), "notes": ["No product lines could be read from that photo."]}
        result = await basket([l["item"] for l in lines], zip_code, progress)
        compared, overpaid = [], 0.0
        for line, row in zip(lines, result["items"]):
            best = row["picks"][0] if row["picks"] else None
            paid = line.get("price_paid")
            qty = line.get("quantity") or 1
            diff = round(paid - best["price"] * qty, 2) if (best and paid is not None) else None
            if diff and diff > 0:
                overpaid += diff
            compared.append({"raw": line.get("raw"), "item": line["item"], "paid": paid, "quantity": qty,
                             "best_price": best["price"] if best else None, "best_store": best["store"] if best else None,
                             "difference": diff})
        if config.llm_live() and not sample:
            try:
                tags = await gemini.learn_persona([l["item"] for l in lines], persona.load()["learned"])
                persona.add_learned(tags.tags)
            except Exception as exc:
                obs.capture(exc)
        return {"sample": sample or result["sample"], "receipt_sample": sample, "store": parsed.get("store"), "date": parsed.get("date"),
                "paid_total": parsed.get("total"), "lines": compared, "overpaid": round(overpaid, 2),
                "basket": result, "persona": persona.load()}


# --------------------------------------------------------- Price Detective

WHY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "The main reason as a short newspaper headline, 10 words at most"},
        "direction": {"type": "string", "enum": ["up", "down", "flat"], "description": "US retail price trend over the last 12 months"},
        "change_summary": {"type": "string", "description": "One sentence with the reported price change and its time period, as stated by a source"},
        "drivers": {
            "type": "array",
            "description": "The 1 to 3 biggest real-world causes, most important first",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Under 6 words"},
                    "category": {"type": "string", "enum": ["weather", "disease", "energy", "shipping", "trade_policy", "labor", "demand", "currency", "other"]},
                    "explanation": {"type": "string", "description": "Two sentences: what happened and how it reaches the shelf price"},
                    "location_name": {"type": "string", "description": "The specific place where this cause is happening, e.g. 'Minas Gerais, Brazil'"},
                    "latitude": {"type": ["number", "null"]},
                    "longitude": {"type": ["number", "null"]},
                },
                "required": ["title", "category", "explanation", "location_name"],
            },
        },
        "exposed": {
            "type": "array",
            "description": "Up to 4 entries whose costs or revenue are tied to the same causes. Include US-listed public companies (producers, processors, brands, retailers) when any exist, then commodities or sectors",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ticker": {"type": ["string", "null"], "description": "US exchange ticker if listed on NYSE or Nasdaq, else null"},
                    "exchange": {"type": ["string", "null"], "description": "'xnys' for NYSE or 'xnas' for Nasdaq when a ticker is given, else null"},
                    "kind": {"type": "string", "enum": ["company", "commodity", "sector", "etf"]},
                    "relationship": {"type": "string", "description": "One factual sentence on how it is tied to this cause. No predictions or advice."},
                },
                "required": ["name", "kind", "relationship"],
            },
        },
        "outlook": {"type": "string", "description": "One sentence on what sources say happens next, attributed, or an empty string"},
        "risks": {"type": "array", "description": "Up to 3 things sources say could change this picture, one sentence each",
                  "items": {"type": "string"}},
    },
    "required": ["headline", "direction", "change_summary", "drivers", "exposed"],
}

LENS_SKILL = (
    "You are a markets explainer for a beginner who wants to understand how world events connect to companies "
    "and sectors. For a topic, find what is happening now and where, then name the US-listed companies, ETFs, "
    "commodities and sectors whose costs or revenue are tied to it, with one factual sentence each. List what "
    "could change the picture. You explain exposure. You never say what to buy, sell or hold, and you never "
    "predict prices."
)

WHY_SKILL = (
    "You are a food-price analyst writing for a grocery shopper. Trace a US retail price back to the "
    "physical events behind it: weather, crop or animal disease, energy, shipping lanes, trade policy, labor. "
    "Prefer the single biggest cause over a long list. Every cause needs a specific place on Earth. "
    "Report price changes only as stated by a source, with the period. State relationships factually; "
    "never predict prices or give investment advice."
)
WHY_SOURCES = (
    "USDA Economic Research Service, USDA market reports, BLS CPI and average price data, the Federal Reserve, "
    "Reuters, AP, Bloomberg, company filings and earnings calls. Avoid blogs and coupon sites."
)


def _attach_trust(result: dict, trust: dict) -> None:
    """Attach each trust claim's citations to the driver or exposure it backs."""
    for claim in trust.get("claims") or []:
        path = claim.get("path") or ""
        cites = [{"url": c.get("url"), "title": c.get("title"), "excerpts": (c.get("excerpts") or [])[:2],
                  "category": c.get("source_category")} for c in (claim.get("citations") or []) if c.get("url")]
        if not cites:
            continue
        for key in ("drivers", "exposed"):
            m = re.search(rf"{key}\[(\d+)\]", path)
            if m and int(m.group(1)) < len(result.get(key) or []):
                target = result[key][int(m.group(1))]
                have = {c["url"] for c in target.setdefault("citations", [])}
                target["citations"] += [c for c in cites if c["url"] not in have]
                rank = {"low": 0, "medium": 1, "high": 2}
                conf, current = claim.get("confidence"), target.get("confidence")
                if conf in rank and (current not in rank or rank[conf] < rank[current]):
                    target["confidence"] = conf  # a driver is only as sure as its weakest claim
                break
        else:
            have = {c["url"] for c in result.setdefault("citations", [])}
            result["citations"] += [c for c in cites if c["url"] not in have]


async def lens(topic: str, zip_code: str, effort: str | None = None, on_status=None) -> dict:
    """Market Lens: what a world event or theme touches. Same machinery as the Price Detective."""
    return await why(topic, zip_code, effort, on_status, as_lens=True)


async def why(item: str, zip_code: str, effort: str | None = None, on_status=None, as_lens: bool = False) -> dict:
    item = item.strip()[:120 if as_lens else 80]
    effort = effort or config.AGENT_EFFORT
    agent = "Market Lens" if as_lens else "Price Detective"
    nimble_agent = "nibble-market-lens" if as_lens else "nibble-price-detective"
    kind = "lens" if as_lens else "why"
    home = await world.locate_zip(zip_code)
    with obs.agent_span(agent, f"What does {item} touch?" if as_lens else f"Why did the price of {item} change?"):
        if not config.NIMBLE_LIVE:
            return {**demo.why_for(item), "home": home, "trust": None, "seconds": 0, "effort": "sample", "lens": as_lens,
                    "risks": []}
        key = f"{item.lower()}|{effort}"
        cached = cache_get(kind, key, 6 * 3600)
        if cached:
            return {**cached, "home": home, "cached": True}

        if as_lens:
            task = (
                f"Topic: {item}. What is happening in the world right now around this topic, and where? Which "
                f"US-listed public companies, ETFs, commodities or sectors are exposed to it, and how? "
                f"What do sources say could change the picture?"
            )
            skill = LENS_SKILL
        else:
            task = (
                f"Why has the US retail price of {item} changed over the last 12 months? Identify the biggest "
                f"real-world causes, where on Earth each is happening, and which public companies, commodities "
                f"or sectors are exposed to the same causes."
            )
            # The shopper's profile rides along in the briefing.
            skill = f"{WHY_SKILL} The shopper you are writing for: {persona.as_text()}."
        # A deeper run continues from the quick one instead of starting over.
        earlier = (cache_get(f"{kind}_thread", item.lower(), 6 * 3600) or {}).get("interaction_id") if effort != "low" else None
        try:
            run = await nimble.agent_start(task, nimble_agent, WHY_SCHEMA, skill, WHY_SOURCES, effort,
                                           previous_interaction_id=earlier)
        except Exception:
            if not earlier:
                raise
            earlier = None
            run = await nimble.agent_start(task, nimble_agent, WHY_SCHEMA, skill, WHY_SOURCES, effort)
        cache_put(f"{kind}_thread", item.lower(), {"interaction_id": run["interaction_id"]})
        out = await nimble.agent_wait(run["run_id"], run["agent_id"], nimble_agent, on_status=on_status)
        content = out["content"] if isinstance(out["content"], dict) else {}
        trust = out.get("trust") or {}
        result = {
            "item": item, "sample": False,
            "headline": content.get("headline") or "",
            "direction": content.get("direction") or "flat",
            "change_summary": content.get("change_summary") or "",
            "drivers": [{**d, "lat": d.get("latitude"), "lng": d.get("longitude")} for d in (content.get("drivers") or [])[:3]],
            "exposed": (content.get("exposed") or [])[:4],
            "outlook": content.get("outlook") or "",
            "plain_summary": "", "shopper_tip": "",
            "trust": {"confidence": trust.get("confidence"), "reasoning": trust.get("reasoning"),
                      "sources": [{"url": s.get("url"), "title": s.get("title"), "type": s.get("type"),
                                   "category": s.get("source_category")} for s in (trust.get("sources") or [])[:12]]},
            "seconds": out.get("seconds"), "effort": effort, "run_id": run["run_id"],
            "continued_from_earlier_run": bool(earlier),
            "lens": as_lens, "risks": [str(r)[:240] for r in (content.get("risks") or [])[:3]],
        }
        _attach_trust(result, trust)
        if len(result["headline"]) > 90 and result["drivers"]:
            # The agent sometimes writes a full sentence; keep it as the summary.
            result["plain_summary"] = result["headline"]
            result["headline"] = result["drivers"][0]["title"]
        for x in result["exposed"]:
            if x.get("exchange") not in ("xnys", "xnas"):
                x["exchange"] = None
            if x.get("kind") != "company":
                x["ticker_is_us_stock"] = False

        if config.llm_live() and result["drivers"] and not as_lens:
            try:
                story = await gemini.narrate_why(item, content)
                result["plain_summary"], result["shopper_tip"] = story.plain_summary or result["plain_summary"], story.shopper_tip
                for loc in story.locations:
                    if 0 <= loc.index < len(result["drivers"]):
                        d = result["drivers"][loc.index]
                        if d.get("lat") is None or d.get("lng") is None:
                            d["lat"], d["lng"] = loc.lat, loc.lng
            except Exception as exc:
                obs.capture(exc)
        for d in result["drivers"]:
            lat, lng = d.get("lat"), d.get("lng")
            if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and -90 <= lat <= 90 and -180 <= lng <= 180):
                d["lat"] = d["lng"] = None
                spot = await world.geocode(d.get("location_name") or "")  # keyless fallback
                if spot:
                    d["lat"], d["lng"] = spot
        if result["drivers"]:
            cache_put(kind, key, result)
        return {**result, "home": home}


# ----------------------------------------------------------------- News Desk

NEWS_QUERIES = [
    "food prices grocery inflation commodities",
    "stock market economy interest rates today",
    "supply chain shipping tariffs disruption",
]


def _personalize(result: dict) -> dict:
    """Mark the headlines that mention this shopper's staples and list them first."""
    p = persona.load()
    out = []
    for h in result.get("headlines", []):
        text = " ".join([h.get("title") or "", h.get("description") or "", " ".join(h.get("groceries") or [])])
        out.append({**h, "in_your_basket": persona.touches(text, p)})
    out.sort(key=lambda h: not h["in_your_basket"])
    return {**result, "headlines": out, "watching": persona.watchwords(p)}


# Beats the live strip cycles through, one per refresh, after the opening three.
LIVE_BEATS = NEWS_QUERIES + [
    "semiconductor chip prices",
    "oil prices OPEC energy",
    "Federal Reserve inflation interest rates",
    "shipping ports container freight rates",
    "housing market mortgage rates",
    "company earnings results",
    "crop harvest weather drought",
]
NEWS_EVERY_S = 25  # the page asks every 30 s; one Nimble news search per refresh
NEWS_POOL = 24
_LIVE = {"pool": [], "at": 0.0, "turn": 0, "busy": False}


def _news_key(h: dict) -> str:
    return re.sub(r"\W+", "", (h.get("title") or "").lower())[:60]


async def _tag_headlines(headlines: list[dict]) -> list[str]:
    """Place each headline on the map and say in one line what it means for a household."""
    if not (config.llm_live() and headlines):
        return []
    try:
        tags = await gemini.tag_news(headlines)
        for t in tags.tags:
            if 0 <= t.index < len(headlines):
                headlines[t.index].update({
                    "place": t.place, "lat": t.lat if _on_earth(t.lat, t.lng) else None,
                    "lng": t.lng if _on_earth(t.lat, t.lng) else None,
                    "category": t.category, "wallet_impact": t.wallet_impact, "groceries": t.groceries[:3]})
        return []
    except Exception as exc:
        obs.capture(exc)
        return ["Headline tagging failed"]


async def news() -> dict:
    """The live news strip. The first call runs three searches at once. After that, each call that
    comes at least NEWS_EVERY_S later runs one more search on the next beat and puts anything new
    at the front, so the strip keeps moving without a burst of calls."""
    with obs.agent_span("News Desk", "Today's finance and supply headlines, placed on the map"):
        if not config.NIMBLE_LIVE:
            return _personalize({"sample": True, "headlines": demo.SAMPLE_NEWS})
        now = time.time()
        notes: list[str] = []
        if not _LIVE["pool"]:
            cached = cache_get("news", "today", 180)
            if cached:
                _LIVE.update(pool=cached["headlines"], at=now)
            elif not _LIVE["busy"]:
                _LIVE["busy"] = True
                try:
                    batches = await asyncio.gather(*(nimble.news_search(q, "day", 7, "News Desk") for q in NEWS_QUERIES),
                                                   return_exceptions=True)
                    seen, headlines = set(), []
                    for b in batches:
                        if isinstance(b, Exception):
                            notes.append(type(b).__name__)
                            obs.capture(b)
                            continue
                        for h in b:
                            if _news_key(h) not in seen:
                                seen.add(_news_key(h))
                                headlines.append(h)
                    headlines = headlines[:14]
                    notes += await _tag_headlines(headlines)
                    _LIVE.update(pool=headlines, at=time.time(), turn=len(NEWS_QUERIES))
                    if headlines:
                        cache_put("news", "today", {"sample": False, "headlines": headlines, "notes": notes})
                finally:
                    _LIVE["busy"] = False
        elif now - _LIVE["at"] >= NEWS_EVERY_S and not _LIVE["busy"]:
            _LIVE.update(busy=True, at=now)
            try:
                beat = LIVE_BEATS[_LIVE["turn"] % len(LIVE_BEATS)]
                _LIVE["turn"] += 1
                found = await nimble.news_search(beat, "day", 6, "News Desk")
                known = {_news_key(h) for h in _LIVE["pool"]}
                fresh = [h for h in found if _news_key(h) not in known][:4]
                if fresh:
                    notes += await _tag_headlines(fresh)
                    for h in fresh:
                        h["fresh_at"] = now
                    _LIVE["pool"] = (fresh + _LIVE["pool"])[:NEWS_POOL]
                    cache_put("news", "today", {"sample": False, "headlines": _LIVE["pool"], "notes": notes})
            except Exception as exc:  # the strip keeps what it has
                obs.capture(exc)
                notes.append(type(exc).__name__)
            finally:
                _LIVE["busy"] = False
        return _personalize({"sample": False, "headlines": list(_LIVE["pool"]), "notes": notes, "updated": _LIVE["at"]})


async def history(ticker: str) -> dict:
    """About a year of closing prices for the "past year" chart, through OpenBB or yfinance."""
    from . import market

    ticker = re.sub(r"[^A-Za-z.\-]", "", ticker).upper()[:8]
    with obs.agent_span("Market Data", f"Past year: {ticker}"):
        if config.FORCE_DEMO:
            rows = []
            for i in range(260):
                day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - (259 - i) * 86400 * 1.4))
                rows.append((day, 100 + i * 0.12 + 9 * math.sin(i / 17)))
            return {**market.summarize(ticker, rows, "sample data"), "sample": True}
        cached = cache_get("history", ticker, 6 * 3600)
        if cached:
            return cached
        with obs.tool_span(f"{market.library() or 'market'}.price_history", {"ticker": ticker}, "Market Data",
                           "Daily closing prices from an open-source market data library"):
            try:
                result = await market.history(ticker)
            except Exception as exc:
                obs.capture(exc)
                return {"available": True, "ticker": ticker, "points": [],
                        "note": f"Price history could not be loaded just now ({type(exc).__name__})."}
        if result.get("points"):
            cache_put("history", ticker, result)
        return result


# -------------------------------------------------------------- Signal Scout

SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "description": "3 to 5 alternative-data readings, each from a page you actually read",
            "items": {
                "type": "object",
                "properties": {
                    "signal": {"type": "string", "description": "What was measured, e.g. 'Open job postings', 'Shelf price changes', 'Store openings', 'App store rating'"},
                    "reading": {"type": "string", "description": "The observed value with its date or period"},
                    "direction": {"type": "string", "enum": ["rising", "falling", "steady", "unclear"]},
                    "why_it_matters": {"type": "string", "description": "One factual sentence. No predictions or advice."},
                },
                "required": ["signal", "reading", "direction", "why_it_matters"],
            },
        }
    },
    "required": ["signals"],
}
SIGNAL_SKILL = (
    "You collect alternative data about a public company from its own pages and other primary sources: "
    "careers pages and job counts, store or plant openings and closures, prices on its own site, "
    "app rankings and review volume, shipping and import records. Report only what a page shows, with "
    "its date. Do not forecast and do not recommend buying or selling anything."
)


async def signals(company: str, effort: str | None = None, on_status=None) -> dict:
    company = company.strip()[:60]
    with obs.agent_span("Signal Scout", f"Alternative-data readings for {company}"):
        if not config.NIMBLE_LIVE:
            return {**demo.SAMPLE_SIGNALS, "company": company}
        key = f"{company.lower()}|{effort or config.AGENT_EFFORT}"
        cached = cache_get("signals", key, 6 * 3600)
        if cached:
            return {**cached, "cached": True}
        task = f"Collect current alternative-data readings for {company}: hiring, footprint changes, own-site pricing, and customer demand signals."
        run = await nimble.agent_start(task, "nibble-signal-scout", SIGNAL_SCHEMA, SIGNAL_SKILL,
                                       "The company's own careers, investor and store pages; regulatory filings.", effort)
        out = await nimble.agent_wait(run["run_id"], run["agent_id"], "nibble-signal-scout", on_status=on_status)
        content = out["content"] if isinstance(out["content"], dict) else {}
        trust = out.get("trust") or {}
        result = {"sample": False, "company": company, "signals": (content.get("signals") or [])[:5],
                  "confidence": trust.get("confidence"), "seconds": out.get("seconds")}
        wrapper = {"drivers": result["signals"], "exposed": []}
        for claim in trust.get("claims") or []:
            claim["path"] = (claim.get("path") or "").replace("signals[", "drivers[")
        _attach_trust(wrapper, trust)
        if result["signals"]:
            cache_put("signals", key, result)
        return result


# --------------------------------------------------------------------- quote


async def quote(ticker: str, exchange: str | None = None, on_status=None) -> dict:
    with obs.agent_span("Signal Scout", f"Quote for {ticker}"):
        if not config.NIMBLE_LIVE:
            return {"sample": True, "ticker": ticker.upper(), "price": None,
                    "note": "Add a Nimble key to pull a live quote."}
        cached = cache_get("quote", ticker.lower(), 900)
        if cached:
            return cached
        result = {"sample": False, **(await nimble.stock_quote(ticker, exchange, "Signal Scout"))}
        cache_put("quote", ticker.lower(), result)
        return result


# -------------------------------------------------------------- News Tracer

TRACE_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "description": "The story's central factual claim in one sentence"},
        "verdict": {"type": "string", "enum": ["corroborated", "single_source", "disputed", "unclear"],
                    "description": "corroborated = at least two independent outlets or one primary source report the same facts"},
        "verdict_reason": {"type": "string", "description": "One sentence naming who else reports it or what is missing"},
        "primary_source": {"type": ["string", "null"],
                           "description": "The original data source or agency behind the facts, e.g. 'BLS CPI release', else null"},
        "chain": {
            "type": "array",
            "description": "3 to 5 links from the triggering event to household costs, in causal order",
            "items": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "enum": ["trigger", "supply", "market", "company", "shelf", "household"]},
                    "what": {"type": "string", "description": "One factual sentence"},
                    "place": {"type": "string",
                              "description": "A real city, region or country a map can find, e.g. 'Minas Gerais, Brazil' or 'Ottawa, Canada'. Never a market, currency or concept."},
                    "latitude": {"type": ["number", "null"]},
                    "longitude": {"type": ["number", "null"]},
                },
                "required": ["stage", "what", "place"],
            },
        },
        "affected_groceries": {"type": "array", "description": "Up to 5 grocery staples the story affects, one or two lowercase words each",
                               "items": {"type": "string"}},
        "what_to_watch": {"type": "string",
                          "description": "One sentence on the next scheduled data release or event sources point to, or an empty string"},
    },
    "required": ["claim", "verdict", "verdict_reason", "chain"],
}
TRACE_SKILL = (
    "You are a fact-checker and supply-chain analyst for a household-finance app. For a news story you do two "
    "things. First, verify: find the original source of the facts and whether independent outlets or primary "
    "sources report the same thing; say plainly if only one outlet reports it or if sources disagree. Second, "
    "trace: lay out the chain of cause and effect from the real-world trigger to a household's costs as 3 to 5 "
    "links, each a single factual sentence tied to a real place a map can find. Use only what sources state. "
    "Never predict prices and never give investment advice."
)
TRACE_SOURCES = (
    "Primary sources first: government statistics and agencies, central banks, company filings and statements. "
    "Then major wire services and national newspapers. Avoid blogs, aggregators and social posts."
)


def _site(url: str) -> str:
    m = re.match(r"https?://(?:www\d*\.)?([^/]+)", url or "")
    host = m.group(1).lower() if m else ""
    parts = host.split(".")
    return ".".join(parts[-3:] if len(parts) > 2 and len(parts[-1]) == 2 and len(parts[-2]) <= 3 else parts[-2:])


def source_check(original_url: str, sources: list[dict]) -> dict:
    """Counted in code from the agent's trust report: how many separate sites
    back the story, and how many of them are official."""
    origin = _site(original_url)
    sites: dict[str, dict] = {}
    for src in sources:
        site = _site(src.get("url") or "")
        if not site:
            continue
        entry = sites.setdefault(site, {"site": site, "category": src.get("category") or "other", "pages": [],
                                        "is_original": site == origin})
        if src.get("category") == "official":
            entry["category"] = "official"
        entry["pages"].append({"url": src.get("url"), "title": src.get("title")})
    rows = sorted(sites.values(), key=lambda e: (not e["is_original"], e["category"] != "official", e["site"]))
    others = [e for e in rows if not e["is_original"]]
    return {"sites": rows, "independent_sites": len(others),
            "official_sites": sum(1 for e in others if e["category"] == "official"),
            "original_was_read": any(e["is_original"] for e in rows)}


async def trace(headline: dict, zip_code: str, effort: str | None = None, on_status=None) -> dict:
    title = (headline.get("title") or "").strip()[:240]
    url = headline.get("url") or ""
    effort = effort or config.AGENT_EFFORT
    home = await world.locate_zip(zip_code)
    original = {"title": title, "url": url, "source": headline.get("source"), "published": headline.get("published")}
    with obs.agent_span("News Tracer", f"Verify and trace: {title}"):
        if not config.NIMBLE_LIVE:
            return {**demo.trace_for(headline, home), "original": original, "home": home,
                    "in_your_basket": persona.touches(title)}
        key = f"{url or title}|{effort}"
        cached = cache_get("trace", key, 6 * 3600)
        if cached:
            return {**cached, "home": home, "cached": True}
        task = (
            f"Check and trace this news story.\nHeadline: {title}\nURL: {url}\n"
            f"Summary: {(headline.get('description') or '')[:400]}\n\n"
            "1) Find whether other independent outlets or primary sources (agencies, data releases, company "
            "statements) report the same facts. 2) Trace the chain of cause and effect from the triggering "
            "real-world event to what a household pays, with a real place for each link."
        )
        run = await nimble.agent_start(task, "nibble-news-tracer", TRACE_SCHEMA, TRACE_SKILL, TRACE_SOURCES, effort)
        out = await nimble.agent_wait(run["run_id"], run["agent_id"], "nibble-news-tracer", on_status=on_status)
        content = out["content"] if isinstance(out["content"], dict) else {}
        trust = out.get("trust") or {}
        chain = [{**c, "lat": c.get("latitude"), "lng": c.get("longitude")} for c in (content.get("chain") or [])[:5]]
        sources = [{"url": s.get("url"), "title": s.get("title"), "type": s.get("type"),
                    "category": s.get("source_category")} for s in (trust.get("sources") or [])[:16]]
        result = {
            "sample": False, "original": original,
            "claim": content.get("claim") or title,
            "verdict": content.get("verdict") or "unclear",
            "verdict_reason": content.get("verdict_reason") or "",
            "primary_source": content.get("primary_source"),
            "chain": chain,
            "affected_groceries": [str(g)[:40] for g in (content.get("affected_groceries") or [])[:5]],
            "what_to_watch": content.get("what_to_watch") or "",
            "check": source_check(url, sources),
            "trust": {"confidence": trust.get("confidence"), "reasoning": trust.get("reasoning")},
            "seconds": out.get("seconds"), "effort": effort,
        }
        # Reuse the driver/exposed citation mapper: chain[i] claims attach to chain links.
        wrapper = {"drivers": result["chain"], "exposed": []}
        for claim in trust.get("claims") or []:
            claim["path"] = (claim.get("path") or "").replace("chain[", "drivers[")
        _attach_trust(wrapper, trust)
        for link in result["chain"]:
            lat, lng = link.get("lat"), link.get("lng")
            if not (isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and -90 <= lat <= 90 and -180 <= lng <= 180):
                link["lat"] = link["lng"] = None
                spot = await world.geocode(link.get("place") or "")
                if spot:
                    link["lat"], link["lng"] = spot
        result["in_your_basket"] = persona.touches(" ".join([title, *result["affected_groceries"]]))
        if result["chain"]:
            cache_put("trace", key, result)
        return {**result, "home": home}


# ----------------------------------------------------------------------- Ask


async def ask(question: str) -> dict:
    question = question.strip()[:300]
    with obs.agent_span("Ask", question):
        if not config.NIMBLE_LIVE:
            return {"sample": True, "answer": "Add a Nimble key to ask live questions.", "citations": []}
        return {"sample": False, **(await nimble.answer(question, "Ask"))}


async def stores(zip_code: str) -> dict:
    with obs.agent_span("Basket Scout", f"Grocery stores near {zip_code}"):
        if not config.NIMBLE_LIVE:
            return {"sample": True, "stores": []}
        cached = cache_get("stores", zip_code, 86400)
        if cached:
            return cached
        result = {"sample": False, "stores": await nimble.nearby_stores(zip_code, "Basket Scout")}
        if result["stores"]:
            cache_put("stores", zip_code, result)
        return result


# ------------------------------------------------------------------- Router

_ROUTE_HINTS = (
    ("season", r"\b(in season|seasonal|season)\b"),
    ("invest", r"\b(invest\w*|stocks?|shares|portfolio|etfs?|markets?|sectors?|exposed)\b"),
    ("learn", r"\b(teach|learn|flashcards?|explain|understand|deep.?dive|what is|what are|what does .* mean|how does|how do|how (is|are) .* prices?|how .* prices? (is|are))\b"),
    ("why", r"^\s*why\b"),
)


def _route_by_rules(text: str) -> dict:
    low = text.lower().strip()
    for intent, pattern in _ROUTE_HINTS:
        if re.search(pattern, low):
            topic = re.sub(r"^(why (is|are|did|does|do)|teach me( about)?|explain|what is|what are|what does)\s+", "", low).strip(" ?.")
            topic = re.sub(r"\s+mean for (investors|my portfolio|the market|markets|stocks)$", "", topic)
            topic = re.sub(r"\s+(so )?(expensive|cheap|pricey|going up|rising|costly)( now| lately| right now)?$", "", topic)
            return {"intent": intent, "items": [], "topic": "" if intent == "season" else topic}
    if "," in low or re.search(r"\b(i need|buy|get me|shopping list|pick up)\b", low):
        body = re.sub(r"^.*?\b(i need|buy|get me|pick up|shopping list:?)\b", "", low)
        items = [i.strip(" .") for i in re.split(r",|\band\b|;", body) if len(i.strip(" .")) > 1]
        if items:
            return {"intent": "basket", "items": items[:15], "topic": ""}
    return {"intent": "ask", "items": [], "topic": text.strip()}


async def route(text: str) -> dict:
    """Turn one typed sentence into an action. Gemini decides; plain rules are the fallback."""
    text = text.strip()[:400]
    with obs.agent_span("Router", text):
        if config.llm_live():
            try:
                r = await gemini.route(text)
                if r.intent in ("basket", "season", "why", "invest", "learn", "ask"):
                    return {"intent": r.intent, "items": [i.strip()[:80] for i in r.items if i.strip()][:15],
                            "topic": r.topic.strip()[:160], "by": "gemini"}
            except Exception as exc:
                obs.capture(exc)
        return {**_route_by_rules(text), "by": "rules"}


# ------------------------------------------------------------------- Season

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


async def season(zip_code: str) -> dict:
    """What is in season near the shopper this month: Nimble Answer finds it, Gemini turns it into list items."""
    home = await world.locate_zip(zip_code)
    month = time.localtime().tm_mon
    with obs.agent_span("Season Guide", f"In season near {home['label']} in {_MONTHS[month - 1]}"):
        if not config.NIMBLE_LIVE:
            return {**demo.season_for(month), "home": home, "month": _MONTHS[month - 1]}
        state = home["label"].split(",")[-1].strip()
        key = f"{state}|{month}"
        cached = cache_get("season", key, 7 * 86400)
        if cached:
            return {**cached, "home": home}
        reply = await nimble.answer(
            f"Which fruits and vegetables are in season in {state}, USA in {_MONTHS[month - 1]}?", "Season Guide")
        items: list[dict] = []
        if config.llm_live() and reply["answer"]:
            try:
                parsed = await gemini.season_items(reply["answer"], home["label"], _MONTHS[month - 1])
                items = [i.model_dump() for i in parsed.items[:8]]
            except Exception as exc:
                obs.capture(exc)
        result = {"sample": False, "month": _MONTHS[month - 1], "answer": reply["answer"],
                  "citations": reply["citations"], "items": items}
        if reply["answer"]:
            cache_put("season", key, result)
        return {**result, "home": home}


# -------------------------------------------------------------------- Learn


async def learn(topic: str = "") -> dict:
    """Finance flashcards built from today's headlines (Nimble) and written by Gemini."""
    topic = topic.strip()[:80]
    with obs.agent_span("Tutor", f"Flashcards{' on ' + topic if topic else ' from the news'}"):
        if not (config.NIMBLE_LIVE and config.llm_live()):
            note = "Sample cards. Live cards need both the Nimble and Gemini keys."
            return {**demo.SAMPLE_DECK, "topic": topic, "note": note}
        headlines = [h for h in (await news())["headlines"] if h.get("url")][:8]
        background, cites = "", []
        if topic:
            try:
                reply = await nimble.answer(f"Explain {topic} simply, for someone new to personal finance", "Tutor")
                background, cites = reply["answer"], reply["citations"]
            except Exception as exc:
                obs.capture(exc)
        deck = await gemini.flashcards(headlines, topic, background)
        cards = []
        for c in deck.cards[:6]:
            card = c.model_dump()
            i = card.pop("headline_index")
            src = headlines[i] if 0 <= i < len(headlines) else None
            card["source"] = {"title": src["title"], "url": src["url"], "outlet": src.get("source")} if src else None
            if len(card["quiz_options"]) != 3 or not 0 <= card["quiz_answer"] < 3:
                card["quiz_question"], card["quiz_options"], card["quiz_answer"] = "", [], 0
            cards.append(card)
        return {"sample": False, "topic": topic, "cards": cards, "background_sources": cites}


# ------------------------------------------------------------ Investing Coach

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {"type": "string", "description": "Two plain sentences: what happened, where and when"},
        "why_it_matters": {"type": "string", "description": "Two plain sentences on why a household or a beginner investor should care"},
        "how_it_spreads": {
            "type": "array",
            "description": "3 to 4 links from the event to prices and company results, in causal order",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "string", "description": "One plain sentence"},
                    "place": {"type": "string", "description": "A real city, region or country a map can find, never a market or concept"},
                    "latitude": {"type": ["number", "null"]},
                    "longitude": {"type": ["number", "null"]},
                },
                "required": ["step", "place"],
            },
        },
        "who_is_affected": {
            "type": "array",
            "description": "Up to 4 US-listed companies, funds, commodities or sectors tied to this story",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "ticker": {"type": ["string", "null"], "description": "US exchange ticker if listed on NYSE or Nasdaq, else null"},
                    "exchange": {"type": ["string", "null"], "description": "'xnys' for NYSE or 'xnas' for Nasdaq when a ticker is given, else null"},
                    "kind": {"type": "string", "enum": ["company", "fund", "commodity", "sector"]},
                    "effect": {"type": "string", "enum": ["costs rise", "costs fall", "sales rise", "sales fall", "mixed"]},
                    "because": {"type": "string", "description": "One factual sentence explaining the effect"},
                },
                "required": ["name", "kind", "effect", "because"],
            },
        },
        "what_is_expected": {
            "type": "array",
            "description": "Up to 3 forecasts that named sources have published about what happens next",
            "items": {
                "type": "object",
                "properties": {
                    "who": {"type": "string", "description": "The named forecaster: an agency, central bank, company or analyst firm"},
                    "expects": {"type": "string", "description": "What they forecast, with the numbers and dates they state"},
                    "by_when": {"type": ["string", "null"]},
                },
                "required": ["who", "expects"],
            },
        },
        "what_could_change_it": {"type": "array", "description": "Up to 3 things that would make those expectations wrong, one sentence each",
                                 "items": {"type": "string"}},
        "what_to_watch": {"type": "string", "description": "The next scheduled report or event to watch, with its date if known"},
        "concepts": {
            "type": "array",
            "description": "3 finance terms this story teaches",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string", "description": "Plain meaning in at most 25 words"}},
                "required": ["term", "meaning"],
            },
        },
    },
    "required": ["what_happened", "why_it_matters", "how_it_spreads", "who_is_affected", "what_is_expected"],
}
LESSON_SKILL = (
    "You are a patient investing teacher for a complete beginner. You turn one news story into a short lesson: "
    "what happened, why it matters, how the effect spreads step by step to prices and company results, who is "
    "affected and in which direction, and what named forecasters expect next. Use plain words and explain any "
    "finance term you use. Report expectations only as published by a named source, with their numbers and dates. "
    "You teach how to reason about news. You never say what to buy, sell or hold, and you never make your own "
    "price predictions."
)
LESSON_SOURCES = (
    "Government agencies and statistics (USDA, BLS, Federal Reserve, EIA), company filings and earnings calls, "
    "major wire services and national newspapers, university extension economists. Avoid blogs, aggregators and "
    "stock-tip sites."
)


def _on_earth(lat, lng) -> bool:
    ok = isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and -90 <= lat <= 90 and -180 <= lng <= 180
    return ok and not (lat == 0 and lng == 0)


def _cites_by_index(trust: dict, key: str) -> dict[int, dict]:
    """For a list field, gather each entry's citations and weakest confidence from the trust report."""
    out: dict[int, dict] = {}
    rank = {"low": 0, "medium": 1, "high": 2}
    for claim in trust.get("claims") or []:
        m = re.search(rf"{key}\[(\d+)\]", claim.get("path") or "")
        cites = [{"url": c.get("url"), "title": c.get("title"), "excerpts": (c.get("excerpts") or [])[:1],
                  "category": c.get("source_category")} for c in (claim.get("citations") or []) if c.get("url")]
        if not m or not cites:
            continue
        entry = out.setdefault(int(m.group(1)), {"citations": [], "confidence": None})
        have = {c["url"] for c in entry["citations"]}
        entry["citations"] += [c for c in cites if c["url"] not in have]
        conf = claim.get("confidence")
        if conf in rank and (entry["confidence"] not in rank or rank[conf] < rank[entry["confidence"]]):
            entry["confidence"] = conf
    return out


async def lesson(headline: dict, zip_code: str, effort: str | None = None, on_status=None) -> dict:
    """An investing lesson built from one story: what, why, how it spreads, who, and what is expected."""
    title = (headline.get("title") or "").strip()[:240]
    url = headline.get("url") or ""
    effort = effort or config.AGENT_EFFORT
    home = await world.locate_zip(zip_code)
    original = {"title": title, "url": url, "source": headline.get("source"), "published": headline.get("published")}
    with obs.agent_span("Investing Coach", f"Lesson from: {title}"):
        if not config.NIMBLE_LIVE:
            return {**demo.lesson_for(headline, home), "original": original, "home": home}
        key = f"{url or title}|{effort}"
        cached = cache_get("lesson", key, 6 * 3600)
        if cached:
            return {**cached, "home": home, "cached": True}
        about = f"Headline: {title}\nURL: {url}\nSummary: {(headline.get('description') or '')[:400]}" if url else f"Topic: {title}"
        task = (
            f"Build a beginner investing lesson from this news story.\n{about}\n\n"
            "Explain what happened, why it matters, how it spreads from the event to prices and company results, "
            "who is affected, what named sources expect to happen next, what could change that, and which finance "
            "terms it teaches."
        )
        run = await nimble.agent_start(task, "nibble-investing-coach", LESSON_SCHEMA, LESSON_SKILL, LESSON_SOURCES, effort)
        out = await nimble.agent_wait(run["run_id"], run["agent_id"], "nibble-investing-coach", on_status=on_status)
        c = out["content"] if isinstance(out["content"], dict) else {}
        trust = out.get("trust") or {}
        result = {
            "sample": False, "original": original,
            "what_happened": c.get("what_happened") or "", "why_it_matters": c.get("why_it_matters") or "",
            "chain": [{"what": s.get("step"), "place": s.get("place"), "lat": s.get("latitude"), "lng": s.get("longitude")}
                      for s in (c.get("how_it_spreads") or [])[:4] if s.get("step")],
            "affected": (c.get("who_is_affected") or [])[:4],
            "expected": (c.get("what_is_expected") or [])[:4],
            "could_change": [str(x)[:260] for x in (c.get("what_could_change_it") or [])[:3] if _real(x)],
            "what_to_watch": c.get("what_to_watch") or "",
            "concepts": (c.get("concepts") or [])[:4],
            "trust": {"confidence": trust.get("confidence"), "sources": len(trust.get("sources") or [])},
            "seconds": out.get("seconds"), "effort": effort,
        }
        for field, key_name in (("chain", "how_it_spreads"), ("affected", "who_is_affected"), ("expected", "what_is_expected")):
            for i, extra in _cites_by_index(trust, key_name).items():
                if i < len(result[field]):
                    result[field][i].update(extra)
        for x in result["affected"]:
            if x.get("exchange") not in ("xnys", "xnas"):
                x["exchange"] = None
        result["quiz"] = None
        extras = None
        if config.llm_live() and result["chain"]:
            # The agent writes the lesson; a model adds map places, words to know and a check question.
            try:
                extras = await gemini.lesson_extras(result)
            except Exception as exc:  # the lesson still stands without them
                obs.capture(exc)
        if extras:
            if len(result["concepts"]) < 2:
                result["concepts"] = [{"term": t.term, "meaning": t.meaning} for t in extras.concepts[:3]]
            if len(extras.quiz_options) >= 2 and 0 <= extras.quiz_answer < len(extras.quiz_options):
                result["quiz"] = {"question": extras.quiz_question, "options": extras.quiz_options[:3],
                                  "answer": extras.quiz_answer, "why": extras.quiz_why}
        named = {p.step_index: p for p in (extras.places if extras else [])}
        for i, link in enumerate(result["chain"]):
            lat, lng = link.get("lat"), link.get("lng")
            if _on_earth(lat, lng):
                continue
            link["lat"] = link["lng"] = None
            guess = named.get(i)
            # Prefer a looked-up position for the named place over coordinates a model recalled.
            for name in ([guess.place] if guess else []) + [link.get("place") or ""]:
                spot = await world.geocode(name) if name and len(name) < 60 else None
                if spot:
                    link["lat"], link["lng"] = spot
                    link["place"] = name
                    break
            else:
                if guess and _on_earth(guess.latitude, guess.longitude):
                    link["lat"], link["lng"], link["place"] = guess.latitude, guess.longitude, guess.place
        if result["what_happened"]:
            cache_put("lesson", key, result)
        return {**result, "home": home}


# ------------------------------------------------------------- Topic Guide

_STR = {"type": "string"}
# Two smaller schemas, run side by side. In testing, one large schema at low effort came back with
# required fields missing or empty; each half on its own came back complete.
GUIDE_BASICS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "A short title for the guide, at most 8 words"},
        "in_one_line": {"type": "string", "description": "The whole story in one plain sentence"},
        "what_it_is": {"type": "string", "description": "3 to 4 plain sentences explaining the thing itself to someone who has never heard of it"},
        "background": {"type": "array", "description": "4 to 6 dated events that led to the situation today, oldest first",
                       "items": {"type": "object", "properties": {
                           "when": {"type": "string", "description": "Year, or month and year"},
                           "what": {"type": "string", "description": "One plain sentence"}}, "required": ["when", "what"]}},
        "how_it_works": {"type": "array", "description": "3 to 5 stages of the supply chain or mechanism, in order",
                         "items": {"type": "object", "properties": {
                             "stage": {"type": "string", "description": "Name of this stage, 2 to 4 words"},
                             "who": {"type": "string", "description": "The main companies or countries at this stage"},
                             "place": {"type": "string", "description": "One real city, region or country a map can find, e.g. 'Hsinchu, Taiwan'"},
                             "detail": {"type": "string", "description": "One or two plain sentences on what happens here and why it matters for price"}},
                             "required": ["stage", "who", "place", "detail"]}},
        "happening_now": {"type": "string", "description": "3 sentences on what is happening right now, with numbers and dates"},
    },
    "required": ["title", "in_one_line", "what_it_is", "background", "how_it_works", "happening_now"],
}
_NO_ADVICE = ("Use plain words and explain every finance term. Report forecasts only as published by a named source, with their "
              "numbers and dates. You teach how to understand a topic. You never say what to buy, sell or hold, and you never "
              "make your own price predictions.")
GUIDE_BASICS_SKILL = (
    "You are a patient teacher writing a beginner's guide to one money topic from current world news. Explain from zero: "
    "what the thing is, the history that led here, how it works step by step and where on Earth each step happens, and "
    "what is happening now with numbers and dates. " + _NO_ADVICE)
GUIDE_SOURCES = (
    "Government agencies and statistics, industry associations, company filings and earnings calls, major wire "
    "services and national newspapers. Avoid blogs, aggregators and stock-tip sites."
)
_VAGUE_PLACE = re.compile(r"\b(global(ly)?|worldwide|mostly|mainly|primarily|heavily|concentrated|in|the|and other .*|various .*)\b", re.I)
_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


async def _guide_part(task: str, agent_name: str, schema: dict, skill: str, effort: str, on_status=None) -> dict:
    """One research run, tried a second time if Nimble reports the run failed."""
    last: Exception | None = None
    for _ in (1, 2):
        try:
            run = await nimble.agent_start(task, agent_name, schema, skill, GUIDE_SOURCES, effort)
            out = await nimble.agent_wait(run["run_id"], run["agent_id"], agent_name, on_status=on_status)
            if isinstance(out.get("content"), dict):
                return out
            last = RuntimeError("the agent did not return structured output")
        except Exception as exc:
            last = exc
    assert last is not None
    raise last


_JUNK = re.compile(r"not the final answer|mid-stream|invalid text|^(null|none|n/?a|undefined)$", re.I)


def _real(text) -> bool:
    """False for the filler an agent sometimes leaves in a field it could not fill."""
    return isinstance(text, str) and len(text.strip()) > 2 and not _JUNK.search(text.strip())


# Wire services, business press, regulators and statistics agencies. Two of the three lookups are
# limited to these with Nimble's include_domains, the pattern Nimble's finance cookbook uses.
TRUSTED_DOMAINS = ["reuters.com", "apnews.com", "cnbc.com", "bloomberg.com", "ft.com", "wsj.com", "sec.gov",
                   "federalreserve.gov", "bls.gov", "bea.gov", "eia.gov", "usda.gov", "imf.org", "worldbank.org", "oecd.org"]
# (question, limit to trusted domains?)
_GUIDE_QUESTIONS = (
    ("What are the main factors moving prices for {topic}, and which companies are most involved?", True),
    ("What do analysts, agencies or companies forecast for {topic} over the next year? Name the sources, numbers and dates.", True),
    ("What should a beginner understand before investing in {topic}: how companies make money, the cycle, and the main risks?", False),
)


async def guide(topic: str, zip_code: str, effort: str | None = None, on_status=None) -> dict:
    """A deep-dive guide to one topic.

    The basics (what it is, how we got here, how it works and where, what is happening now) come from a
    Nimble Web Search Agent with per-field citations. The investing view (what moves the price, the main
    companies, what to understand, what named sources expect) is written by the model from three Nimble
    Answer lookups run at the same time. A low-effort agent run with the whole schema, and one with only
    the investing half, both came back with required fields empty or filled with filler, so that half is
    not asked of the agent."""
    topic = topic.strip()[:200]
    effort = effort or config.AGENT_EFFORT
    home = await world.locate_zip(zip_code)
    with obs.agent_span("Topic Guide", f"Deep dive: {topic}"):
        if not config.NIMBLE_LIVE:
            return {**demo.guide_for(topic), "home": home}
        key = f"{topic.lower()}|{effort}"
        cached = cache_get("guide", key, 6 * 3600)
        if cached:
            return {**cached, "home": home, "cached": True}
        started = time.time()

        async def lookup(question: str, trusted: bool) -> dict:
            empty = {"question": question, "answer": "", "citations": [], "snippets": [], "trusted_only": False}
            for domains in ([TRUSTED_DOMAINS, None] if trusted else [None]):
                try:
                    found = await nimble.answer(question, "Topic Guide", domains, "standard" if domains else None)
                except Exception as exc:
                    obs.capture(exc)
                    continue
                if _real(found.get("answer")):
                    return {"question": question, **found, "trusted_only": bool(domains)}
            return empty

        basics, *research = await asyncio.gather(
            _guide_part(
                f"Explain this topic from zero for a beginner learning to invest.\nTopic: {topic}\n\n"
                "Cover what it is, the dated background that led here, how the supply chain works stage by stage and "
                "where each stage happens, and what is happening right now.",
                "nibble-topic-basics", GUIDE_BASICS_SCHEMA, GUIDE_BASICS_SKILL, effort, on_status),
            *[lookup(q.format(topic=topic), trusted) for q, trusted in _GUIDE_QUESTIONS])
        research = [r for r in research if _real(r.get("answer"))]
        c = basics["content"]
        trust = basics.get("trust") or {}

        def rows(name: str, limit: int) -> list[dict]:
            return [dict(r) for r in (c.get(name) or [])[:limit] if isinstance(r, dict)]

        seen: set[str] = set()
        sources = []
        for src in [*(trust.get("sources") or []), *[cite for r in research for cite in r["citations"]]]:
            if isinstance(src, dict) and src.get("url") and src["url"] not in seen:
                seen.add(src["url"])
                sources.append({"url": src["url"], "title": src.get("title") or src["url"]})
        result = {
            "sample": False, "topic": topic, "title": c.get("title") if _real(c.get("title")) else topic,
            "in_one_line": c.get("in_one_line") if _real(c.get("in_one_line")) else "",
            "what_it_is": c.get("what_it_is") if _real(c.get("what_it_is")) else "",
            "background": [b for b in rows("background", 6) if _real(b.get("what"))],
            "stages": [st for st in rows("how_it_works", 5) if _real(st.get("detail"))],
            "happening_now": c.get("happening_now") if _real(c.get("happening_now")) else "",
            "things_to_know": [], "price_drivers": [], "key_players": [], "numbers": [], "expected": [], "mistakes": [],
            "research": [{"question": r["question"], "answer": r["answer"], "citations": r["citations"],
                          "trusted_only": r["trusted_only"]} for r in research],
            "written_by": None, "quiz": [], "notes": [],
            "sources": sources[:14],
            "trust": {"confidence": trust.get("confidence"), "sources": len(sources)},
            "seconds": 0, "effort": effort,
        }
        for field, key_name in (("background", "background"), ("stages", "how_it_works")):
            for i, extra in _cites_by_index(trust, key_name).items():
                if i < len(result[field]):
                    result[field][i].update(extra)
        for st in result["stages"]:
            # "Global, mostly United States" -> "United States"; then look the place up.
            name = re.sub(r"\s+", " ", _VAGUE_PLACE.sub(" ", st.get("place") or "")).strip(" ,;.")
            spot = await world.geocode(name) if 2 < len(name) < 80 else None
            st["lat"], st["lng"] = spot if spot else (None, None)

        if config.llm_live() and result["what_it_is"]:
            try:
                view = await gemini.guide_investing(topic, result, research)
                result["things_to_know"] = [x[:360] for x in view.things_to_know[:6] if _real(x)]
                result["price_drivers"] = [d.model_dump() for d in view.price_drivers[:5] if _real(d.driver) and _real(d.explanation)]
                result["key_players"] = [{"name": k.name, "role": k.role, "exchange": None,
                                          "ticker": k.ticker.strip().upper() if _TICKER.match(k.ticker.strip().upper()) else None}
                                         for k in view.key_players[:6] if _real(k.name)]
                result["numbers"] = [x.model_dump() for x in view.numbers_to_know[:5] if _real(x.value) and _real(x.figure)]
                result["expected"] = [{"who": e.who, "expects": e.expects, "by_when": e.by_when or None}
                                      for e in view.what_is_expected[:3] if _real(e.who) and _real(e.expects)]
                result["mistakes"] = [x[:360] for x in view.common_mistakes[:4] if _real(x)]
                result["written_by"] = "model"
            except Exception as exc:
                obs.capture(exc)
                result["notes"].append("The investing view could not be written just now, so the research notes are shown as they came back.")
            try:
                quiz = await gemini.guide_quiz(result)
                result["quiz"] = [q.model_dump() for q in quiz.questions[:3]
                                  if len(q.options) >= 2 and 0 <= q.answer < len(q.options)]
            except Exception as exc:  # the guide stands without a quiz
                obs.capture(exc)
        result["seconds"] = round(time.time() - started)
        if result["what_it_is"] and result["things_to_know"]:
            cache_put("guide", key, result)
        return {**result, "home": home}
