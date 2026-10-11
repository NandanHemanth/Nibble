"""Check each service with one real call. Run before you demo:

    .venv/bin/python scripts/preflight.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, gemini, nimble_tools, obs  # noqa: E402


async def main() -> None:
    print(f"Nimble {'key found' if config.NIMBLE_API_KEY else 'KEY MISSING'} | "
          f"Gemini {'key found' if config.GEMINI_API_KEY else 'KEY MISSING'} | "
          f"Sentry {'DSN found' if config.SENTRY_DSN else 'off'}\n")

    if config.NIMBLE_API_KEY:
        for template in config.STORE_TEMPLATES:
            start = time.time()
            try:
                offers = await nimble_tools.store_search(template, "large eggs", "10001")
                took = time.time() - start
                print(f"[ok]   {template}: {len(offers)} products in {took:.0f} s")
                for o in offers[:3]:
                    print(f"         ${o['price']:<6} {o['name'][:60]}  (priced at {o.get('priced_for')})")
                if not offers:
                    print("         No products parsed after one retry. Result shape, to help debug:")
                    print(f"         {nimble_tools.LAST_RAW.get(template)}")
            except Exception as exc:
                print(f"[FAIL] {template}: {type(exc).__name__}: {exc} (after {time.time() - start:.0f} s)")
        try:
            stores = await nimble_tools.nearby_stores("10001")
            print(f"[ok]   google_maps_search: {len(stores)} stores, first: {stores[0]['name'] if stores else '-'}")
        except Exception as exc:
            print(f"[FAIL] google_maps_search: {type(exc).__name__}: {exc}")
        try:
            reply = await nimble_tools.answer("Why are US egg prices changing?")
            print(f"[ok]   Nimble Answer: {len(reply['citations'])} citations, {reply['answer'][:70]}...")
        except Exception as exc:
            print(f"[FAIL] Nimble Answer: {type(exc).__name__}: {exc}")
        try:
            news = await nimble_tools.news_search("food prices", "day", 3)
            print(f"[ok]   news search: {len(news)} headlines, first: {news[0]['title'][:70] if news else '-'}")
        except Exception as exc:
            print(f"[FAIL] news search: {type(exc).__name__}: {exc}")

    if config.HOSTED_LIVE:
        start = time.time()
        try:
            tags = await gemini._hosted("Known habits: []\nItems: ['organic whole milk', 'store brand oats']",
                                        gemini.PersonaTags, "Name buying habits visible in the product names.",
                                        "preflight", 0.0, None)
            mode = "JSON mode" if "PersonaTags" in gemini._hosted_loose else "JSON schema"
            print(f"[ok]   Hosted model ({config.LLM_MODEL} at {config.LLM_BASE_URL}): {tags.tags} in {time.time() - start:.1f} s, {mode}")
        except Exception as exc:
            print(f"[FAIL] Hosted model ({config.LLM_MODEL} at {config.LLM_BASE_URL}): {type(exc).__name__}: {str(exc)[:240]}")
    else:
        print("[--]   Hosted model: off (set NIBBLE_LLM_API_KEY in .env to use one)")

    local = await gemini.local_status()
    if local == "live":
        start = time.time()
        try:
            tags = await gemini._local("Known habits: []\nItems: ['organic whole milk', 'store brand oats']",
                                       gemini.PersonaTags, "Name buying habits visible in the product names.",
                                       "preflight", 0.0, None)
            print(f"[ok]   Local model ({config.LOCAL_MODEL}): {tags.tags} in {time.time() - start:.0f} s")
        except Exception as exc:
            print(f"[FAIL] Local model ({config.LOCAL_MODEL}): {type(exc).__name__}: {str(exc)[:160]}")
    elif local != "off":
        print(f"[FAIL] Local model ({config.LOCAL_MODEL}): {local}. Run: ollama serve, then: ollama pull {config.LOCAL_MODEL}")
    else:
        print("[--]   Local model: off (set NIBBLE_LOCAL_MODEL in .env to use one)")

    if config.GEMINI_API_KEY:
        saved, config.LOCAL_LIVE = config.LOCAL_LIVE, False  # test Gemini itself, not the other models
        saved_hosted, config.HOSTED_LIVE = config.HOSTED_LIVE, False
        try:
            tags = await gemini.learn_persona(["organic whole milk", "store brand oats"], [])
            print(f"[ok]   Gemini ({gemini._model}): {tags.tags}")
        except Exception as exc:
            print(f"[FAIL] Gemini: {type(exc).__name__}: {str(exc)[:160]}")
            if gemini._is_busy(exc):
                print("         Google reports the model is busy. This is temporary; run preflight again in a minute.")
                print("         The app keeps working meanwhile: it retries, tries the fallback models, then uses plain rules.")
            else:
                print("         Set GEMINI_MODEL in .env to a model your key can use.")
        config.LOCAL_LIVE, config.HOSTED_LIVE = saved, saved_hosted

    if config.SENTRY_DSN:
        import sentry_sdk

        obs.init()
        event = sentry_sdk.capture_message("Nibble preflight check")
        sentry_sdk.flush(5)
        print(f"[ok]   Sentry: sent test event {event}")


asyncio.run(main())
