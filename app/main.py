"""Nibble's web server. Run with: uvicorn app.main:app --port 8000"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable

import sentry_sdk
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, demo, nimble_tools, obs, persona, pipelines, world

obs.init()  # before the app is created so the FastAPI integration attaches

app = FastAPI(title="Nibble", docs_url="/api/docs", openapi_url="/api/openapi.json")
STATIC = config.ROOT / "static"

# Long agent runs happen in the background; the page polls for the result.
JOBS: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()


def _start_job(kind: str, label: str, work: Callable[[Callable], Awaitable[dict]]) -> dict:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "kind": kind, "label": label, "status": "running", "phase": "queued",
           "started": time.time(), "result": None, "error": None}
    JOBS[job_id] = job
    if len(JOBS) > 100:
        for old in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:20]:
            JOBS.pop(old, None)

    def on_status(status: str, waited: float) -> None:
        job["phase"] = status

    async def runner() -> None:
        # Its own Sentry transaction: the HTTP request that started it has already returned.
        with sentry_sdk.isolation_scope():
            with sentry_sdk.start_transaction(op="gen_ai.invoke_agent", name=f"{kind}: {label}"):
                try:
                    job["result"] = await work(on_status)
                    job["status"] = "done"
                except Exception as exc:
                    job["status"] = "failed"
                    job["error"] = f"{type(exc).__name__}: {exc}"[:400]
                    job["sentry_event"] = obs.capture(exc)
                finally:
                    job["seconds"] = round(time.time() - job["started"], 1)

    task = asyncio.create_task(runner())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"job": job_id}


class BasketIn(BaseModel):
    items: list[str] = Field(min_length=1, max_length=15)
    zip: str = "10001"


class WhyIn(BaseModel):
    item: str = Field(min_length=2, max_length=80)
    zip: str = "10001"
    effort: str | None = Field(default=None, pattern="^(low|medium|high)$")


class SignalsIn(BaseModel):
    company: str = Field(min_length=2, max_length=60)
    effort: str | None = Field(default=None, pattern="^(low|medium|high)$")


class QuoteIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=8, pattern="^[A-Za-z.]+$")
    exchange: str | None = Field(default=None, pattern="^(xnys|xnas)$")


class TraceIn(BaseModel):
    title: str = Field(min_length=4, max_length=300)
    url: str | None = Field(default=None, max_length=600)
    description: str | None = Field(default=None, max_length=600)
    source: str | None = Field(default=None, max_length=80)
    published: str | None = Field(default=None, max_length=40)
    place: str | None = Field(default=None, max_length=80)
    lat: float | None = None
    lng: float | None = None
    groceries: list[str] | None = None
    zip: str = "10001"
    effort: str | None = Field(default=None, pattern="^(low|medium|high)$")


class LensIn(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    zip: str = "10001"
    effort: str | None = Field(default=None, pattern="^(low|medium|high)$")


class RouteIn(BaseModel):
    text: str = Field(min_length=2, max_length=400)


class LearnIn(BaseModel):
    topic: str = Field(default="", max_length=80)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class PersonaIn(BaseModel):
    organic_only: bool | None = None
    store_brands_ok: bool | None = None
    diet: str | None = None
    skip_stores: list[str] | None = None
    learned: list[str] | None = None


@app.get("/api/status")
async def status() -> dict:
    from . import gemini

    local = await gemini.local_status()
    info = {**config.status(), "local": local, "local_model": config.LOCAL_MODEL or None,
            "sample_list": demo.SAMPLE_LIST}
    info["ai"] = "hosted" if config.HOSTED_LIVE else "local" if local == "live" else ("gemini" if config.GEMINI_LIVE else "off")
    info["hosted_by"] = config.LLM_BASE_URL.split("//")[-1].split("/")[0] if config.HOSTED_LIVE else None
    return info


@app.post("/api/basket")
async def basket(body: BasketIn) -> dict:
    label = f"{len(body.items)} items near {body.zip}"
    return _start_job("Basket Scout", label, lambda cb: pipelines.basket(body.items, body.zip, cb))


@app.post("/api/receipt")
async def receipt(zip: str = Form("10001"), photo: UploadFile | None = File(None)) -> dict:
    image, mime = None, "image/jpeg"
    if photo is not None:
        image = await photo.read()
        mime = photo.content_type or mime
        if len(image) > 8 * 1024 * 1024:
            raise HTTPException(413, "Photo is larger than 8 MB")
        if not mime.startswith("image/") and mime != "application/pdf":
            raise HTTPException(415, "Upload a photo or PDF of the receipt")
    return _start_job("Receipt Reader", "receipt photo", lambda cb: pipelines.receipt(image, mime, zip, cb))


@app.post("/api/why")
async def why(body: WhyIn) -> dict:
    return _start_job("Price Detective", body.item, lambda cb: pipelines.why(body.item, body.zip, body.effort, cb))


@app.post("/api/signals")
async def signals(body: SignalsIn) -> dict:
    return _start_job("Signal Scout", body.company, lambda cb: pipelines.signals(body.company, body.effort, cb))


@app.post("/api/quote")
async def quote(body: QuoteIn) -> dict:
    return _start_job("Signal Scout", f"quote {body.ticker.upper()}", lambda cb: pipelines.quote(body.ticker, body.exchange, cb))


@app.post("/api/trace")
async def trace(body: TraceIn) -> dict:
    headline = body.model_dump(exclude={"zip", "effort"})
    return _start_job("News Tracer", body.title[:60], lambda cb: pipelines.trace(headline, body.zip, body.effort, cb))


@app.post("/api/lens")
async def lens(body: LensIn) -> dict:
    return _start_job("Market Lens", body.topic[:60], lambda cb: pipelines.lens(body.topic, body.zip, body.effort, cb))


@app.post("/api/route")
async def route(body: RouteIn) -> dict:
    return await pipelines.route(body.text)


@app.get("/api/season")
async def season(zip: str = "10001") -> dict:
    return await pipelines.season(zip)


@app.post("/api/learn")
async def learn(body: LearnIn) -> dict:
    return await pipelines.learn(body.topic)


@app.post("/api/lesson")
async def lesson(body: TraceIn) -> dict:
    headline = body.model_dump(exclude={"zip", "effort"})
    return _start_job("Investing Coach", body.title[:60], lambda cb: pipelines.lesson(headline, body.zip, body.effort, cb))


@app.get("/api/atlas")
async def get_atlas(items: str = "") -> dict:
    """Fixed globe layers. `items` is the current shopping list, comma separated, so its origins are marked."""
    from . import atlas

    listed = [x.strip().lower()[:40] for x in items.split(",") if x.strip()][:20]
    return atlas.atlas([s.lower() for s in persona.patterns()["staples"]] + listed)


@app.post("/api/ask")
async def ask(body: AskIn) -> dict:
    return await pipelines.ask(body.question)


@app.get("/api/jobs/{job_id}")
async def job(job_id: str) -> dict:
    j = JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown job")
    return {**j, "elapsed": round(time.time() - j["started"], 1)}


@app.get("/api/news")
async def news() -> dict:
    return await pipelines.news()


@app.get("/api/world")
async def live_world() -> dict:
    return await world.live_events()


@app.get("/api/stores")
async def stores(zip: str = "10001") -> dict:
    try:
        return await pipelines.stores("".join(ch for ch in zip if ch.isdigit())[:5])
    except Exception as exc:
        obs.capture(exc)
        return {"sample": False, "stores": [], "error": type(exc).__name__}


@app.get("/api/persona")
async def get_persona() -> dict:
    p = persona.load()
    return {**p, "patterns": persona.patterns(p)}


@app.put("/api/persona")
async def put_persona(body: PersonaIn) -> dict:
    p = persona.update({k: v for k, v in body.model_dump().items() if v is not None})
    return {**p, "patterns": persona.patterns(p)}


@app.delete("/api/persona")
async def forget_persona() -> dict:
    p = persona.reset()
    return {**p, "patterns": persona.patterns(p)}


@app.get("/api/activity")
async def activity() -> dict:
    return obs.activity()


@app.post("/api/chaos")
async def chaos() -> dict:
    """Break one tool call on purpose, to show the failure landing in Sentry."""
    with obs.agent_span("Basket Scout", "Chaos test: call a template that does not exist"):
        try:
            if config.NIMBLE_LIVE:
                await nimble_tools.run_template("nibble_chaos_template_that_does_not_exist", {"keyword": "eggs"}, False, "Basket Scout")
            else:
                with obs.tool_span("nimble.template.chaos", {"keyword": "eggs"}, "Basket Scout"):
                    raise RuntimeError("Simulated tool failure (sample mode)")
        except Exception as exc:
            return {"broke": True, "error": f"{type(exc).__name__}: {exc}"[:300], "sentry_event": obs.capture(exc),
                    "sentry": config.SENTRY_LIVE}
    return {"broke": False}


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    event = obs.capture(exc)
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"[:400], "sentry_event": event}, status_code=500)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
