"""Causal feature library for lab4 pattern research (pure stdlib).

Every feature value at index t uses only candles that CLOSED on or before t:
prices, ranges, quote volume, number of trades and taker-buy quote volume
(an order-flow proxy: aggressive buyers vs aggressive sellers). Historical
L2 order books are not publicly available for 2018-2022, so they are not used.
"""
import math

from . import data

DAY_MS = 86_400_000


# -- data --------------------------------------------------------------------

def full_klines(symbol, start, end):
    """{open_ms: (o, h, l, c, quote_vol, trades, taker_buy_quote_vol)} from the
    same public archives/cache as lab4.data, closed candles only."""
    out = {}
    today = data.datetime.now(data.timezone.utc).date()
    for y, m in data._months(start, end):
        complete = (y, m) < (today.year, today.month)
        if complete:
            path = data.CACHE / f"spot-{symbol}-1d-{y}-{m:02d}.json"

            def fetch(y=y, m=m):
                blob = data._get(f"{data.ARCHIVE}/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{y}-{m:02d}.zip")
                return data._read_zip_csv(blob) if blob else []

            rows = data._cached(path, fetch)
            if not rows:
                path.unlink(missing_ok=True)
                rows = data._api_month(symbol, y, m, "1d")
        else:
            rows = data._api_month(symbol, y, m, "1d")
        for r in rows:
            t, close_t = data._ms(r[0]), data._ms(r[6])
            if close_t != t + DAY_MS - 1:
                continue
            o, h, l, c = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            if not (0 < l <= min(o, c) <= max(o, c) <= h):
                continue
            out[t] = (o, h, l, c, float(r[7]), float(r[8]), float(r[10]))
    s, e = data._ms_of(start), data._ms_of(end)
    return {t: v for t, v in out.items() if s <= t < e}


def asset_full(asset, start, end):
    aliases = data.SYMBOL_ALIASES.get(asset, [(asset + "USDT", "2017-01-01")])
    merged = {}
    for i, (sym, since) in enumerate(aliases):
        s = max(start, data.datetime.strptime(since, "%Y-%m-%d").date())
        e = end
        if i + 1 < len(aliases):
            e = min(end, data.datetime.strptime(aliases[i + 1][1], "%Y-%m-%d").date())
        if s < e:
            merged.update(full_klines(sym, s, e))
    return merged


def attach_orderflow(panel, start, end):
    """Adds panel.trades[a] and panel.taker[a] (taker-buy quote volume)."""
    panel.trades, panel.taker = {}, {}
    for a in panel.assets:
        rows = asset_full(a, start, end)
        tr, tk = [None] * panel.n, [None] * panel.n
        for t, v in rows.items():
            k = (t - panel.start_ms) // DAY_MS
            if 0 <= k < panel.n:
                tr[k], tk[k] = v[5], v[6]
        panel.trades[a], panel.taker[a] = tr, tk


# -- helpers -----------------------------------------------------------------

def _window(xs, t, n):
    if t - n + 1 < 0:
        return None
    w = xs[t - n + 1:t + 1]
    return None if any(v is None for v in w) else w


def _mean(w):
    return sum(w) / len(w)


def _std(w):
    m = _mean(w)
    return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) if len(w) > 1 else 0.0


def _ema(xs, n):
    out, e, run = [None] * len(xs), None, 0
    a = 2.0 / (n + 1)
    for i, x in enumerate(xs):
        if x is None:
            e, run = None, 0
            continue
        run += 1
        if e is None:
            e = x
        else:
            e = a * x + (1 - a) * e
        if run >= n:
            out[i] = e
    return out


def _rsi(c, n):
    out, g, l = [None] * len(c), None, None
    for i in range(1, len(c)):
        if c[i] is None or c[i - 1] is None:
            g = l = None
            continue
        ch = c[i] - c[i - 1]
        up, dn = max(ch, 0.0), max(-ch, 0.0)
        if g is None:
            g, l, cnt = up, dn, 1
        else:
            g, l = (g * (n - 1) + up) / n, (l * (n - 1) + dn) / n
            cnt += 1
        if cnt >= n:
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


# -- feature computation -------------------------------------------------------

FEATURES = (
    "ret_1", "ret_3", "ret_7", "ret_14", "ret_28", "ret_56", "ret_90", "ret_28_skip7",
    "dist_sma20", "dist_sma50", "dist_sma200", "sma50_200",
    "macd_hist", "macd_line", "rsi_2", "rsi_14", "stoch_14", "bb_pctb_20", "donchian_55",
    "high_365_prox", "drawdown_90", "eff_ratio_20",
    "vol_30", "vol_ratio_7_30", "atr_14", "max_ret_30", "min_ret_30", "skew_30", "range_1",
    "qv_ratio_7_90", "log_qv_30", "taker_ratio_1", "taker_ratio_7", "taker_imb_7_90",
    "trades_ratio_7_90", "trade_size_ratio_7_90", "mfi_14", "obv_flow_20",
    "beta_btc_90", "corr_btc_90", "idio_vol_90",
)
MARKET_RELATIVE = ("beta_btc_90", "corr_btc_90", "idio_vol_90")


def asset_features(p, a, btc_r1=None):
    """dict feature -> list[n] for one asset (None where unavailable)."""
    n = p.n
    o, h, l, c, qv = p.o[a], p.h[a], p.l[a], p.c[a], p.qv[a]
    tr = getattr(p, "trades", {}).get(a, [None] * n)
    tk = getattr(p, "taker", {}).get(a, [None] * n)
    F = {f: [None] * n for f in FEATURES}
    r1 = [None] + [c[i] / c[i - 1] - 1 if c[i] is not None and c[i - 1] is not None else None
                   for i in range(1, n)]
    ema12, ema26 = _ema(c, 12), _ema(c, 26)
    macd = [x - y if x is not None and y is not None else None for x, y in zip(ema12, ema26)]
    sig = _ema(macd, 9)
    rsi2, rsi14 = _rsi(c, 2), _rsi(c, 14)
    tr_range = [None] + [max(h[i], c[i - 1]) - min(l[i], c[i - 1])
                         if None not in (h[i], l[i], c[i - 1]) else None for i in range(1, n)]
    for t in range(n):
        if c[t] is None:
            continue

        def ret(k, end=t):
            return c[end] / c[end - k] - 1 if end - k >= 0 and c[end - k] is not None and c[end] is not None else None

        for k in (1, 3, 7, 14, 28, 56, 90):
            F[f"ret_{k}"][t] = ret(k)
        if t - 7 >= 0 and c[t - 7] is not None:
            F["ret_28_skip7"][t] = ret(21, t - 7)
        for k in (20, 50, 200):
            w = _window(c, t, k)
            if w:
                F[f"dist_sma{k}"][t] = c[t] / _mean(w) - 1
        w50, w200 = _window(c, t, 50), _window(c, t, 200)
        if w50 and w200:
            F["sma50_200"][t] = _mean(w50) / _mean(w200) - 1
        if macd[t] is not None and sig[t] is not None:
            F["macd_hist"][t] = (macd[t] - sig[t]) / c[t]
        if macd[t] is not None:
            F["macd_line"][t] = macd[t] / c[t]
        F["rsi_2"][t], F["rsi_14"][t] = rsi2[t], rsi14[t]
        wh, wl = _window(h, t, 14), _window(l, t, 14)
        if wh and wl and max(wh) > min(wl):
            F["stoch_14"][t] = (c[t] - min(wl)) / (max(wh) - min(wl))
        w20 = _window(c, t, 20)
        if w20:
            sd = _std(w20)
            if sd > 0:
                F["bb_pctb_20"][t] = (c[t] - (_mean(w20) - 2 * sd)) / (4 * sd)
            moves = sum(abs(w20[i] - w20[i - 1]) for i in range(1, 20))
            if moves > 0:
                F["eff_ratio_20"][t] = abs(w20[-1] - w20[0]) / moves
        wh, wl = _window(h, t, 55), _window(l, t, 55)
        if wh and wl and max(wh) > min(wl):
            F["donchian_55"][t] = (c[t] - min(wl)) / (max(wh) - min(wl))
        wh = _window(h, t, 365)
        if wh:
            F["high_365_prox"][t] = c[t] / max(wh)
        wh = _window(h, t, 90)
        if wh:
            F["drawdown_90"][t] = c[t] / max(wh) - 1
        wr = _window(r1, t, 30)
        if wr:
            F["vol_30"][t] = _std(wr) * math.sqrt(365)
            F["max_ret_30"][t], F["min_ret_30"][t] = max(wr), min(wr)
            sd = _std(wr)
            if sd > 0:
                m = _mean(wr)
                F["skew_30"][t] = sum(((x - m) / sd) ** 3 for x in wr) / 30
            w7 = wr[-7:]
            sd7 = _std(w7)
            if sd > 0:
                F["vol_ratio_7_30"][t] = sd7 / sd
        wt = _window(tr_range, t, 14)
        if wt:
            F["atr_14"][t] = _mean(wt) / c[t]
        if h[t] is not None and l[t] is not None:
            F["range_1"][t] = (h[t] - l[t]) / c[t]
        q7, q30, q90 = _window(qv, t, 7), _window(qv, t, 30), _window(qv, t, 90)
        if q7 and q90 and sum(q90) > 0:
            F["qv_ratio_7_90"][t] = _mean(q7) / _mean(q90)
        if q30 and sum(q30) > 0:
            F["log_qv_30"][t] = math.log(_mean(q30))
        k1, k7, k90 = _window(tk, t, 1), _window(tk, t, 7), _window(tk, t, 90)
        if k1 and qv[t]:
            F["taker_ratio_1"][t] = k1[0] / qv[t]
        if k7 and q7 and sum(q7) > 0:
            F["taker_ratio_7"][t] = sum(k7) / sum(q7)
            if k90 and q90 and sum(q90) > 0:
                F["taker_imb_7_90"][t] = sum(k7) / sum(q7) - sum(k90) / sum(q90)
        t7, t90 = _window(tr, t, 7), _window(tr, t, 90)
        if t7 and t90 and sum(t90) > 0 and sum(t7) > 0 and q7 and q90:
            F["trades_ratio_7_90"][t] = _mean(t7) / _mean(t90)
            F["trade_size_ratio_7_90"][t] = (sum(q7) / sum(t7)) / (sum(q90) / sum(t90))
        if t >= 15:
            tp = [(h[i] + l[i] + c[i]) / 3 if None not in (h[i], l[i], c[i]) else None for i in range(t - 14, t + 1)]
            vv = qv[t - 13:t + 1]
            if None not in tp and None not in vv:
                pos = sum(vv[i] for i in range(14) if tp[i + 1] > tp[i])
                neg = sum(vv[i] for i in range(14) if tp[i + 1] < tp[i])
                F["mfi_14"][t] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
        wr20, wq20 = _window(r1, t, 20), _window(qv, t, 20)
        if wr20 and wq20 and sum(wq20) > 0:
            F["obv_flow_20"][t] = sum(q * (1 if r > 0 else -1 if r < 0 else 0)
                                      for r, q in zip(wr20, wq20)) / sum(wq20)
        if btc_r1 is not None:
            wa, wb = _window(r1, t, 90), _window(btc_r1, t, 90)
            if wa and wb:
                ma, mb = _mean(wa), _mean(wb)
                cov = sum((x - ma) * (y - mb) for x, y in zip(wa, wb))
                vb = sum((y - mb) ** 2 for y in wb)
                va = sum((x - ma) ** 2 for x in wa)
                if vb > 0 and va > 0:
                    beta = cov / vb
                    F["beta_btc_90"][t] = beta
                    F["corr_btc_90"][t] = cov / math.sqrt(va * vb)
                    resid = [x - ma - beta * (y - mb) for x, y in zip(wa, wb)]
                    F["idio_vol_90"][t] = _std(resid) * math.sqrt(365)
    return F, r1


def all_features(p):
    """dict asset -> dict feature -> list. Memoised on the panel."""
    def build():
        btc_f, btc_r1 = asset_features(p, "BTC")
        out = {"BTC": btc_f}
        for a in p.assets:
            if a != "BTC":
                out[a] = asset_features(p, a, btc_r1)[0]
        return out
    return p.memo(("features",), build)
