"""Public Binance archive loader for lab4 research. GET-only, no credentials.

Daily spot klines and USD-M perpetual funding rates come from the public
monthly archives at data.binance.vision (with a daily-archive fallback for
the current month). Files are cached under data/lab4/. Nothing here can
place orders.
"""
import csv
import io
import json
import math
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE = "https://data.binance.vision/data"
API = "https://data-api.binance.vision/api/v3/klines"
DAY_MS = 86_400_000
CACHE = Path(__file__).resolve().parents[1] / "data" / "lab4"

# Point-in-time universe: the 20 largest coins by market cap on 2019-01-01
# (CoinMarketCap snapshot), minus stablecoins and coins without a Binance USDT
# spot pair at that time (USDT, BSV, XEM, XMR had no/short Binance USDT history).
# Losers (EOS, IOTA, NEO, DASH, ZEC, WAVES ...) are kept on purpose to limit
# survivorship bias. Chosen BEFORE any lab4 result was computed.
UNIVERSE_2019 = (
    "BTC", "XRP", "ETH", "BCH", "EOS", "XLM", "LTC", "TRX", "ADA", "IOTA",
    "BNB", "DASH", "ETC", "NEO", "ZEC", "WAVES",
)
# Binance renamed some pairs; map research asset -> list of (symbol, from_date).
SYMBOL_ALIASES = {
    "BCH": [("BCHABCUSDT", "2018-11-16"), ("BCHUSDT", "2019-12-01")],
}


def _get(url, retries=4, agent="lab4-research/0.1"):
    req = urllib.request.Request(url, headers={"User-Agent": agent})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)


def _ms(value):
    v = int(float(value))
    # Spot archives switched to microsecond timestamps in 2025.
    return v // 1000 if v > 10 ** 14 else v


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _read_zip_csv(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        text = z.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and not rows[0][0].lstrip("-").isdigit():
        rows = rows[1:]  # header
    return rows


def _cached(path, fetch):
    if path.exists():
        return json.loads(path.read_text())
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    tmp.replace(path)
    return data


INTERVAL_MS = {"1h": 3_600_000, "1d": DAY_MS}


def klines(symbol, start, end, interval="1d", market="spot"):
    """Return {open_ms: (o,h,l,c,quote_volume)} for candles opening in [start, end).

    Only fully closed candles with the exact interval length are kept. Missing
    or interrupted candles are simply absent; callers decide how to treat gaps
    (lab4 never fills them synthetically).
    """
    step = INTERVAL_MS[interval]
    base = f"{ARCHIVE}/spot" if market == "spot" else f"{ARCHIVE}/futures/um"
    out = {}
    today = datetime.now(timezone.utc).date()
    for y, m in _months(start, end):
        key = f"{market}-{symbol}-{interval}-{y}-{m:02d}"
        complete_month = (y, m) < (today.year, today.month)

        def fetch(y=y, m=m):
            blob = _get(f"{base}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{y}-{m:02d}.zip")
            return _read_zip_csv(blob) if blob else []

        if complete_month:
            rows = _cached(CACHE / f"{key}.json", fetch)
        else:
            rows = _api_month(symbol, y, m, interval) if market == "spot" else []
        for r in rows:
            t, close_t = _ms(r[0]), _ms(r[6])
            if close_t != t + step - 1:
                continue  # interrupted / partial candle: drop, never patch
            o, h, l, c, qv = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
            if not all(math.isfinite(x) and x > 0 for x in (o, h, l, c)):
                continue
            if not (l <= min(o, c) <= max(o, c) <= h):
                continue
            out[t] = (o, h, l, c, qv)
    s, e = _ms_of(start), _ms_of(end)
    return {t: v for t, v in out.items() if s <= t < e}


def daily_klines(symbol, start, end, market="spot"):
    return klines(symbol, start, end, "1d", market)


def _api_month(symbol, y, m, interval="1d"):
    """Current, still incomplete month: page the public REST API."""
    cursor = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    rows = []
    while cursor < now_ms:
        blob = _get(f"{API}?symbol={symbol}&interval={interval}&startTime={cursor}&limit=1000")
        page = json.loads(blob) if blob else []
        if not page:
            break
        rows.extend(page)
        nxt = int(page[-1][0]) + INTERVAL_MS[interval]
        if nxt <= cursor or len(page) < 1000:
            break
        cursor = nxt
    # Drop the still-forming candle: only candles closed before "now".
    return [r for r in rows if int(r[6]) < now_ms]


def funding_rates(symbol, start, end):
    """Return sorted [(funding_time_ms, rate)] for a USD-M perpetual."""
    out = []
    today = datetime.now(timezone.utc).date()
    for y, m in _months(start, end):
        if (y, m) >= (today.year, today.month):
            continue
        key = f"funding-{symbol}-{y}-{m:02d}"

        def fetch(y=y, m=m):
            blob = _get(f"{ARCHIVE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip")
            return _read_zip_csv(blob) if blob else []

        for r in _cached(CACHE / f"{key}.json", fetch):
            out.append((_ms(r[0]), float(r[2])))
    s, e = _ms_of(start), _ms_of(end)
    return sorted(x for x in out if s <= x[0] < e)


def _ms_of(d):
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def asset_daily(asset, start, end):
    """Daily USDT spot candles for a research asset, following pair renames."""
    aliases = SYMBOL_ALIASES.get(asset, [(asset + "USDT", "2017-01-01")])
    merged = {}
    for i, (sym, since) in enumerate(aliases):
        s = max(start, datetime.strptime(since, "%Y-%m-%d").date())
        e = end
        if i + 1 < len(aliases):
            e = min(end, datetime.strptime(aliases[i + 1][1], "%Y-%m-%d").date())
        if s < e:
            merged.update(daily_klines(sym, s, e))
    return merged


SPY_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/SPY?period1={p1}&period2={p2}"
           "&interval=1d&events=div%2Csplit&includeAdjustedClose=true")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500"


def spy_total_return(start, end):
    """SPY dividend-adjusted closes {date: adjclose}: the realistic S&P 500
    alternative an investor could have bought (includes the ETF fee)."""
    path = CACHE / f"spy-{start}-{end}.json"

    def fetch():
        p1 = _ms_of(start) // 1000
        p2 = _ms_of(end) // 1000
        req = urllib.request.Request(SPY_URL.format(p1=p1, p2=p2), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)["chart"]["result"][0]
        adj = res["indicators"]["adjclose"][0]["adjclose"]
        out = {}
        for ts, a in zip(res["timestamp"], adj):
            if a is not None:
                out[datetime.fromtimestamp(ts, timezone.utc).date().isoformat()] = a
        return out

    return _cached(path, fetch)


def fred_sp500():
    """S&P 500 price index (no dividends) from FRED, used only as a cross-check."""
    path = CACHE / "fred-sp500.json"

    def fetch():
        blob = _get(FRED_URL, agent="Mozilla/5.0")
        out = {}
        for row in list(csv.reader(io.StringIO(blob.decode())))[1:]:
            if len(row) == 2 and row[1] not in ("", "."):
                out[row[0]] = float(row[1])
        return out

    return _cached(path, fetch)
