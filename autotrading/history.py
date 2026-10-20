"""Historical Binance *public* spot klines. GET-only, no credentials, no trading."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from .market import ALLOWED_PAIRS, Candle, parse_closed_candles

PUBLIC_HOST = "https://data-api.binance.vision"
INTERVAL_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}
PAGE_LIMIT = 1000
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "history"


def public_get(path, params=None):
    if path not in ("/api/v3/time", "/api/v3/klines", "/api/v3/exchangeInfo"):
        raise ValueError("Historical downloader only supports public market data")
    url = PUBLIC_HOST + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Autotrading-Research/0.1"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError("Public Binance market API HTTP " + str(exc.code)) from None
            time.sleep(min(2 ** attempt, 8))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            time.sleep(min(2 ** attempt, 8))


def time_range(days, server_time_ms):
    """Return an inclusive end of last completed 15m candle and a 16-day warmup."""
    if not 30 <= days <= 365:
        raise ValueError("Use --days between 30 and 365")
    end_ms = (server_time_ms // INTERVAL_MS["15m"]) * INTERVAL_MS["15m"] - 1
    # 16 days warm up 200+ hourly EMA; no trading in warmup.
    trading_start = (end_ms + 1) - days * 86_400_000
    download_start = trading_start - 16 * 86_400_000
    return download_start, trading_start, end_ms


def validate_candles(candles, interval, start_ms, end_ms):
    step = INTERVAL_MS[interval]
    first_expected = (start_ms // step) * step
    last_expected = ((end_ms + 1) // step - 1) * step
    if not candles or candles[0].open_time_ms != first_expected:
        raise RuntimeError("Missing start of historical series: cannot backtest")
    if candles[-1].open_time_ms != last_expected:
        raise RuntimeError("Missing end of historical series: cannot backtest")
    for before, after in zip(candles, candles[1:]):
        if after.open_time_ms - before.open_time_ms != step:
            raise RuntimeError("Historical data contains a missing or duplicate candle")


def fetch_range(symbol, interval, start_ms, end_ms, requester=None):
    if symbol not in ALLOWED_PAIRS or interval not in INTERVAL_MS:
        raise ValueError("Unsupported historical pair/interval")
    if end_ms < start_ms:
        raise ValueError("Invalid requested historical period")
    request = requester or public_get
    step = INTERVAL_MS[interval]
    cursor = (start_ms // step) * step
    rows = []
    while cursor <= end_ms:
        page = request("/api/v3/klines", {
            "symbol": symbol, "interval": interval,
            "startTime": cursor, "endTime": end_ms, "limit": PAGE_LIMIT
        })
        if not page:
            break
        rows.extend(page)
        last_open = int(page[-1][0])
        if last_open < cursor:
            raise RuntimeError("Kline pagination failed to advance")
        cursor = last_open + step
        if len(rows) > 90_000:
            raise RuntimeError("Historical download exceeded safety bound")
        if cursor <= end_ms:
            time.sleep(0.08)
    # Supply end_ms+1 as a synthetic clock: the final completed bar is included.
    candles = parse_closed_candles(rows, end_ms + 1)
    validate_candles(candles, interval, start_ms, end_ms)
    return candles


def load_history(symbol, interval, start_ms, end_ms, refresh=False, requester=None):
    if symbol not in ALLOWED_PAIRS or interval not in INTERVAL_MS:
        raise ValueError("Unsupported historical pair/interval")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}-{interval}-{start_ms}-{end_ms}.json"
    if path.exists() and not refresh:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source") != PUBLIC_HOST or payload.get("symbol") != symbol:
            raise RuntimeError("Unexpected historical cache metadata; use --refresh")
        candles = [Candle(**x) for x in payload["candles"]]
        validate_candles(candles, interval, start_ms, end_ms)
        print(f"Using cached {symbol} {interval}: {len(candles)} candles", flush=True)
        return candles
    candles = fetch_range(symbol, interval, start_ms, end_ms, requester=requester)
    payload = {
        "source": PUBLIC_HOST, "symbol": symbol, "interval": interval,
        "start_ms": start_ms, "end_ms": end_ms,
        "candles": [asdict(c) for c in candles],
    }
    temp = path.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as output:
        json.dump(payload, output, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temp, path)
    print(f"Downloaded {symbol} {interval}: {len(candles)} candles", flush=True)
    return candles


def first_available_open(symbol, interval, requester=None):
    """Find the first available PUBLIC spot bar, without backfilling dates.

    Binance REST returns the oldest bars on/after the requested startTime.
    Trading pairs have different listing dates; 8 years requested is NOT a
    guarantee of 8 years of usable BTCUSDC/ETHUSDC bars.
    """
    if symbol not in ALLOWED_PAIRS or interval not in INTERVAL_MS:
        raise ValueError("Unsupported spot pair or timeframe")
    fetch = requester or public_get
    page = fetch("/api/v3/klines", {
        "symbol": symbol, "interval": interval, "startTime": 0, "limit": 1,
    })
    if not page or not isinstance(page, list) or not isinstance(page[0], list):
        raise RuntimeError(f"No public spot history for {symbol} {interval}")
    start = int(page[0][0])
    if start < 0 or start % INTERVAL_MS[interval] != 0:
        raise RuntimeError("Unexpected earliest Binance candle timestamp")
    return start


def current_spot_filters(symbol, requester=None):
    """Current PUBLIC exchange filters for simulated MARKET orders.

    IMPORTANT: current exchange filters are NOT point-in-time historical
    filters. The reported order rejections are approximate for old dates.
    """
    if symbol not in ALLOWED_PAIRS:
        raise ValueError("Unsupported symbol")
    info = (requester or public_get)("/api/v3/exchangeInfo", {"symbol": symbol})
    items = info.get("symbols", [])
    if len(items) != 1 or items[0].get("symbol") != symbol or items[0].get("status") != "TRADING":
        raise RuntimeError("Market exchangeInfo is missing or symbol is not active")
    filters = {f["filterType"]: f for f in items[0].get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE", {})
    # Some symbols publish MARKET_LOT_SIZE stepSize=0; use LOT_SIZE then.
    if float(lot.get("stepSize", "0")) <= 0:
        lot = filters.get("LOT_SIZE", {})
    notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
    return {
        "min_qty": lot.get("minQty", "0"),
        "step_size": lot.get("stepSize", "0"),
        "min_notional": notional.get("minNotional", "0"),
        "filter_source": "Current Binance public exchangeInfo (historically imperfect)",
    }
