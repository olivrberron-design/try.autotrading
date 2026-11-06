#!/usr/bin/env python3
"""Replay all daily hypotheses at UTC midnight and one hour later, on cached hours."""
from dataclasses import asdict,replace
from pathlib import Path
import argparse
import hashlib
import json

from chronological_research import ROOT,PROTOCOL,episode_series,utc
from autotrading.market import Candle
from autotrading.research_data import timestamp
from autotrading.research_strategies import build_targets,WARMUP_BARS
from autotrading.research_models import build_model_targets
from autotrading.portfolio_research import PortfolioConfig,simulate_portfolio,compact
from autotrading.hourly_portfolio import simulate_hourly_targets


def lite(result):
 return {k:v for k,v in result.items() if k not in ('positions','equity_curve_hourly','equity_curve_daily','hourly_returns','daily_returns','ledger')}


def load_hours(symbol,start,end):
 matches=sorted((ROOT/'data/history').glob(f'{symbol}-1h-*.json'))
 for path in matches:
  data=json.loads(path.read_text())
  if data.get('start_ms',start+1)>start or data.get('end_ms',0)<end-1:continue
  if data.get('symbol')!=symbol or data.get('interval')!='1h' or data.get('source')!='https://data-api.binance.vision':raise ValueError('Invalid hourly metadata')
  rows=[Candle(**b) for b in data['candles'] if start<=b['open_time_ms']<end]
  encoded=json.dumps([asdict(b) for b in rows],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
  return rows,{'symbol':symbol,'source':data['source'],'rows':len(rows),'start_ms':start,'end_exclusive_ms':end,'subset_sha256':hashlib.sha256(encoded).hexdigest()}
 raise FileNotFoundError('No complete cached hourly episode for '+symbol)


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'data/hourly-robustness')
 args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 protocol={'purpose':'Execution-resolution robustness, not a new strategy search or independent market sample.','execution_hours_utc':[0,1],'extra_hour_cost_cases':{'base':asdict(PortfolioConfig()),'double':asdict(PortfolioConfig(fee_bps=20,impact_bps=12))},'USDT_subset_start':'2021-12-25','data_exclusion':'2021-12-24 04:00 UTC BTC/ETH hourly candles have shortened close timestamps; USDT replay begins next UTC day.','USDC_start':'2023-11-21','drawdown':'Hourly CLOSE observations, not intrahour worst-case drawdown.'}
 (args.output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
 report={'protocol':protocol,'source_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ('hourly_robustness.py','autotrading/hourly_portfolio.py','chronological_research.py','autotrading/research_strategies.py','autotrading/research_models.py','autotrading/portfolio_research.py','autotrading/research_data.py','autotrading/indicators.py','autotrading/market.py')},'episodes':[]}
 for ep in PROTOCOL['episodes']:
  daily,dm=episode_series(ep)
  targets={**build_targets(daily),**build_model_targets(daily)}
  start=timestamp('2021-12-25') if ep['name'].startswith('USDT') else next(iter(daily.values()))[WARMUP_BARS+1].open_time_ms
  end=timestamp(ep['end_exclusive'])
  hours={};hm=[]
  for s in daily:
   hours[s],m=load_hours(s,start,end);hm.append(m)
  first=next(i for i,b in enumerate(next(iter(daily.values()))) if b.open_time_ms==start)
  day_stop=next((i for i,b in enumerate(next(iter(daily.values()))) if b.open_time_ms==end),len(next(iter(daily.values()))))
  prices_match=all(all(hs[k*24].open==daily[s][first+k].open and hs[k*24+23].close==daily[s][first+k].close for k in range((end-start)//86400000)) for s,hs in hours.items())
  results={};details={}
  for name,t in targets.items():
   reference=simulate_portfolio(daily,t,first,day_stop)
   zero=simulate_hourly_targets(hours,daily,t,start,end,execution_hour=0)
   one=simulate_hourly_targets(hours,daily,t,start,end,execution_hour=1)
   double=simulate_hourly_targets(hours,daily,t,start,end,cfg=PortfolioConfig(fee_bps=20,impact_bps=12),execution_hour=1)
   if prices_match and abs(reference['ending_equity']-zero['ending_equity'])>1e-6:raise AssertionError('Midnight daily/hourly accounting disagreement')
   results[name]={'daily_same_period':compact(reference),'hour0':lite(zero),'hour1':lite(one),'hour1_double':lite(double)}
   # Keep daily marks and exact fills; hourly full curves remain reproducible
   # without publishing a large redundant file for every cost scenario.
   details[name]={key:{'summary':lite(r),'equity_curve_daily':r['equity_curve_daily'],'ledger':r['ledger']} for key,r in (('hour0',zero),('hour1',one),('hour1_double',double))}
   print(ep['name'],name,'daily',round(reference['return_pct'],3),'hour1',round(one['return_pct'],3),'hourDD',round(one['max_hourly_close_drawdown_pct'],3),flush=True)
  report['episodes'].append({'episode':ep['name'],'start_utc':utc(start),'end_exclusive_utc':utc(end),'daily_manifest':dm,'hourly_manifest':hm,'daily_hourly_boundary_prices_identical':prices_match,'results':results})
  (args.output/(ep['name']+'-ledgers.json')).write_text(json.dumps(details,separators=(',',':'),allow_nan=False))
 (args.output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')

if __name__=='__main__':main()
