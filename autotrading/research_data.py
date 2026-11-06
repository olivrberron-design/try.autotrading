"""Bounded, credential-free daily research data; never splice quote currencies."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .history import public_get
from .market import Candle, parse_closed_candles

DAY = 86_400_000
SYMBOLS = {"BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC"}


def timestamp(date):
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def validate_daily(candles, start, end):
    """Exact [start,end) UTC coverage, finite OHLCV, no synthetic gap filling."""
    if start % DAY or end % DAY or start >= end or len(candles) != (end-start)//DAY:
        raise ValueError("Incomplete or unaligned daily coverage")
    for i, c in enumerate(candles):
        if c.open_time_ms != start+i*DAY or c.close_time_ms != start+(i+1)*DAY-1:
            raise ValueError("Invalid daily candle timestamps")
        if not all(math.isfinite(x) for x in (c.open,c.high,c.low,c.close,c.volume)):
            raise ValueError("Nonfinite daily candle")
        if not (0 < c.low <= min(c.open,c.close) <= max(c.open,c.close) <= c.high and c.volume >= 0):
            raise ValueError("Invalid daily OHLCV")


def load_daily(symbol, start, end, cache_dir, requester=None, now_ms=None):
    if symbol not in SYMBOLS:
        raise ValueError("Unsupported research symbol")
    # Request boundaries are not an observation clock. In particular, tomorrow's
    # end boundary must never turn today's unfinished exchange candle into data.
    observed_ms = int(datetime.now(timezone.utc).timestamp() * 1000) if now_ms is None else now_ms
    if (not isinstance(start, int) or not isinstance(end, int)
            or start % DAY or end % DAY
            or not isinstance(observed_ms, (int, float)) or not math.isfinite(observed_ms)
            or end > observed_ms // DAY * DAY):
        raise ValueError("Daily research requires aligned, already completed UTC days")
    if not 1 <= (end-start)//DAY <= 4000:
        raise ValueError("Daily research request exceeds bound")
    path = Path(cache_dir)/f"{symbol}-daily-{start}-{end}.json"
    if path.exists():
        raw = path.read_bytes()
        data = json.loads(raw)
        if data.get("symbol") != symbol or data.get("start_ms") != start or data.get("end_exclusive_ms") != end or data.get("source") != "https://data-api.binance.vision/api/v3/klines":
            raise ValueError("Invalid daily cache metadata")
        bars = [Candle(**c) for c in data["candles"]]
    else:
        get = requester or public_get
        rows, cursor = [], start
        while cursor < end:
            page = get("/api/v3/klines", {"symbol":symbol,"interval":"1d", "startTime":cursor,"endTime":end-1,"limit":1000})
            if not page:
                raise ValueError("Historical coverage unavailable")
            if int(page[0][0]) != cursor or int(page[-1][0]) < cursor:
                raise ValueError("Missing history or nonadvancing pagination")
            rows.extend(page)
            cursor = int(page[-1][0])+DAY
            if len(rows)>4000:
                raise ValueError("Daily row bound exceeded")
        bars = parse_closed_candles(rows,end)
        validate_daily(bars,start,end)
        data={"source":"https://data-api.binance.vision/api/v3/klines", "symbol":symbol,"start_ms":start,"end_exclusive_ms":end,"candles":[asdict(c) for c in bars]}
        raw = json.dumps(data,separators=(",",":"),allow_nan=False).encode()
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
    validate_daily(bars,start,end)
    return bars, {"symbol":symbol,"source":data["source"],"start_ms":start,"end_exclusive_ms":end,"rows":len(bars),"sha256":hashlib.sha256(raw).hexdigest()}
