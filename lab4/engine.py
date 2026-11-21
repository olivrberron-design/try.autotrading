"""Daily weight-based portfolio simulator for lab4 (pure stdlib).

Timing contract (no look-ahead):
  * period k runs from the OPEN of day k to the OPEN of day k+1;
  * the target used in period k is computed by ``strategy(panel, k-1)``,
    i.e. only from candles that CLOSED on or before day k-1;
  * trading costs are charged on the turnover between the drifted weights
    and the new target at the open of day k.
An asset without an open on day k cannot be bought. If it has an open on day
k but none on k+1 (it stopped trading), the period return uses its close on k
and the position is liquidated at the next step.
"""
import math
from datetime import datetime, timezone

DAY_MS = 86_400_000


class Panel:
    """Aligned daily arrays. ``o/h/l/c/qv[asset][k]`` are floats or None."""

    def __init__(self, start_ms, n_days):
        self.start_ms = start_ms
        self.n = n_days
        self.o, self.h, self.l, self.c, self.qv = {}, {}, {}, {}, {}
        self.extra = {}  # pseudo-asset period returns, e.g. carry legs
        self._cache = {}

    def day_ms(self, k):
        return self.start_ms + k * DAY_MS

    def date(self, k):
        return datetime.fromtimestamp(self.day_ms(k) / 1000, timezone.utc).date()

    def index_of(self, d):
        ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        return (ms - self.start_ms) // DAY_MS

    def add_asset(self, name, rows):
        arrs = [[None] * self.n for _ in range(5)]
        for t, vals in rows.items():
            k = (t - self.start_ms) // DAY_MS
            if 0 <= k < self.n:
                for a, v in zip(arrs, vals):
                    a[k] = v
        self.o[name], self.h[name], self.l[name], self.c[name], self.qv[name] = arrs

    @property
    def assets(self):
        return sorted(self.c)

    def memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def age(self, asset):
        """Number of consecutive available closes ending at each day."""
        def build():
            out, run = [0] * self.n, 0
            for k, v in enumerate(self.c[asset]):
                run = run + 1 if v is not None else 0
                out[k] = run
            return out
        return self.memo(("age", asset), build)

    def sma(self, asset, n):
        def build():
            c, out, s = self.c[asset], [None] * self.n, 0.0
            age = self.age(asset)
            for k in range(self.n):
                if c[k] is None:
                    continue
                s += c[k]
                if age[k] > n:
                    s -= c[k - n]
                if age[k] >= n:
                    out[k] = s / n
                if age[k] == 1:
                    s = c[k]
            return out
        return self.memo(("sma", asset, n), build)

    def ret(self, asset, n):
        def build():
            c, out, age = self.c[asset], [None] * self.n, self.age(asset)
            for k in range(self.n):
                if c[k] is not None and age[k] > n:
                    out[k] = c[k] / c[k - n] - 1
            return out
        return self.memo(("ret", asset, n), build)

    def vol(self, asset, n):
        """Annualised close-to-close volatility over n daily returns."""
        def build():
            r1 = self.ret(asset, 1)
            out = [None] * self.n
            for k in range(self.n):
                if self.age(asset)[k] > n:
                    xs = [math.log1p(r1[j]) for j in range(k - n + 1, k + 1)]
                    m = sum(xs) / n
                    out[k] = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) * 365)
            return out
        return self.memo(("vol", asset, n), build)


def period_return(panel, asset, k):
    """Return from open k to open k+1 (or close k if the asset stops)."""
    if asset in panel.extra:
        return panel.extra[asset][k]
    o = panel.o[asset]
    if o[k] is None:
        return None
    if k + 1 < panel.n and o[k + 1] is not None:
        return o[k + 1] / o[k] - 1
    c = panel.c[asset][k]
    return c / o[k] - 1 if c is not None else 0.0


def simulate(panel, strategy, start, end, cost=0.0015, delay=1, band=0.02):
    """Run ``strategy`` over periods [start, end). Returns dict with daily
    returns (net), gross exposure and turnover.

    ``delay`` = 1 means decide at close k-1, trade at open k (registered).
    ``cost`` is per side per unit of traded weight.
    ``band``: an existing position whose target differs by less than ``band``
    (fraction of equity) is left alone, to avoid paying for tiny rebalances.
    Entries from zero and exits to zero always trade.
    """
    w = {}
    rets, expo, turns, orders = [], [], [], []
    for k in range(start, end):
        target = strategy(panel, k - delay) if k - delay >= 0 else {}
        target = {a: x for a, x in target.items() if x > 1e-9}
        for a in list(target):
            if period_return(panel, a, k) is None:
                del target[a]  # cannot trade an asset without an open today
        gross = sum(target.values())
        if gross > 1.0 + 1e-9:
            target = {a: x / gross for a, x in target.items()}
        if band > 0:
            banded = {a: (w[a] if a in w and abs(x - w[a]) < band else x) for a, x in target.items()}
            if sum(banded.values()) <= 1.0 + 1e-9:
                target = banded
        w0 = w
        turnover = sum(abs(target.get(a, 0) - w.get(a, 0)) for a in set(target) | set(w))
        tc = turnover * cost
        r = sum(x * period_return(panel, a, k) for a, x in target.items())
        net = (1 - tc) * (1 + r) - 1
        if 1 + r > 0:
            w = {a: x * (1 + period_return(panel, a, k)) / (1 + r) for a, x in target.items()}
        else:
            w = {}
        rets.append(net)
        expo.append(sum(target.values()))
        turns.append(turnover)
        orders.append(sum(1 for a in set(target) | set(w0) if abs(target.get(a, 0) - w0.get(a, 0)) > 1e-9))
    return {"returns": rets, "exposure": expo, "turnover": turns, "orders": orders,
            "start": start, "end": end}
