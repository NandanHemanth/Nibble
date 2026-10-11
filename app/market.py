"""Past share prices, through open-source market-data libraries.

OpenBB (https://github.com/OpenBB-finance/OpenBB) is used when it is installed,
with its free Yahoo Finance provider. If only yfinance is installed, that is
used directly. With neither, the "past year" button explains how to add one.
Nothing here needs an API key.
"""
from __future__ import annotations

import asyncio
import importlib.util
import math
from datetime import date, timedelta


def _has(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def library() -> str | None:
    """Which library will be used: 'openbb', 'yfinance' or None."""
    return next((name for name in ("openbb", "yfinance") if _has(name)), None)


def _from_openbb(ticker: str, start: str) -> list[tuple[str, float]]:
    from openbb import obb

    rows = obb.equity.price.historical(symbol=ticker, start_date=start, provider="yfinance").results
    return [(str(r.date)[:10], float(r.close)) for r in rows if getattr(r, "close", None) is not None]


def _from_yfinance(ticker: str, start: str) -> list[tuple[str, float]]:
    import yfinance as yf

    closes = yf.Ticker(ticker).history(start=start, interval="1d")["Close"].dropna()
    return [(stamp.strftime("%Y-%m-%d"), float(value)) for stamp, value in closes.items()]


def summarize(ticker: str, rows: list[tuple[str, float]], source: str) -> dict:
    """Turn daily closes into what the page shows: about 60 points and five plain numbers."""
    rows = [(d, c) for d, c in rows if isinstance(c, (int, float)) and math.isfinite(c) and c > 0]
    if len(rows) < 5:
        return {"available": True, "ticker": ticker, "points": [], "note": f"No price history was found for {ticker}."}
    step = max(1, len(rows) // 60)
    points = rows[::step]
    if points[-1] != rows[-1]:
        points.append(rows[-1])
    first, last = rows[0][1], rows[-1][1]
    high = max(rows, key=lambda r: r[1])
    low = min(rows, key=lambda r: r[1])
    return {
        "available": True, "ticker": ticker, "source": source,
        "points": [{"d": d, "c": round(c, 2)} for d, c in points],
        "from": rows[0][0], "to": rows[-1][0], "first": round(first, 2), "last": round(last, 2),
        "change_pct": round((last - first) / first * 100, 1),
        "high": round(high[1], 2), "high_on": high[0], "low": round(low[1], 2), "low_on": low[0],
    }


async def history(ticker: str, days: int = 365) -> dict:
    """About a year of daily closing prices for one US ticker."""
    ticker = ticker.upper().strip()
    lib = library()
    if not lib:
        return {"available": False, "ticker": ticker,
                "note": "Price history needs OpenBB. In the project folder run: .venv/bin/pip install openbb  then restart Nibble."}
    start = (date.today() - timedelta(days=days)).isoformat()
    routes = [(_from_openbb, "OpenBB (Yahoo Finance data)")] if lib == "openbb" else []
    if _has("yfinance"):  # OpenBB's Yahoo provider installs it, so it doubles as the fallback
        routes.append((_from_yfinance, "yfinance (Yahoo Finance data)"))
    last: Exception | None = None
    for fetch, source in routes:
        try:
            rows = await asyncio.wait_for(asyncio.to_thread(fetch, ticker, start), timeout=45)
            if len(rows) >= 5:
                return summarize(ticker, rows, source)
        except Exception as exc:
            last = exc
    if last:
        raise last
    return summarize(ticker, [], "")
