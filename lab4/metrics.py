"""Performance metrics for lab4 daily return series (pure stdlib)."""
import math
import random
from datetime import date, timedelta


def equity(returns):
    eq, out = 1.0, []
    for r in returns:
        eq *= 1 + r
        out.append(eq)
    return out


def max_drawdown(returns):
    peak, eq, worst = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1 + r
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1)
    return -worst


def cagr(returns, periods_per_year=365):
    if not returns:
        return 0.0
    total = equity(returns)[-1]
    if total <= 0:
        return -1.0
    return total ** (periods_per_year / len(returns)) - 1


def sharpe(returns, periods_per_year=365):
    n = len(returns)
    if n < 2:
        return 0.0
    m = sum(returns) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in returns) / (n - 1))
    return m / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0


def ann_vol(returns, periods_per_year=365):
    n = len(returns)
    if n < 2:
        return 0.0
    m = sum(returns) / n
    return math.sqrt(sum((r - m) ** 2 for r in returns) / (n - 1) * periods_per_year)


def rolling_positive(returns, window=182, step=30):
    """Fraction of full rolling windows with positive compounded return."""
    wins = []
    for s in range(0, len(returns) - window + 1, step):
        g = 1.0
        for r in returns[s:s + window]:
            g *= 1 + r
        wins.append(g - 1)
    if not wins:
        return None, 0, None
    return sum(w > 0 for w in wins) / len(wins), len(wins), min(wins)


def calendar_years(returns, first_day):
    """Compounded return per calendar year (partial years included, labelled)."""
    out = {}
    for i, r in enumerate(returns):
        y = (first_day + timedelta(days=i)).year
        out.setdefault(y, [1.0, 0])
        out[y][0] *= 1 + r
        out[y][1] += 1
    return {str(y): {"return": g - 1, "days": n, "partial": n < 365} for y, (g, n) in out.items()}


def stationary_bootstrap_p05(returns, mean_block=30, draws=2000, seed=20261121):
    """5th percentile of annualised mean daily return under the Politis-Romano
    stationary bootstrap. Exploratory: assumes stationarity."""
    n = len(returns)
    if n < 60:
        return None
    rng = random.Random(seed)
    p = 1.0 / mean_block
    stats = []
    for _ in range(draws):
        i = rng.randrange(n)
        s = 0.0
        for _ in range(n):
            s += returns[i]
            i = rng.randrange(n) if rng.random() < p else (i + 1) % n
        stats.append(s / n * 365)
    stats.sort()
    return stats[int(0.05 * draws)]


def summary(returns, first_day, exposure=None, turnover=None):
    pos_frac, n_win, worst_win = rolling_positive(returns)
    years = calendar_years(returns, first_day)
    out = {
        "days": len(returns),
        "first_day": first_day.isoformat(),
        "total_return": equity(returns)[-1] - 1 if returns else 0.0,
        "cagr": cagr(returns),
        "ann_vol": ann_vol(returns),
        "sharpe": sharpe(returns),
        "max_drawdown": max_drawdown(returns),
        "rolling182_positive": pos_frac,
        "rolling182_windows": n_win,
        "rolling182_worst": worst_win,
        "worst_year": min(v["return"] for v in years.values()) if years else None,
        "years": years,
    }
    out["calmar"] = out["cagr"] / out["max_drawdown"] if out["max_drawdown"] > 0 else None
    if exposure is not None:
        out["avg_exposure"] = sum(exposure) / len(exposure) if exposure else 0.0
    if turnover is not None:
        out["turnover_per_year"] = sum(turnover) / len(turnover) * 365 if turnover else 0.0
    return out


def spy_summary(adjclose, start, end):
    """S&P 500 (SPY total return) over calendar dates [start, end).

    Uses the last close BEFORE ``start`` as the base so the whole period is
    covered. Sharpe/vol annualised with 252 trading days, CAGR on calendar days.
    """
    dates = sorted(adjclose)
    before = [d for d in dates if d < start.isoformat()]
    inside = [d for d in dates if start.isoformat() <= d < end.isoformat()]
    if not before or not inside:
        return None
    seq = [adjclose[before[-1]]] + [adjclose[d] for d in inside]
    rets = [seq[i + 1] / seq[i] - 1 for i in range(len(seq) - 1)]
    days = (end - start).days
    total = seq[-1] / seq[0]
    years = {}
    prev = seq[0]
    for d in inside:
        y = d[:4]
        years.setdefault(y, [prev, None])
        years[y][1] = adjclose[d]
        prev = adjclose[d]
    return {
        "first": inside[0], "last": inside[-1],
        "total_return": total - 1,
        "cagr": total ** (365.0 / days) - 1,
        "ann_vol": ann_vol(rets, 252),
        "sharpe": sharpe(rets, 252),
        "max_drawdown": max_drawdown(rets),
        "years": {y: b / a - 1 for y, (a, b) in years.items()},
    }


def daterange_days(a, b):
    return (b - a).days


def to_date(s):
    return date.fromisoformat(s)
