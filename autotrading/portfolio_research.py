"""Cash-conserving daily spot portfolio research. Decisions at close, later opens.

No credentials, exchange orders, shorting, funding, or leverage. Daily close
marked drawdown understates intraday drawdown. Quantity filters are fixed
research assumptions, not reconstructed point-in-time exchange rules.
"""
from dataclasses import dataclass
from math import isfinite, sqrt, prod, log
from statistics import mean, pstdev
import random

from .research_data import DAY, SYMBOLS, validate_daily


@dataclass(frozen=True)
class PortfolioConfig:
    capital: float = 5000.0
    cap: float = 0.20
    fee_bps: float = 10.0
    impact_bps: float = 6.0  # adverse slippage plus half-spread, each side
    delay_days: int = 1
    rebalance_band: float = 0.01
    min_notional: float = 5.0

    def validate(self):
        if not all(isfinite(x) for x in (self.capital,self.cap,self.fee_bps,self.impact_bps,self.rebalance_band,self.min_notional)):
            raise ValueError('Nonfinite execution configuration')
        if not (self.capital>0 and 0<self.cap<=1 and 0<=self.fee_bps<=100 and 0<=self.impact_bps<=100 and 0<=self.rebalance_band<=self.cap and self.min_notional>=0):
            raise ValueError('Invalid execution configuration')
        if not isinstance(self.delay_days,int) or not 1<=self.delay_days<=5:
            raise ValueError('Delay must be 1..5 full daily bars')


def check_series(series):
    if not series:
        raise ValueError('Empty universe')
    if set(series) - SYMBOLS:
        raise ValueError('Unsupported research instrument')
    if len({symbol[-4:] for symbol in series}) != 1:
        raise ValueError('Shared cash requires a single quote currency; no implicit conversion')
    timestamps = None
    for symbol, bars in series.items():
        if not bars:
            raise ValueError('Empty series')
        validate_daily(bars,bars[0].open_time_ms,bars[-1].close_time_ms+1)
        times = [b.open_time_ms for b in bars]
        if timestamps is not None and times!=timestamps:
            raise ValueError('Unaligned multi-asset data; never fill missing bars')
        timestamps=times
    return len(timestamps)


def simulate_portfolio(series, targets, first, stop=None, cfg=PortfolioConfig(), buy_hold=False):
    cfg.validate()
    n=check_series(series)
    stop=n if stop is None else stop
    if len(targets)!=n or not cfg.delay_days<=first<stop<=n:
        raise ValueError('Invalid target length or simulation range')
    symbols=sorted(series)
    for row in targets[:stop]:
        if set(row)-set(symbols) or any(not isfinite(v) or v<0 for v in row.values()) or sum(row.values())>cfg.cap+1e-10:
            raise ValueError('Invalid target weights')
    fee,impact=cfg.fee_bps/10000,cfg.impact_bps/10000
    cash=cfg.capital
    qty={s:0.0 for s in symbols}
    ledger,curve=[],[]
    peak=cfg.capital
    dd=fees=turnover=exposure_sum=0.0
    peak_exposure=0.0
    previous=cfg.capital
    returns=[]
    for i in range(first,stop):
        prices={s:series[s][i].open for s in symbols}
        equity=cash+sum(qty[s]*prices[s] for s in symbols)
        target=targets[i-cfg.delay_days]
        desired={s:equity*target.get(s,0)/prices[s] for s in symbols}
        if buy_hold and i>first:
            desired=dict(qty)
        for side in ('SELL','BUY'):
            orders=[]
            for s in symbols:
                difference=desired[s]-qty[s]
                if side=='SELL' and difference>=0 or side=='BUY' and difference<=0:
                    continue
                # An explicit zero target exits even inside the rebalance band.
                if abs(difference)*prices[s]/equity<cfg.rebalance_band and target.get(s,0)>0:
                    continue
                orders.append((s,abs(difference)))
            required=sum(q*prices[s]*(1+impact)*(1+fee) for s,q in orders)
            scale=min(1,cash/required) if side=='BUY' and required else 1
            for s,q in orders:
                q=q*scale if side=='BUY' else min(q,qty[s])
                # Explicit fixed step assumptions; cash tiny residuals retained.
                step=0.00001 if s.startswith('BTC') else 0.0001
                q=int((q+1e-12)/step)*step
                px=prices[s]*(1+impact if side=='BUY' else 1-impact)
                gross=q*px
                if q<=0 or gross<cfg.min_notional:
                    continue
                cost=gross*fee
                if side=='BUY':
                    cash-=gross+cost
                    qty[s]+=q
                else:
                    cash+=gross-cost
                    qty[s]-=q
                fees+=cost
                turnover+=gross
                ledger.append({'day_index':i,'symbol':s,'side':side,'signal_ms':series[s][i-cfg.delay_days].close_time_ms,
                    'fill_ms':series[s][i].open_time_ms,'quantity':q,'reference_price':prices[s],'price':px,'fee':cost,'notional':gross,'cash_after':cash})
        if cash < -1e-7 or any(q<-1e-10 for q in qty.values()):
            raise AssertionError('Cash/position conservation violated')
        value=sum(qty[s]*series[s][i].close for s in symbols)
        marked=cash+value*(1-impact)*(1-fee)
        peak=max(peak,marked)
        dd=max(dd,1-marked/peak)
        exposure=value/(cash+value)
        exposure_sum+=exposure
        peak_exposure=max(peak_exposure,exposure)
        returns.append(marked/previous-1)
        previous=marked
        curve.append({'open_ms':series[symbols[0]][i].open_time_ms,'equity':marked,'cash':cash,'gross_position':value})
    # Independent cash ledger replay: no trust in reported totals.
    check_cash=cfg.capital
    check_qty={s:0.0 for s in symbols}
    for e in ledger:
        sign=1 if e['side']=='BUY' else -1
        check_qty[e['symbol']]+=sign*e['quantity']
        check_cash-=sign*e['notional']+e['fee']
        if e['signal_ms']>=e['fill_ms']:
            raise AssertionError('Signal must precede execution')
    check_end=check_cash+sum(check_qty[s]*series[s][stop-1].close*(1-impact)*(1-fee) for s in symbols)
    if abs(check_end-curve[-1]['equity'])>1e-6 or abs(check_cash-cash)>1e-6:
        raise AssertionError('Final account does not reconcile')
    total=curve[-1]['equity']/cfg.capital-1
    sd=pstdev(returns)
    return {'return_pct':100*total,'cagr_pct':100*((1+total)**(365/len(returns))-1),
        'max_daily_close_drawdown_pct':100*dd,'sharpe':mean(returns)/sd*sqrt(365) if sd>1e-12 else None,
        'fills':len(ledger),'buys':sum(e['side']=='BUY' for e in ledger),'sells':sum(e['side']=='SELL' for e in ledger),
        'fees':fees,'turnover':turnover,'mean_exposure_pct':100*exposure_sum/len(returns),
        'peak_close_exposure_pct':100*peak_exposure,'ending_equity':curve[-1]['equity'],'cash':cash,'positions':qty,
        'equity_curve':curve,'daily_returns':returns,'ledger':ledger,'audit_passed':True}


def compact(result):
    return {k:v for k,v in result.items() if k not in ('equity_curve','daily_returns','ledger','positions')}


def bootstrap_family(returns_by_name, repetitions=1000, block=30, seed=20261106):
    """Circular moving-block bootstrap with common indices, max-t FWER null.

    Exploratory stationarity approximation; not a guarantee, not DSR/PBO.
    Preserves within-block serial and cross-strategy dependence. Family is
    this registered batch only, not all prior research done by anyone.
    """
    names=sorted(returns_by_name)
    n=len(returns_by_name[names[0]])
    if n<2 or repetitions<100 or block<1 or any(len(returns_by_name[s])!=n for s in names):
        raise ValueError('Invalid bootstrap matrix')
    data={s:[log(1+x) for x in returns_by_name[s]] for s in names}
    means={s:mean(data[s]) for s in names}
    rng=random.Random(seed)
    draws={s:[] for s in names}
    for _ in range(repetitions):
        indices=[]
        while len(indices)<n:
            start=rng.randrange(n)
            indices.extend((start+j)%n for j in range(min(block,n-len(indices))))
        for s in names:
            draws[s].append(sum(data[s][j] for j in indices)/n)
    se={s:pstdev(draws[s]) for s in names}
    maxima=[max((draws[s][r]-means[s])/se[s] if se[s]>1e-15 else 0 for s in names) for r in range(repetitions)]
    result={}
    for s in names:
        ordered=sorted(draws[s])
        t=means[s]/se[s] if se[s]>1e-15 else 0
        result[s]={'annualized_log_mean_ci95_pct':[36500*ordered[int(.025*repetitions)],36500*ordered[min(repetitions-1,int(.975*repetitions))]],
            'familywise_p_positive_mean':(1+sum(v>=t for v in maxima))/(repetitions+1),
            'block_days':block,'resamples':repetitions,'family_size':len(names)}
    return result
