"""Read-only Binance Demo spot market data. No API keys or order endpoint."""
from dataclasses import dataclass
import json
import urllib.error
import urllib.parse
import urllib.request


DEMO_HOST = "https://demo-api.binance.com"
ALLOWED_PAIRS = frozenset({"BTCUSDC", "ETHUSDC"})
ALLOWED_INTERVALS = frozenset({"15m", "1h"})


@dataclass(frozen=True)
class Candle:
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse_closed_candles(rows, server_time_ms):
    """Discard the still-forming last candle to avoid look-ahead/repainting."""
    candles = []
    for row in rows:
        if len(row) < 7:
            raise ValueError("Incomplete kline")
        candle = Candle(
            open_time_ms=int(row[0]),
            close_time_ms=int(row[6]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        if not (candle.open_time_ms < candle.close_time_ms
                and candle.high >= max(candle.open, candle.close, candle.low)
                and candle.low <= min(candle.open, candle.close, candle.high)
                and candle.low > 0 and candle.volume >= 0):
            raise ValueError("Invalid market candle")
        if candle.close_time_ms >= server_time_ms:
            continue
        if candles and candle.open_time_ms <= candles[-1].open_time_ms:
            raise ValueError("Candles must be chronological and unique")
        candles.append(candle)
    return candles


class MarketData:
    def __init__(self, opener=None):
        self._opener = opener or urllib.request.urlopen

    def _get(self, path, params=None):
        if path not in {"/api/v3/time", "/api/v3/klines"}:
            raise ValueError("Only the approved public market-data endpoints are allowed")
        query = urllib.parse.urlencode(params or {})
        url = DEMO_HOST + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, method="GET")
        try:
            with self._opener(req, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Demo API returned HTTP {exc.code}") from exc

    def candles(self, symbol, interval, limit=300):
        if symbol not in ALLOWED_PAIRS or interval not in ALLOWED_INTERVALS:
            raise ValueError("Unapproved pair or interval")
        if not 210 <= limit <= 1000:
            raise ValueError("Use 210–1000 klines to support indicator warmup")
        server_ms = int(self._get("/api/v3/time")["serverTime"])
        raw = self._get("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "limit": limit
        })
        candles = parse_closed_candles(raw, server_ms)
        if len(candles) < 200:
            raise RuntimeError("Insufficient closed candles for analysis")
        return candles
