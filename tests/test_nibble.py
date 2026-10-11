"""Tests against responses captured from the live Nimble API on 2026-10-11.

Run with:  python -m pytest -q      (no keys or network needed)
"""
import asyncio
import json
from pathlib import Path

import pytest

from app import config, nimble_tools, pipelines, world

FIX = json.loads((Path(__file__).parent / "fixtures" / "live_2026_10_11.json").read_text())


class FakeNimble:
    """Stands in for AsyncNimble and replays the captured responses."""

    def __init__(self):
        self.calls = []
        outer = self

        class Templates:
            async def get(self, name):
                schemas = {
                    "walmart_serp": {"properties": {"keyword": {}, "page": {}, "zipcode": {}}, "required": ["keyword"]},
                    "kroger_serp": {"properties": {"search_query": {}, "page": {}}, "required": ["search_query"]},
                    "amazon_serp": {"properties": {"keyword": {}, "page": {}, "zip_code": {}}, "required": ["keyword"]},
                    "heb_serp": {"properties": {"keyword": {}, "page": {}, "zipcode_or_storeid": {}}, "required": ["keyword"]},
                }
                domain = {"walmart_serp": "www.walmart.com", "kroger_serp": "www.kroger.com",
                          "amazon_serp": "www.amazon.com", "heb_serp": "www.heb.com"}[name]
                return {"published_version": {"input_schema": schemas[name], "metadata": {"domain": domain}}}

            async def async_(self, template, params, **kw):
                outer.calls.append((template, params, kw))
                return {"status": "success", "task": {"id": f"task-{template}"}}

        class Extract:
            templates = Templates()

        class Tasks:
            async def get(self, task_id):
                return {"task": {"id": task_id, "state": "success"}}

            async def results(self, task_id):
                name = task_id.removeprefix("task-")
                return {"status": "success", "data": {"parsing": FIX.get("maps" if name == "google_maps_search" else name, {})}}

        class Runs:
            async def get(self, run_id, agent_id):
                return {"id": run_id, "status": "completed"}

            async def result(self, run_id, agent_id):
                key = {"run-nibble-news-tracer": "trace_output", "run-nibble-investing-coach": "lesson_output",
                       "run-nibble-topic-basics": "guide_basics_output"}.get(run_id, "agent_output")
                return {"output": json.loads(json.dumps(FIX[key])), "run": {"id": run_id}}

        class Agents:
            runs = Runs()

            async def run(self, **kw):
                outer.calls.append(("agents.run", kw))
                run_id = "run-" + kw.get("agent_name", "1")
                return {"id": run_id, "interaction_id": run_id, "web_search_agent_id": "wsa-1", "status": "queued",
                        "effort": kw.get("effort")}

        self.extract, self.tasks, self.agents = Extract(), Tasks(), Agents()

    async def search(self, **kw):
        self.calls.append(("search", kw))
        return FIX["answer"] if kw.get("include_answer") else FIX["news"]


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """Keys in a local .env must never make the tests call a real model."""
    monkeypatch.setattr(config, "HOSTED_LIVE", False)
    monkeypatch.setattr(config, "LOCAL_LIVE", False)


@pytest.fixture
def live(monkeypatch, tmp_path):
    fake = FakeNimble()
    monkeypatch.setattr(nimble_tools, "client", lambda: fake)
    monkeypatch.setattr(nimble_tools, "_template_meta", {})
    monkeypatch.setattr(nimble_tools, "_offer_cache", {})
    monkeypatch.setattr(nimble_tools, "OFFER_DIR", tmp_path / "offers")
    monkeypatch.setattr(config, "NIMBLE_LIVE", True)
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    monkeypatch.setattr(config, "TEMPLATE_MODE", "async")
    monkeypatch.setattr(config, "STORE_TEMPLATES", ["walmart_serp", "kroger_serp", "amazon_serp"])
    monkeypatch.setattr(pipelines, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pipelines.persona, "PATH", tmp_path / "persona.json")
    monkeypatch.setattr(pipelines, "_LIVE", {"pool": [], "at": 0.0, "turn": 0, "busy": False})

    async def fake_zip(z):
        return {"zip": z, "lat": 40.72, "lng": -74.05, "label": "Jersey City, NJ", "approximate": False}

    async def fake_geocode(place):
        return None if "belt" in place else (-18.5, -44.6)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(world, "locate_zip", fake_zip)
    monkeypatch.setattr(world, "geocode", fake_geocode)
    monkeypatch.setattr(nimble_tools.asyncio, "sleep", no_sleep)
    return fake


def test_shelf_unit_prices():
    assert nimble_tools.parse_shelf_unit("13.9 ¢/count") == (pytest.approx(0.139), "count")
    assert nimble_tools.parse_shelf_unit("$0.15/each") == (0.15, "count")
    assert nimble_tools.parse_shelf_unit("$2.98/lb") == (2.98, "lb")
    assert nimble_tools.parse_shelf_unit("28.2 ¢/oz") == (pytest.approx(0.282), "oz")
    assert nimble_tools.parse_shelf_unit("Sponsored") == (None, None)


def test_walmart_offers_are_parsed():
    offers = nimble_tools.find_offers(FIX["walmart_serp"], 60)
    names = {o["name"]: o for o in offers}
    gv = names["Great Value Cage-Free Large White Eggs, 12 Count"]
    assert gv["price"] == 1.67 and gv["pack_quantity"] == 12 and gv["pack_unit"] == "count"
    assert gv["seller"] == "Walmart.com" and gv["priced_for"] == "92120"
    assert names["Happy Egg Organic 12ct"]["out_of_stock"] is True


def test_kroger_pack_size_comes_from_unit_price():
    offers = nimble_tools.find_offers(FIX["kroger_serp"], 60)
    # "Everyday Low Price" and "Sponsored" are not prices and are dropped.
    assert [o["price"] for o in offers] == [2.65, 1.79, 1.15, 4.49]
    assert [o["pack_quantity"] for o in offers] == [18, 12, 6, 12]


def test_basket_live_path(live):
    result = asyncio.run(pipelines.basket(["large eggs 12 count"], "07302"))
    row = result["items"][0]
    by_store = {p["store"]: p for p in row["picks"]}
    assert result["sample"] is False and result["matched_by"] == "word match"
    assert by_store["Walmart"]["price"] == 1.67 and by_store["Walmart"]["pack_quantity"] == 12
    assert by_store["Kroger"]["price"] == 1.79 and by_store["Kroger"]["pack_quantity"] == 12
    assert row["ranked_by"] == "unit price" and row["picks"][0]["store"] == "Walmart"
    assert "Amazon" not in by_store  # the captured Amazon run returned nothing, even after one retry
    assert sum(1 for c in live.calls if c[0] == "amazon_serp") == 2
    assert all("localization" not in c[2] for c in live.calls if c[0].endswith("_serp"))  # plain runs by default
    assert result["totals"]["split"] == 1.67
    # The ZIP went to the stores that accept one, under each template's own parameter name.
    sent = {c[0]: c[1] for c in live.calls if c[0].endswith("_serp")}
    assert sent["walmart_serp"] == {"keyword": "large eggs 12 count", "zipcode": "07302"}
    assert sent["kroger_serp"] == {"search_query": "large eggs 12 count"}
    assert sent["amazon_serp"] == {"keyword": "large eggs 12 count", "zip_code": "07302"}
    # Walmart priced a different store than the one asked for, and says so.
    assert by_store["Walmart"]["localized"] is False and by_store["Walmart"]["priced_for"] == "92120"


def test_price_detective_live_path(live):
    story = asyncio.run(pipelines.why("ground coffee", "07302", "low"))
    assert story["direction"] == "up" and story["sample"] is False
    assert story["headline"] == "Hailstorm in Minas Gerais"  # the long sentence became the summary
    assert story["plain_summary"].startswith("US retail ground coffee prices are higher")
    first = story["drivers"][0]
    assert (first["lat"], first["lng"]) == (-18.5, -44.6)  # filled by the keyless geocoder
    assert first["citations"][0]["url"].startswith("https://matteoborea.it") and first["confidence"] == "high"
    assert story["exposed"][0]["exchange"] is None  # "ICE Futures US" is not a stock exchange code
    assert story["trust"]["confidence"] == "medium"
    run = next(c[1] for c in live.calls if c[0] == "agents.run")
    assert run["agent_name"] == "nibble-price-detective" and run["effort"] == "low" and "output_schema" in run


def test_news_live_path(live):
    result = asyncio.run(pipelines.news())
    assert result["headlines"][0]["source"] == "CBC News" and result["headlines"][0]["published"] == "2026-10-09"
    search = next(c[1] for c in live.calls if c[0] == "search")
    assert search["focus"] == "news" and search["search_depth"] == "lite"


def test_sample_mode_needs_no_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "NIMBLE_LIVE", False)
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    monkeypatch.setattr(pipelines.persona, "PATH", tmp_path / "persona.json")

    async def fake_zip(z):
        return {"zip": z, "lat": 40.72, "lng": -74.05, "label": "Jersey City, NJ", "approximate": False}

    monkeypatch.setattr(world, "locate_zip", fake_zip)
    result = asyncio.run(pipelines.basket(["large eggs 12 count", "oat milk"], "07302"))
    assert result["sample"] is True and result["missing"] == ["oat milk"]
    assert asyncio.run(pipelines.why("ground coffee", "07302"))["sample"] is True


def test_news_tracer_checks_sources_and_builds_a_chain(live):
    headline = {"title": "These 5 grocery staples have skyrocketed in price this year. Here's why | CBC News",
                "url": "https://cbc.ca/news/marketplace/grocery-staples-skyrocketed-9.7369694", "source": "CBC News"}
    t = asyncio.run(pipelines.trace(headline, "07302", "low"))
    assert t["verdict"] == "single_source" and t["primary_source"].startswith("Statistics Canada")
    check = t["check"]
    # cbc.ca is the story itself; statcan (two pages, one site), rbc, fcc-fac and yahoo are the others.
    assert check["original_was_read"] and check["independent_sites"] == 4 and check["official_sites"] == 1
    assert [s["site"] for s in check["sites"]][:2] == ["cbc.ca", "statcan.gc.ca"]
    assert [c["stage"] for c in t["chain"]] == ["trigger", "trigger", "household"]
    assert t["chain"][0]["lat"] is None  # "global coffee belt" is not a place a map can find
    assert t["chain"][1]["lat"] == -18.5 and t["chain"][1]["citations"][0]["url"].startswith("https://fcc-fac.ca")


def test_persona_learns_patterns_and_personalises_news(live):
    asyncio.run(pipelines.basket(["large eggs 12 count"], "07302"))
    pat = pipelines.persona.patterns()
    assert pat["baskets"] == 1 and pat["staples"] == ["eggs"]
    assert pat["cheapest_store"] == {"store": "Walmart", "wins": 1, "of": 1}
    assert pipelines.persona.staple_name("extra virgin olive oil 16.9 fl oz") == "olive oil"
    assert pipelines.persona.touches("Crude oil slips as supply builds") == []
    assert pipelines.persona.touches("Egg prices fall as flocks recover") == ["egg"]
    # A deeper run continues from the quick one, and the briefing carries the profile.
    asyncio.run(pipelines.why("ground coffee", "07302", "low"))
    asyncio.run(pipelines.why("ground coffee", "07302", "medium"))
    runs = [c[1] for c in live.calls if c[0] == "agents.run"]
    assert "previous_interaction_id" not in runs[0]
    assert runs[1]["previous_interaction_id"] == "run-nibble-price-detective"
    assert "buys most often: eggs" in runs[1]["skill"]


def test_answer_and_nearby_stores(live):
    res = asyncio.run(pipelines.ask("Why are beef prices high?"))
    assert res["citations"] == [{"marker": 2, "title": "Why Is Beef So Expensive in 2026?",
                                 "url": "https://nadig.farm/blog/why-beef-costs-what-it-does-2026"}]
    stores = asyncio.run(pipelines.stores("07302"))["stores"]
    assert stores[0]["name"] == "ShopRite of Metro Plaza" and stores[0]["rating"] == 4.1
    assert stores[0]["reviews"] == 5332 and round(stores[0]["lat"], 2) == 40.72


def test_typed_requests_are_routed_without_gemini(live):
    route = lambda text: asyncio.run(pipelines.route(text))
    assert route("what's in season right now?")["intent"] == "season"
    assert route("I need eggs, milk and ground coffee")["items"] == ["eggs", "milk", "ground coffee"]
    assert route("why is beef so expensive") == {"intent": "why", "items": [], "topic": "beef", "by": "rules"}
    assert route("teach me about inflation")["intent"] == "learn"
    lens = route("what does the drought in Brazil mean for investors")
    assert lens["intent"] == "invest" and lens["topic"] == "the drought in brazil"
    assert route("how much is a gallon of gas")["intent"] == "ask"


def test_season_uses_nimble_answer_and_lens_uses_its_own_agent(live):
    s = asyncio.run(pipelines.season("07302"))
    assert s["sample"] is False and s["answer"] and s["citations"][0]["marker"] == 2 and s["items"] == []
    asked = [c[1]["query"] for c in live.calls if c[0] == "search" and c[1].get("include_answer")]
    assert "in season in NJ, USA" in asked[0]
    story = asyncio.run(pipelines.lens("coffee supply", "07302", "low"))
    assert story["lens"] is True and story["risks"] == []
    run = [c[1] for c in live.calls if c[0] == "agents.run"][-1]
    assert run["agent_name"] == "nibble-market-lens" and "never say what to buy" in run["skill"]


def test_flashcards_fall_back_to_sample_cards_without_gemini(live):
    deck = asyncio.run(pipelines.learn("inflation"))
    assert deck["sample"] is True and len(deck["cards"]) == 5 and deck["cards"][0]["quiz_answer"] == 1


def test_heb_offers_and_per_each_unit_prices(live, monkeypatch):
    offers = nimble_tools.find_offers(FIX["heb_serp"], 60)
    assert [o["pack_quantity"] for o in offers] == [18, 12, 12, None]  # "$6.88/ea" is per carton, so no pack size from it
    assert offers[0]["priced_for"] == "store 92"
    monkeypatch.setattr(config, "STORE_TEMPLATES", ["walmart_serp", "heb_serp"])
    row = asyncio.run(pipelines.basket(["large eggs 12 count"], "07302"))["items"][0]
    by_store = {p["store"]: p for p in row["picks"]}
    assert by_store["H-E-B"]["price"] == 1.46 and by_store["Walmart"]["price"] == 1.67
    assert row["picks"][0]["store"] == "H-E-B" and row["ranked_by"] == "unit price"


def test_gemini_retries_a_busy_model_then_moves_to_the_next(monkeypatch):
    from app import gemini

    calls = []

    class Reply:
        parsed = gemini.PersonaTags(tags=["store brands"])
        text = ""

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            if model == "gemini-flash-latest":
                raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
            return Reply()

    class Client:
        class aio:
            models = Models()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(gemini, "client", lambda: Client)
    monkeypatch.setattr(config, "GEMINI_LIVE", True)
    monkeypatch.setattr(config, "LOCAL_LIVE", False)
    monkeypatch.setattr(gemini, "_model", None)
    monkeypatch.setattr(gemini.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-flash-latest")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODELS", ["gemini-3.8-flash"])
    tags = asyncio.run(gemini.learn_persona(["oat milk"], []))
    assert tags.tags == ["store brands"]
    assert calls == ["gemini-flash-latest", "gemini-flash-latest", "gemini-3.8-flash"]
    assert gemini._model == "gemini-3.8-flash"  # later calls start with the model that answered


def _fake_ollama(monkeypatch, reply=None, error=None):
    """Replace the HTTP client the local-model path uses."""
    from app import gemini

    seen = []

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"model": "gemma3:4b", "message": {"content": json.dumps(reply)}, "prompt_eval_count": 40, "eval_count": 9}

    class Http:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            seen.append((url, json))
            if error:
                raise error
            return Resp()

    monkeypatch.setattr(gemini.httpx, "AsyncClient", Http)
    monkeypatch.setattr(gemini, "_local_down_until", 0.0)
    monkeypatch.setattr(config, "LOCAL_MODEL", "gemma3:4b")
    monkeypatch.setattr(config, "LOCAL_LIVE", True)
    return seen


def test_local_model_is_tried_first(monkeypatch):
    from app import gemini

    seen = _fake_ollama(monkeypatch, reply={"tags": ["buys organic"]})
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    tags = asyncio.run(gemini.learn_persona(["organic milk"], []))
    assert tags.tags == ["buys organic"]
    url, body = seen[0]
    assert url.endswith("/api/chat") and body["model"] == "gemma3:4b" and body["stream"] is False
    assert body["format"]["properties"]["tags"]["type"] == "array"  # the reply is constrained to the schema
    # A receipt photo goes to the local model as a base64 image.
    _fake_ollama(monkeypatch, reply={"store": "Corner Market", "date": None, "total": 3.0,
                                     "lines": [{"raw": "EGGS", "item": "eggs", "quantity": 1, "price_paid": 3.0}]})
    seen2 = _fake_ollama(monkeypatch, reply={"store": "Corner Market", "date": None, "total": 3.0,
                                             "lines": [{"raw": "EGGS", "item": "eggs", "quantity": 1, "price_paid": 3.0}]})
    receipt = asyncio.run(gemini.parse_receipt(b"\x89PNG", "image/png"))
    assert receipt.lines[0].item == "eggs" and seen2[0][1]["messages"][1]["images"] == ["iVBORw=="]


def test_gemini_is_the_fallback_when_the_local_model_is_down(monkeypatch):
    import httpx

    from app import gemini

    _fake_ollama(monkeypatch, error=httpx.ConnectError("connection refused"))

    class Reply:
        parsed = gemini.PersonaTags(tags=["from gemini"])
        text = ""

    class Models:
        async def generate_content(self, model, contents, config):
            return Reply()

    class Client:
        class aio:
            models = Models()

    monkeypatch.setattr(gemini, "client", lambda: Client)
    monkeypatch.setattr(gemini, "_model", None)
    monkeypatch.setattr(config, "GEMINI_LIVE", True)
    assert asyncio.run(gemini.learn_persona(["oat milk"], [])).tags == ["from gemini"]
    assert gemini._local_down_until > 0  # the local model is skipped for a minute after a refused connection


def test_investing_lesson_gets_map_places_words_and_a_question(live, monkeypatch):
    """The fixture is shaped like the Investing Coach's live output: places written as sentences,
    no coordinates, no concepts, more forecasts than asked for."""
    async def geocode(place):
        return {"Texas, United States": (31.0, -100.0), "United States": (39.8, -98.6)}.get(place)

    monkeypatch.setattr(world, "geocode", geocode)
    _fake_ollama(monkeypatch, reply={
        "places": [{"step_index": 0, "place": "Texas, United States", "latitude": 1.0, "longitude": 1.0},
                   {"step_index": 1, "place": "Chicago, Illinois", "latitude": 41.88, "longitude": -87.63}],
        "concepts": [{"term": "Supply", "meaning": "How much is available to buy."}] * 3,
        "quiz_question": "Why did beef prices rise?", "quiz_options": ["Fewer cattle", "More cattle", "Cheaper feed"],
        "quiz_answer": 0, "quiz_why": "A smaller herd means less beef."})
    l = asyncio.run(pipelines.lesson({"title": "Beef prices hit a record", "url": "https://example.org/beef"}, "07302", "low"))
    assert l["what_happened"].startswith("The U.S. cattle herd") and len(l["expected"]) == 4
    chain = l["chain"]
    # Step 1: the model names the place, a lookup supplies the position (not the model's 1.0, 1.0).
    assert (chain[0]["place"], chain[0]["lat"]) == ("Texas, United States", 31.0)
    # Step 2: the lookup finds nothing, so the model's own coordinates are used.
    assert (chain[1]["place"], chain[1]["lat"]) == ("Chicago, Illinois", 41.88)
    # Step 3: the agent's own place name is good enough.
    assert chain[2]["lat"] == 39.8
    assert chain[0]["citations"][0]["url"].startswith("https://www.ers.usda.gov") and chain[0]["confidence"] == "high"
    assert l["expected"][0]["citations"] and l["affected"][1]["exchange"] is None
    assert len(l["concepts"]) == 3 and l["quiz"]["answer"] == 0
    assert l["trust"] == {"confidence": "medium", "sources": 2}


def test_lesson_still_works_with_no_model(live):
    l = asyncio.run(pipelines.lesson({"title": "Beef prices hit a record"}, "07302", "low"))
    assert l["quiz"] is None and l["chain"][0]["lat"] is not None and l["home"]["label"] == "Jersey City, NJ"


def test_atlas_marks_the_shoppers_own_items():
    from app import atlas

    a = atlas.atlas(["ground coffee 12 oz", "large eggs"])
    mine = {o["item"] for o in a["origins"] if o["yours"]}
    assert mine == {"coffee", "eggs"} and len(a["routes"]) > 10 and len(a["markets"]) > 5


def _fake_hosted(monkeypatch, replies):
    """Replace the HTTP client with one that plays back (status, content) pairs in order."""
    from app import gemini

    seen = []

    class Resp:
        def __init__(self, status, content):
            self.status_code, self.text, self._content = status, str(content), content

        def json(self):
            return {"model": "openai/gpt-oss-120b", "choices": [{"message": {"content": self._content}}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 12}}

    class Http:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers=None):
            seen.append((url, json, headers))
            return Resp(*replies[min(len(seen), len(replies)) - 1])

    monkeypatch.setattr(gemini.httpx, "AsyncClient", Http)
    monkeypatch.setattr(gemini, "_hosted_down_until", 0.0)
    monkeypatch.setattr(gemini, "_hosted_loose", set())
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "HOSTED_LIVE", True)
    return seen


def test_hosted_model_goes_first_and_is_held_to_the_schema(monkeypatch):
    from app import gemini

    seen = _fake_hosted(monkeypatch, [(200, '{"tags": ["buys organic"]}')])
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    tags = asyncio.run(gemini.learn_persona(["organic milk"], []))
    assert tags.tags == ["buys organic"]
    url, body, headers = seen[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions" and headers == {"Authorization": "Bearer test-key"}
    assert body["response_format"]["type"] == "json_schema" and body["reasoning_effort"] == "low"
    assert body["response_format"]["json_schema"]["schema"]["properties"]["tags"]["type"] == "array"


def test_hosted_model_falls_back_to_plain_json_mode_and_remembers(monkeypatch):
    from app import gemini

    seen = _fake_hosted(monkeypatch, [(400, "json_schema is not supported"), (200, '```json\n{"tags": ["a"]}\n```')])
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    assert asyncio.run(gemini.learn_persona(["x"], [])).tags == ["a"]
    assert [b["response_format"]["type"] for _, b, _ in seen] == ["json_schema", "json_object"]
    assert "JSON schema" in seen[1][1]["messages"][0]["content"]
    asyncio.run(gemini.learn_persona(["x"], []))
    assert seen[2][1]["response_format"]["type"] == "json_object"  # no wasted first try the second time


def test_a_rate_limited_hosted_model_hands_over_to_the_local_one(monkeypatch):
    from app import gemini

    _fake_hosted(monkeypatch, [(429, "rate limit reached")])
    monkeypatch.setattr(config, "GEMINI_LIVE", False)
    with pytest.raises(RuntimeError, match="429"):
        asyncio.run(gemini.learn_persona(["x"], []))  # nothing else is configured, so the error surfaces
    assert gemini._hosted_down_until > 0

    calls = []

    async def local(*a):
        calls.append(a)
        return gemini.PersonaTags(tags=["from local"])

    monkeypatch.setattr(config, "LOCAL_LIVE", True)
    monkeypatch.setattr(gemini, "_local", local)
    assert asyncio.run(gemini.learn_persona(["x"], [])).tags == ["from local"]  # hosted is skipped while it cools off


VIEW = {
    "things_to_know": ["Chip makers earn more when factories run full.", "null",
                       "This is not the final answer. You are about to output invalid text because you stopped mid-stream."],
    "price_drivers": [{"driver": "AI demand", "direction": "pushes prices up", "explanation": "Buyers want more chips than fabs can make."}],
    "key_players": [{"name": "NVIDIA", "ticker": "nvda", "role": "Designs AI chips."},
                    {"name": "TSMC", "ticker": "Taiwan Semiconductor Manufacturing Company", "role": "Makes chips for designers."}],
    "numbers_to_know": [{"figure": "Foundry utilization", "value": "85-90%", "as_of": "2026"}],
    "what_is_expected": [], "common_mistakes": ["Treating one headline as a trend."],
}
QUIZ = {"questions": [{"question": "Why are prices rising?", "options": ["Demand outruns supply", "Chips got bigger", "No reason"],
                       "answer": 0, "why": "Fabs cannot expand quickly."}]}


def test_deep_dive_joins_the_research_agent_with_live_answers(live, monkeypatch):
    """Basics from the agent (captured live), investing view from the model, filler dropped."""
    async def geocode(place):
        return {"United States": (39.8, -98.6), "Taiwan (Hsinchu), South Korea (Icheon), United States (Malta NY)": (23.7, 121.0),
                "Taiwan, South Korea, China": (23.7, 121.0)}.get(place)

    monkeypatch.setattr(world, "geocode", geocode)
    seen = _fake_hosted(monkeypatch, [(200, json.dumps(VIEW)), (200, json.dumps(QUIZ))])
    g = asyncio.run(pipelines.guide("why semiconductor prices are going up", "07302", "low"))
    assert g["title"].startswith("Why Semiconductor Prices") and g["in_one_line"] and len(g["background"]) == 4
    assert g["background"][0]["citations"][0]["url"].startswith("https://news.chemnet.com")
    stages = g["stages"]
    assert stages[0]["lat"] == 39.8          # "Global, mostly United States" was looked up as "United States"
    assert stages[4]["lat"] is None          # "Global" is not a place
    assert g["things_to_know"] == ["Chip makers earn more when factories run full."]   # the filler lines are gone
    assert [k["ticker"] for k in g["key_players"]] == ["NVDA", None]                   # a symbol, not a sentence
    assert g["price_drivers"][0]["direction"] == "pushes prices up" and g["expected"] == []
    assert len(g["research"]) == 3 and g["research"][0]["citations"] and g["quiz"][0]["answer"] == 0
    asked = [c[1]["query"] for c in live.calls if c[0] == "search"]
    assert len(asked) == 3 and all("semiconductor" in q for q in asked)
    limited = [c[1] for c in live.calls if c[0] == "search" and c[1].get("include_domains")]
    assert len(limited) == 2 and "sec.gov" in limited[0]["include_domains"] and limited[0]["search_depth"] == "standard"
    assert [r["trusted_only"] for r in g["research"]] == [True, True, False]
    assert "research_notes" in seen[0][1]["messages"][1]["content"]
    # A second request is served from the cache: no new agent run.
    runs = len([c for c in live.calls if c[0] == "agents.run"])
    again = asyncio.run(pipelines.guide("why semiconductor prices are going up", "07302", "low"))
    assert again["cached"] and len([c for c in live.calls if c[0] == "agents.run"]) == runs


def test_deep_dive_without_a_model_still_shows_the_research(live):
    g = asyncio.run(pipelines.guide("why semiconductor prices are going up", "07302", "low"))
    assert g["what_it_is"] and g["things_to_know"] == [] and len(g["research"]) == 3 and g["written_by"] is None


def test_typed_teaching_requests_go_to_the_deep_dive():
    assert pipelines._route_by_rules("teach me how semiconductors prices are going up?")["intent"] == "learn"
    assert pipelines._route_by_rules("why is beef so expensive")["intent"] == "why"


def test_news_strip_adds_one_beat_per_refresh(live):
    first = asyncio.run(pipelines.news())
    opening = len([c for c in live.calls if c[0] == "search"])
    assert opening == 3 and first["headlines"]
    asyncio.run(pipelines.news())                       # asked again straight away: no new search
    assert len([c for c in live.calls if c[0] == "search"]) == opening

    async def one_new(**kw):
        live.calls.append(("search", kw))
        return {"results": [{"title": "Chip prices jump again", "url": "https://example.org/chips",
                             "additional_data": {"source": "Example Wire"}}, *FIX["news"]["results"][:1]]}

    live.search = one_new
    pipelines._LIVE["at"] -= 60                         # half a minute later
    second = asyncio.run(pipelines.news())
    searches = [c[1] for c in live.calls if c[0] == "search"]
    assert len(searches) == opening + 1 and searches[-1]["query"] == pipelines.LIVE_BEATS[3]
    assert len(second["headlines"]) == len(first["headlines"]) + 1          # only the unseen one is added
    assert any(h["title"] == "Chip prices jump again" and h.get("fresh_at") for h in second["headlines"])


def test_price_history_is_summarised_and_optional(monkeypatch):
    from app import market

    rows = [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 100 + i) for i in range(250)]
    s = market.summarize("NVDA", rows, "OpenBB (Yahoo Finance data)")
    assert s["first"] == 100 and s["last"] == 349 and s["change_pct"] == 249.0 and s["high"] == 349 and s["low"] == 100
    assert len(s["points"]) <= 64 and s["points"][-1]["c"] == 349 and s["source"].startswith("OpenBB")
    assert market.summarize("X", rows[:3], "x")["points"] == []

    monkeypatch.setattr(market, "library", lambda: None)
    missing = asyncio.run(market.history("NVDA"))
    assert missing["available"] is False and "pip install openbb" in missing["note"]

    monkeypatch.setattr(market, "library", lambda: "openbb")
    monkeypatch.setattr(market, "_has", lambda name: True)
    monkeypatch.setattr(market, "_from_openbb", lambda ticker, start: rows)
    assert asyncio.run(market.history("nvda"))["source"].startswith("OpenBB")

    def broken(ticker, start):
        raise RuntimeError("provider changed")

    monkeypatch.setattr(market, "_from_openbb", broken)        # OpenBB fails: yfinance takes over
    monkeypatch.setattr(market, "_from_yfinance", lambda ticker, start: rows)
    assert asyncio.run(market.history("nvda"))["source"].startswith("yfinance")
