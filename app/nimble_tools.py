"""Every Nimble call Nibble makes, each wrapped in a Sentry tool span.

Nimble products used:
  Extract Templates  walmart_serp, target_serp, amazon_serp (keyword + ZIP ->
                     live shelf prices), google_maps_search (nearby stores),
                     morningstar_stock_quote (a quote for an exposed company)
  Search             focus="news" (the wire)
  Web Search Agents  persistent named agents with an output schema and a
                     per-claim trust report (Price Detective, Signal Scout)

Template names, parameters and output fields below were checked against the
live template definitions on 2026-10-11.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any

from nimble_python import AsyncNimble

from . import config
from .obs import set_result, tool_span

_client: AsyncNimble | None = None
_template_meta: dict[str, dict] = {}

QUERY_PARAM_NAMES = ("keyword", "query", "search_term", "search_query", "q", "search", "term")
ZIP_PARAM_NAMES = ("zip_code", "zipcode", "zip", "postal_code")


def client() -> AsyncNimble:
    global _client
    if _client is None:
        _client = AsyncNimble(api_key=config.NIMBLE_API_KEY, max_retries=1, timeout=90)
    return _client


def _plain(obj: Any) -> Any:
    """SDK model -> plain dicts and lists."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


# ---------------------------------------------------------------- templates


async def template_meta(name: str) -> dict:
    """Input schema for a template, fetched once so params are built from the
    template's own definition instead of guessed."""
    if name in _template_meta:
        return _template_meta[name]
    meta: dict = {"name": name, "properties": {}, "required": [], "domain": ""}
    try:
        with tool_span("nimble.templates.get", {"template": name}, description="Read a template's input schema") as span:
            res = _plain(await client().extract.templates.get(name))
            pv = res.get("published_version") or {}
            schema = pv.get("input_schema") or {}
            meta["properties"] = schema.get("properties") or {}
            meta["required"] = schema.get("required") or []
            meta["domain"] = (pv.get("metadata") or {}).get("domain") or ""
            set_result(span, {"params": list(meta["properties"]), "required": meta["required"]})
    except Exception:
        pass  # fall back to conventional parameter names below
    _template_meta[name] = meta
    return meta


def _pick_param(meta: dict, candidates: tuple[str, ...], default: str | None) -> str | None:
    props = meta.get("properties") or {}
    for name in candidates:
        if name in props:
            return name
    if not props:
        return default
    return None


async def run_template(template: str, params: dict, localization: bool = False, agent: str = "",
                       timeout_s: int | None = None) -> Any:
    """Run an Extract Template and return its parsed output.

    Retail search templates render a full page and take roughly 15-40 s, which
    is longer than the realtime endpoint reliably waits. The default mode
    therefore submits an async task and polls it. Set NIBBLE_TEMPLATE_MODE=sync
    to use the realtime endpoint instead.
    """
    timeout_s = timeout_s or config.TEMPLATE_TIMEOUT_S
    args = {"template": template, "params": params, "localization": localization, "mode": config.TEMPLATE_MODE}
    with tool_span(f"nimble.template.{template}", args, agent, "Run a Nimble Extract Template") as span:
        kwargs: dict = {"template": template, "params": params}
        if localization:
            kwargs["localization"] = True
        if config.TEMPLATE_MODE == "sync":
            res = _plain(await client().extract.templates.run(**kwargs))
        else:
            started = _plain(await client().extract.templates.async_(**kwargs))
            task_id = (started.get("task") or {}).get("id")
            if not task_id:
                raise RuntimeError(f"Nimble did not return a task id for {template}")
            waited, delay = 0.0, 3.0
            while True:
                await asyncio.sleep(delay)
                waited += delay
                task = (_plain(await client().tasks.get(task_id)).get("task")) or {}
                state = task.get("state")
                if state == "success":
                    break
                if state == "error":
                    raise RuntimeError(f"{template} failed: {task.get('error') or task.get('error_type') or 'error'}")
                if waited >= timeout_s:
                    raise TimeoutError(f"{template} still {state} after {timeout_s}s")
                delay = 2.0
            res = _plain(await client().tasks.results(task_id))
        parsing = _find_parsing(res)
        seen = len(find_offers(parsing, 100))
        LAST_RAW[template] = {
            "status": res.get("status") if isinstance(res, dict) else None,
            "status_code": res.get("status_code") if isinstance(res, dict) else None,
            "top_keys": sorted(res)[:12] if isinstance(res, dict) else type(res).__name__,
            "data_keys": sorted(res["data"])[:12] if isinstance(res, dict) and isinstance(res.get("data"), dict) else None,
            "parsing": f"{type(parsing).__name__}, {len(parsing) if isinstance(parsing, (list, dict)) else 0} entries",
            "first_row_keys": sorted(parsing[0])[:14] if isinstance(parsing, list) and parsing and isinstance(parsing[0], dict) else None,
            "warnings": res.get("warnings") if isinstance(res, dict) else None,
        }
        set_result(span, {"status": LAST_RAW[template]["status"], "offers_seen": seen})
        return parsing


LAST_RAW: dict[str, dict] = {}  # shape of each template's latest result, for scripts/preflight.py


def _find_parsing(res: Any, depth: int = 0) -> Any:
    """The parsed output, wherever the response carries it."""
    if not isinstance(res, dict) or depth > 3:
        return None
    data = res.get("data")
    if isinstance(data, dict) and data.get("parsing") not in (None, {}, []):
        return data["parsing"]
    if res.get("parsing") not in (None, {}, []):
        return res["parsing"]
    for key in ("result", "results", "output", "response"):
        inner = res.get(key)
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            inner = inner[0]
        found = _find_parsing(inner, depth + 1) if isinstance(inner, dict) else None
        if found is not None:
            return found
    return data.get("parsing") if isinstance(data, dict) else None


_offer_cache: dict[tuple, tuple[float, list[dict]]] = {}
OFFER_DIR = config.DATA_DIR / "offers"  # survives a restart, so a warmed-up demo stays fast


def _offer_file(key: tuple):
    return OFFER_DIR / (hashlib.sha1("|".join(key).encode()).hexdigest()[:20] + ".json")


def _offers_from_disk(key: tuple) -> list[dict] | None:
    try:
        path = _offer_file(key)
        if time.time() - path.stat().st_mtime < config.OFFER_CACHE_S:
            return json.loads(path.read_text())
    except Exception:
        pass
    return None


async def store_search(template: str, query: str, zip_code: str = "", agent: str = "") -> list[dict]:
    """Search one retailer through its template and return normalized offers,
    best candidates first (in stock, sold by the retailer, not sponsored)."""
    key = (template, query.lower(), zip_code)
    hit = _offer_cache.get(key)
    if hit and time.time() - hit[0] < config.OFFER_CACHE_S:
        return [dict(o) for o in hit[1]]
    saved = _offers_from_disk(key)
    if saved:
        _offer_cache[key] = (time.time(), saved)
        return [dict(o) for o in saved]
    meta = await template_meta(template)
    q_param = _pick_param(meta, QUERY_PARAM_NAMES, "keyword")
    if q_param is None:
        required = [p for p in meta.get("required", []) if p not in ZIP_PARAM_NAMES]
        q_param = required[0] if required else "keyword"
    params: dict = {q_param: query}
    z_param = _pick_param(meta, ZIP_PARAM_NAMES, None)
    if zip_code and z_param:
        params[z_param] = zip_code
    localized = bool(zip_code and z_param and config.LOCALIZE)
    try:
        parsing = await run_template(template, params, localized, agent)
    except TimeoutError:
        raise
    except Exception:
        if not localized:
            raise
        # Retry once without the store switch if the localized run is rejected.
        localized = False
        parsing = await run_template(template, params, False, agent)
    store = store_label(template, meta.get("domain", ""))
    offers = find_offers(parsing, 60)
    if not offers:
        # Retail sites sometimes serve an empty or blocked page; one more try usually lands.
        parsing = await run_template(template, params, False, agent)
        offers = find_offers(parsing, 60)
    offers = [o for o in offers if not o.get("out_of_stock")]
    offers.sort(key=lambda o: (o["sponsored"], not _first_party(o, store), o.get("position") or 999))
    seen, unique = set(), []
    for o in offers:
        ident = o.get("url") or (o["name"].lower(), o["price"])  # same name, different pack = different product
        if ident in seen:
            continue
        seen.add(ident)
        o["store"] = store
        o["source"] = template
        if o.get("url", "") and o["url"].startswith("/") and meta.get("domain"):
            o["url"] = "https://" + re.sub(r"^https?://", "", meta["domain"]).strip("/") + o["url"]
        # A ZIP may be requested, but the template reports which store it actually priced.
        o["localized"] = bool(zip_code and o.get("priced_for") == zip_code)
        unique.append(o)
    unique = unique[:10]
    if unique:
        _offer_cache[key] = (time.time(), [dict(o) for o in unique])
        try:
            OFFER_DIR.mkdir(exist_ok=True)
            _offer_file(key).write_text(json.dumps(unique))
        except Exception:
            pass
    return unique


def _first_party(offer: dict, store: str) -> bool:
    seller = (offer.get("seller") or "").lower()
    return not seller or store.lower().split()[0] in seller


def store_label(template: str, domain: str = "") -> str:
    base = (domain or template).lower()
    for key, label in (
        ("walmart", "Walmart"), ("amazon", "Amazon"), ("target", "Target"), ("kroger", "Kroger"),
        ("costco", "Costco"), ("instacart", "Instacart"), ("bestbuy", "Best Buy"), ("best_buy", "Best Buy"),
        ("homedepot", "Home Depot"), ("home_depot", "Home Depot"), ("aldi", "Aldi"), ("wholefoods", "Whole Foods"),
        ("samsclub", "Sam's Club"), ("safeway", "Safeway"), ("albertsons", "Albertsons"), ("publix", "Publix"),
        ("heb.com", "H-E-B"), ("heb_", "H-E-B"), ("sams_club", "Sam's Club"), ("traderjoes", "Trader Joe's"), ("shoprite", "ShopRite"), ("wegmans", "Wegmans"),
    ):
        if key in base:
            return label
    if "." in base:  # a bare domain such as "shop.example.com"
        parts = base.split(".")
        return (parts[-2] if len(parts) >= 2 else parts[0]).title()
    return template.split("_")[0].title()


# ------------------------------------------------- offer normalization (no LLM)

_NAME_KEYS = ("full_display_name", "product_name", "product_title", "name", "title", "item_name")
_PRICE_KEYS = ("product_price", "price", "web_price", "current_price", "final_price", "sale_price", "offer_price", "price_value", "min_price")
_URL_KEYS = ("product_url", "url", "link", "product_link", "canonical_url")
_SIZE_KEYS = ("product_price_per_unit", "price_per_size", "price_per_unit", "unit_price", "size", "package_size", "weight")
_IMG_KEYS = ("product_image", "image_url", "image", "thumbnail", "img")
_RATING_KEYS = ("product_rating", "rating", "average_of_reviews", "average_rating", "stars")
_SELLER_KEYS = ("product_seller", "seller", "merchant", "sold_by")


def to_price(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if 0 < float(value) < 10000 else None
    if isinstance(value, dict):
        for k in ("value", "amount", "price", "current", "min"):
            if k in value:
                return to_price(value[k])
        return None
    if isinstance(value, str):
        m = re.search(r"(\d{1,4}(?:,\d{3})*(?:\.\d{1,2})?)", value.replace(" ", " "))
        if m:
            try:
                p = float(m.group(1).replace(",", ""))
                return p if 0 < p < 10000 else None
            except ValueError:
                return None
    return None


_UNIT_WORDS = {"each": "count", "ea": "count", "count": "count", "ct": "count", "oz": "oz", "ounce": "oz",
               "fl oz": "fl oz", "floz": "fl oz", "lb": "lb", "pound": "lb", "gal": "gal", "gallon": "gal",
               "l": "l", "liter": "l", "qt": "qt", "pt": "pt", "g": "g", "kg": "kg", "ml": "ml"}


def parse_shelf_unit(text: Any) -> tuple[float | None, str | None]:
    """The retailer's own unit price, e.g. '13.9 \u00a2/count', '$0.15/each', '$2.98/lb'
    -> (dollars per unit, unit)."""
    if not isinstance(text, str):
        return None, None
    m = re.search(r"(\$)?\s*(\d+(?:\.\d+)?)\s*(\u00a2|c)?\s*/\s*([a-z .]+)", text.lower())
    if not m:
        return None, None
    value = float(m.group(2))
    if m.group(3) and not m.group(1):
        value /= 100
    unit = _UNIT_WORDS.get(m.group(4).strip().rstrip("."))
    return (value, unit) if unit and value > 0 else (None, None)


def _first(d: dict, keys: tuple[str, ...]) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _as_offer(d: dict) -> dict | None:
    name = _first(d, _NAME_KEYS)
    price = None
    for k in _PRICE_KEYS:
        price = to_price(d.get(k))
        if price is not None:
            break
    if not isinstance(name, str) or price is None:
        return None
    url = _first(d, _URL_KEYS)
    img = _first(d, _IMG_KEYS)
    size = _first(d, _SIZE_KEYS)
    if isinstance(d.get("unit_price"), (int, float)) and d.get("unit_price_measure"):
        size = f"${d['unit_price']}/{d['unit_price_measure']}"  # H-E-B reports the number and the unit separately
    rating = _first(d, _RATING_KEYS)
    seller = _first(d, _SELLER_KEYS)
    return {
        "name": name.strip()[:160],
        "price": round(price, 2),
        "url": url if isinstance(url, str) else None,
        "image": img if isinstance(img, str) else None,
        "size_hint": str(size)[:60] if size is not None else None,
        "rating": rating if isinstance(rating, (int, float)) else None,
        "sponsored": bool(d.get("sponsored") or d.get("is_sponsored") or d.get("is_ad") or d.get("featured")),
        "seller": seller if isinstance(seller, str) else None,
        "out_of_stock": bool(d.get("product_out_of_stock") or d.get("is_out_of_stock") or d.get("is_not_available")
                             or str(d.get("availability") or "").upper() in ("OUT_OF_STOCK", "UNAVAILABLE")
                             or str(d.get("in_stock") or "IN_STOCK").upper() not in ("IN_STOCK", "TRUE", "LOW_STOCK")),
        "position": d.get("position") if isinstance(d.get("position"), (int, float)) else None,
        "priced_for": str(d.get("agent_zip_code") or d.get("store_location")
                          or (f"store {int(d['store_id'])}" if isinstance(d.get("store_id"), (int, float)) else ""))[:24] or None,
        **_pack_from_shelf(price, size),
    }


def _pack_from_shelf(price: float, shelf: Any) -> dict:
    """Pack size implied by the shelf unit price: $2.65 at $0.15/each is an 18-count."""
    per, unit = parse_shelf_unit(shelf)
    if not per or not unit:
        return {"shelf_unit_price": None, "shelf_unit": None, "pack_quantity": None, "pack_unit": None}
    qty = price / per
    qty = float(round(qty)) if unit == "count" else round(qty, 1)
    if unit == "count" and qty <= 1:
        # "$6.88/ea" on a dozen means per carton, not per egg; read the size from the name instead.
        return {"shelf_unit_price": None, "shelf_unit": None, "pack_quantity": None, "pack_unit": None}
    return {"shelf_unit_price": per, "shelf_unit": unit, "pack_quantity": qty if qty > 0 else None, "pack_unit": unit}


def find_offers(parsing: Any, limit: int = 12) -> list[dict]:
    """Find the product list inside a template's parsed output.

    Template output shapes differ (a bare list, {"entities": {...}}, nested
    result arrays), so walk the structure and keep the list that yields the
    most name+price records.
    """
    best: list[dict] = []

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal best
        if depth > 6:
            return
        if isinstance(node, list):
            offers = [o for o in (_as_offer(x) for x in node if isinstance(x, dict)) if o]
            if len(offers) > len(best):
                best = offers
            for x in node[:40]:
                if isinstance(x, (dict, list)):
                    walk(x, depth + 1)
        elif isinstance(node, dict):
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)

    walk(parsing)
    return best[:limit]


# --------------------------------------------------------------------- search


async def shopping_search(query: str, agent: str = "", max_results: int = 8) -> list[dict]:
    """Nimble Search with the shopping focus. Lite results carry product links
    (routed through retailer templates) but no prices, so this is used to find
    where an item is sold, not what it costs."""
    args = {"query": query, "focus": "shopping", "search_depth": "lite", "max_results": max_results}
    with tool_span("nimble.search.shopping", args, agent, "Nimble Search, shopping focus") as span:
        res = _plain(await client().search(country="US", **args))
        out = [{"title": r.get("title"), "url": r.get("url"),
                "via": (r.get("metadata") or {}).get("agent_name"), "store": store_label(_domain(r.get("url") or ""))}
               for r in res.get("results") or [] if r.get("title") and r.get("url")]
        set_result(span, {"results": len(out)})
        return out


def _price_in_text(text: str) -> float | None:
    m = re.search(r"\$\s?(\d{1,4}(?:\.\d{2})?)", text)
    return float(m.group(1)) if m else None


def _domain(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


async def news_search(query: str, time_range: str = "day", max_results: int = 8, agent: str = "") -> list[dict]:
    args = {"query": query, "focus": "news", "search_depth": "lite", "time_range": time_range, "max_results": max_results}
    with tool_span("nimble.search.news", args, agent, "Nimble Search, news focus") as span:
        res = _plain(await client().search(**args))
        out = []
        for r in res.get("results") or []:
            if not r.get("title") or not r.get("url"):
                continue
            extra = r.get("additional_data") or {}
            out.append({
                "title": r["title"].strip(),
                "url": r["url"],
                "description": (r.get("description") or "").strip()[:400],
                "source": extra.get("source") or _domain(r["url"]),
                "published": extra.get("publish_date") or extra.get("date") or extra.get("published_at"),
            })
        set_result(span, {"results": len(out)})
        return out


def _num(value: Any) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value)) if value is not None else None
    return float(m.group(0)) if m else None


async def nearby_stores(zip_code: str, agent: str = "") -> list[dict]:
    """Grocery stores near a ZIP via the Google Maps template (about 1-2 s).
    Rows live at parsing.entities.SearchResult; numbers arrive as strings."""
    parsing = await run_template("google_maps_search", {"query": f"grocery stores near {zip_code}"}, False, agent, 40)
    entities = (parsing or {}).get("entities") if isinstance(parsing, dict) else None
    candidates = (entities or {}).get("SearchResult") if isinstance(entities, dict) else None
    if not isinstance(candidates, list):
        candidates = _largest_list(parsing)
    rows: list[dict] = []
    for p in candidates or []:
        if not isinstance(p, dict) or not p.get("title") or p.get("sponsored"):
            continue
        if str(p.get("business_status") or "OPERATIONAL").upper() != "OPERATIONAL":
            continue
        rows.append({
            "name": str(p["title"])[:80],
            "rating": _num(p.get("rating")),
            "reviews": int(_num(p.get("number_of_reviews")) or 0) or None,
            "address": p.get("address") or p.get("street_address"),
            "lat": _num(p.get("latitude")),
            "lng": _num(p.get("longitude")),
            "url": p.get("place_url") if isinstance(p.get("place_url"), str) else None,
        })
    return rows[:8]


async def answer(question: str, agent: str = "", domains: list[str] | None = None, depth: str | None = None) -> dict:
    """Nimble Answer: one synthesized reply with numbered citations (a few seconds).

    `domains` limits the search to those sites (Nimble's include_domains), and depth="standard"
    returns about 2,000 characters of page text per result instead of a short description."""
    args: dict = {"query": question, "include_answer": True, "max_results": 4 if domains else 5}
    if domains:
        args["include_domains"] = domains
    if depth:
        args["search_depth"] = depth
    with tool_span("nimble.search.answer", args, agent, "Nimble Search with a synthesized answer") as span:
        res = _plain(await client().search(**args))
        results = res.get("results") or []
        cites = []
        for c in res.get("answer_citations") or []:
            i = c.get("result_index")
            if isinstance(i, int) and 0 <= i < len(results) and results[i].get("url"):
                cites.append({"marker": c.get("marker"), "title": results[i].get("title"), "url": results[i]["url"]})
        if not cites:  # fall back to the results the answer was drawn from
            cites = [{"marker": i + 1, "title": r.get("title"), "url": r.get("url")} for i, r in enumerate(results) if r.get("url")]
        set_result(span, {"answered": bool(res.get("answer")), "citations": len(cites)})
        size = 600 if depth == "standard" else 320
        snippets = [f"{r.get('title') or ''}: {str(r.get('description') or r.get('content') or '')[:size]}" for r in results[:3]]
        return {"answer": res.get("answer") or "", "citations": cites[:6], "snippets": [x for x in snippets if len(x) > 20]}


def _largest_list(node: Any, depth: int = 0) -> list:
    best: list = []
    if depth > 5:
        return best
    if isinstance(node, list):
        if len(node) > len(best) and all(isinstance(x, dict) for x in node[:3]):
            best = node
        for x in node[:20]:
            sub = _largest_list(x, depth + 1)
            if len(sub) > len(best):
                best = sub
    elif isinstance(node, dict):
        for v in node.values():
            sub = _largest_list(v, depth + 1)
            if len(sub) > len(best):
                best = sub
    return best


# ---------------------------------------------------------- web search agents


# Nimble agent name -> the name its spans carry in Sentry.
_SPAN_AGENT = {"nibble-price-detective": "Price Detective", "nibble-signal-scout": "Signal Scout",
               "nibble-news-tracer": "News Tracer", "nibble-market-lens": "Market Lens",
               "nibble-investing-coach": "Investing Coach", "nibble-topic-basics": "Topic Guide"}


async def agent_start(
    task: str,
    agent_name: str,
    output_schema: dict | None = None,
    skill: str | None = None,
    prioritize: str | None = None,
    effort: str | None = None,
    use_case: str = "research",
    previous_interaction_id: str | None = None,
) -> dict:
    """Start a run on a persistent named agent. Reusing the name means the
    agent's memory carries over between runs."""
    kwargs: dict = {"input": task, "agent_name": agent_name, "effort": effort or config.AGENT_EFFORT, "use_case": use_case}
    if output_schema:
        kwargs["output_schema"] = output_schema
    if skill:
        kwargs["skill"] = skill
    if prioritize:
        kwargs["sources"] = {"prioritize": prioritize}
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id  # build on an earlier run
    with tool_span("nimble.agents.run", {k: v for k, v in kwargs.items() if k != "output_schema"},
                   _SPAN_AGENT.get(agent_name, agent_name),
                   "Start a Nimble Web Search Agent run") as span:
        run = _plain(await client().agents.run(**kwargs))
        set_result(span, {"run_id": run.get("id"), "status": run.get("status"), "effort": run.get("effort")})
        return {"run_id": run["id"], "agent_id": run["web_search_agent_id"], "status": run.get("status"),
                "interaction_id": run.get("interaction_id") or run["id"]}


async def agent_wait(run_id: str, agent_id: str, agent_name: str = "", timeout_s: int = 900, on_status=None) -> dict:
    """Poll a run to completion and return {content, type, trust}."""
    with tool_span("nimble.agents.wait", {"run_id": run_id}, _SPAN_AGENT.get(agent_name, agent_name),
                   "Poll a Web Search Agent run") as span:
        waited = 0.0
        delay = 2.0
        while True:
            run = _plain(await client().agents.runs.get(run_id, agent_id=agent_id))
            status = run.get("status")
            if on_status:
                on_status(status, waited)
            if status in ("completed", "failed", "cancelled"):
                break
            if waited > timeout_s:
                raise TimeoutError(f"Nimble agent run {run_id} still {status} after {timeout_s}s")
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 1.3, 8.0)
        if status != "completed":
            message = ((run.get("error") or {}).get("message")) or status
            raise RuntimeError(f"Nimble agent run {status}: {message}")
        res = _plain(await client().agents.runs.result(run_id, agent_id=agent_id))
        output = res.get("output")
        if not output:
            message = ((res.get("error") or {}).get("message")) or "no output"
            raise RuntimeError(f"Nimble agent returned no output: {message}")
        trust = output.get("trust") or {}
        set_result(span, {"seconds": round(waited), "confidence": trust.get("confidence"),
                          "claims": len(trust.get("claims") or []), "sources": len(trust.get("sources") or [])})
        return {"type": output.get("type"), "content": output.get("content"), "trust": trust, "seconds": round(waited)}


# ------------------------------------------------------------------ quotes

QUOTE_TEMPLATE = "morningstar_stock_quote_community_2026_04_24"


async def stock_quote(ticker: str, exchange: str | None = None, agent: str = "") -> dict:
    """A quote for one US-listed ticker from the Morningstar template. The
    template needs the exchange, so both main US exchanges are tried."""
    ticker = re.sub(r"[^A-Za-z.]", "", ticker).lower()[:8]
    if not ticker:
        raise ValueError("No ticker")
    order = [exchange.lower()] if exchange else []
    order += [x for x in ("xnys", "xnas") if x not in order]
    last: Exception | None = None
    for ex in order:
        try:
            parsing = await run_template(QUOTE_TEMPLATE, {"ticker": ticker, "exchange": ex}, False, agent, 60)
        except Exception as exc:
            last = exc
            continue
        row = _find_quote(parsing)
        if row and row.get("price"):
            return {k: row.get(k) for k in ("name", "ticker", "exchange", "price", "change", "change_percent",
                                             "previous_close", "market_cap", "pe_ratio", "dividend_yield",
                                             "52_week_low", "52_week_high")} | {"source": "Morningstar via Nimble"}
    raise RuntimeError(f"No quote found for {ticker.upper()}" + (f" ({last})" if last else ""))


def _find_quote(node: Any, depth: int = 0) -> dict | None:
    if depth > 5:
        return None
    if isinstance(node, dict):
        if "price" in node and ("ticker" in node or "previous_close" in node):
            return node
        for v in node.values():
            hit = _find_quote(v, depth + 1)
            if hit:
                return hit
    elif isinstance(node, list):
        for v in node[:10]:
            hit = _find_quote(v, depth + 1)
            if hit:
                return hit
    return None
