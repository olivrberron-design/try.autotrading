#!/usr/bin/env python3
"""Registered daily shared-portfolio research; explicitly excludes old holdouts."""
from dataclasses import asdict,replace
from datetime import datetime,timezone
from pathlib import Path
from statistics import median
import argparse
import csv
import hashlib
import json
import math
import subprocess

from autotrading.market import Candle
from autotrading.research_data import DAY,timestamp,load_daily,validate_daily
from autotrading.research_strategies import build_targets,STRATEGY_SPECS,WARMUP_BARS
from autotrading.research_models import build_model_targets,MODEL_SPECS
from autotrading.portfolio_research import simulate_portfolio,PortfolioConfig,compact,bootstrap_family

ROOT=Path(__file__).resolve().parent
BASELINES={'equal_weight_20','sma200_trend'}
PROTOCOL={
 'version':1,
 'registered_before_results':'2026-11-06',
 'purpose':'Exploratory retrospective discovery; no untouched holdout or forward validation claim.',
 'episodes':[
  {'name':'USDT_2018_2022','symbols':['BTCUSDT','ETHUSDT'],'download_start':'2018-02-10','end_exclusive':'2022-07-01','warmup_bars':240},
  {'name':'USDC_2023_2025','symbols':['BTCUSDC','ETHUSDC'],'download_start':'2023-03-25','end_exclusive':'2026-01-01','warmup_bars':240}],
 'excluded_prior_reserved_intervals_utc':[['2022-07-08T02:00:00+00:00','2022-09-29T02:59:59.999+00:00'],['2026-01-27T06:00:00+00:00','2026-10-20T16:59:59.999+00:00']],
 'data_exclusion_note':'2018-02-08 Binance daily candle has a short close timestamp (1518049694788), so the new contiguous episode starts 2018-02-10. No OHLC/volume filling.',
 'selection':{'train_days':365,'test_days':90,'min_training_fills':12,'positive_training_return':True,'positive_training_excess_vs_rebalanced_equal_weight':True,'ranking':'training Sharpe, then strategy ID','fallback':'cash'},
 'fixed_nonoverlap_stress_days':180,
 'cost_scenarios':{'base':[10,6,1],'double':[20,12,1],'quadruple':[40,24,1],'one_extra_day':[10,6,2]},
 'bootstrap':{'resamples':1000,'block_days':[15,30,60],'seed':20261106,'method':'common-index circular block bootstrap confidence intervals; centered max-t familywise p for this batch'},
 'candidate_gate':{'all_episodes_positive_under_double_costs':True,'positive_180d_fraction_at_least':.60,'minimum_180d_windows':8,'positive_median_180d_excess_vs_rebalanced':True,'max_drawdown_pct_at_most':15,'minimum_fills_total':40,'familywise_p_at_most_each_episode':.05,'positive_log_mean_lower_CI_each_episode':True,'walk_forward_positive_each_episode':True},
 'interpretation':'Passing qualifies only as a provisional historical research candidate. Fresh preregistered forward evidence is still required for a consistent-profit claim.',
}


def utc(ms):
 return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()


def episode_series(ep,allow_download=False):
 series={}; manifests=[]
 for symbol in ep['symbols']:
  start,end=timestamp(ep['download_start']),timestamp(ep['end_exclusive'])
  if symbol.endswith('USDC'):
   matches=sorted((ROOT/'data/history').glob(f'{symbol}-1d-1679702400000-*.json'))
   if not matches:
    if not allow_download: raise FileNotFoundError('USDC local cache absent; use --download')
    bars,manifest=load_daily(symbol,start,end,ROOT/'data/research_daily')
   else:
    # Read only permitted subset; no strategy sees any reserved-period bars.
    payload=json.loads(matches[0].read_text())
    if (payload.get('symbol') != symbol or payload.get('source') != 'https://data-api.binance.vision' or payload.get('interval') != '1d' or payload.get('start_ms') != start or payload.get('end_ms',0) < end-1):
     raise ValueError('Invalid source cache metadata')
    bars=[Candle(**c) for c in payload['candles'] if start<=c['open_time_ms']<end]
    validate_daily(bars,start,end)
    raw=json.dumps([asdict(c) for c in bars],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    manifest={'symbol':symbol,'source':payload['source'],'rows':len(bars),'start_ms':start,'end_exclusive_ms':end,'permitted_subset_sha256':hashlib.sha256(raw).hexdigest()}
  else:
   path=ROOT/'data/research_daily'/f'{symbol}-daily-{start}-{end}.json'
   if not path.exists() and not allow_download: raise FileNotFoundError('Research cache absent; use --download')
   bars,manifest=load_daily(symbol,start,end,ROOT/'data/research_daily')
  series[symbol]=bars; manifests.append(manifest)
 return series,manifests


def walk_forward(series,targets,first,cfg):
 if cfg.delay_days != 1:
  raise ValueError('Walk-forward model selection currently requires next-day execution')
 n=len(next(iter(series.values())))
 test_start=first+365
 stitched=[{} for _ in range(n)]
 folds=[]
 # Include a shortened final fold but identify it; do not discard adverse tails.
 for a in range(test_start,n,90):
  b=min(n,a+90)
  training={name:simulate_portfolio(series,t,a-365,a,cfg) for name,t in targets.items()}
  benchmark=training['equal_weight_20']['return_pct']
  eligible=[name for name,r in training.items() if name not in BASELINES and r['return_pct']>0 and r['return_pct']>benchmark and r['fills']>=12 and r['sharpe'] is not None]
  chosen=max(eligible,key=lambda s:(training[s]['sharpe'],s)) if eligible else 'cash'
  # The model choice at boundary a uses returns through a-1 close, before a open.
  # Assign decisions whose fills are inside this test fold. Continuous account.
  for i in range(a,b):
   stitched[i-cfg.delay_days]=targets[chosen][i-cfg.delay_days] if chosen!='cash' else {}
  folds.append({'training_start':a-365,'training_stop_exclusive':a,'test_start':a,'test_stop_exclusive':b,'test_start_utc':utc(next(iter(series.values()))[a].open_time_ms),'chosen':chosen,'training_scores':{s:compact(r) for s,r in training.items()}})
 if test_start>=n:return {'folds':[],'result':None}
 result=simulate_portfolio(series,stitched,test_start,n,cfg)
 # Fold returns use the uninterrupted account including transition costs.
 for f in folds:
  x=f['test_start']-test_start;y=f['test_stop_exclusive']-test_start
  f['test_return_pct']=100*(math.prod(1+r for r in result['daily_returns'][x:y])-1)
 return {'folds':folds,'result':result}


def run_episode(ep,repetitions=1000,allow_download=False,batch=1):
 series,manifest=episode_series(ep,allow_download)
 targets=build_targets(series)
 if batch>=2:
  targets.update(build_model_targets(series))
 first=WARMUP_BARS+1
 n=len(next(iter(series.values())))
 cfg=PortfolioConfig()
 results={}; detailed={}; rows=[]
 for name,t in targets.items():
  r=simulate_portfolio(series,t,first,n,cfg)
  detailed[name]=r
  results[name]={'base':compact(r)}
  for label,(fee,impact,delay) in PROTOCOL['cost_scenarios'].items():
   if label=='base':continue
   results[name][label]=compact(simulate_portfolio(series,t,first,n,replace(cfg,fee_bps=fee,impact_bps=impact,delay_days=delay)))
  windows=[]
  for a in range(first,n,180):
   b=min(n,a+180)
   # Report partial tails separately; gate counts full 180-day windows only.
   rr=simulate_portfolio(series,t,a,b,cfg)
   bench=simulate_portfolio(series,targets['equal_weight_20'],a,b,cfg)
   windows.append({'start_utc':utc(next(iter(series.values()))[a].open_time_ms),'days':b-a,'return_pct':rr['return_pct'],'excess_vs_rebalanced_pct':rr['return_pct']-bench['return_pct'],'fills':rr['fills']})
  results[name]['windows']=windows
  years={}
  byyear={}
  for p,rday in zip(r['equity_curve'],r['daily_returns']):
   year=utc(p['open_ms'])[:4];byyear.setdefault(year,[]).append(rday)
  for year,rd in byyear.items():years[year]={'days':len(rd),'return_pct':100*(math.prod(1+x for x in rd)-1)}
  results[name]['years']=years
  rows.append({'episode':ep['name'],'strategy':name,**compact(r)})
  print(ep['name'],name,'net',round(r['return_pct'],3),'double',round(results[name]['double']['return_pct'],3),'DD',round(r['max_daily_close_drawdown_pct'],3),flush=True)
 bootstrap={str(block):bootstrap_family({s:r['daily_returns'] for s,r in detailed.items()},repetitions,block) for block in (15,30,60)}
 wf=walk_forward(series,targets,first,cfg)
 wf_stress=walk_forward(series,targets,first,replace(cfg,fee_bps=20,impact_bps=12))
 bh=simulate_portfolio(series,targets['equal_weight_20'],first,n,cfg,buy_hold=True)
 meta={'episode':ep,'data_manifest':manifest,'start_utc':utc(next(iter(series.values()))[first].open_time_ms),'end_utc':utc(next(iter(series.values()))[-1].close_time_ms),'study_days':n-first,'results':results,'bootstrap':bootstrap,'buy_hold_initial_20':compact(bh),'walk_forward':{'folds':wf['folds'],'result':compact(wf['result']) if wf['result'] else None},'walk_forward_double':{'folds':wf_stress['folds'],'result':compact(wf_stress['result']) if wf_stress['result'] else None}}
 return meta,{'fixed_strategies':detailed,'walk_forward':wf,'walk_forward_double':wf_stress},rows


def candidate_gates(episodes):
 gates={}
 for name in episodes[0]['results']:
  fullwindows=[w for ep in episodes for w in ep['results'][name]['windows'] if w['days']==180]
  metrics={
   'positive_double_all':all(ep['results'][name]['double']['return_pct']>0 for ep in episodes),
   'positive_full_windows_fraction':sum(w['return_pct']>0 for w in fullwindows)/len(fullwindows),
   'full_windows':len(fullwindows),
   'median_window_excess':median(w['excess_vs_rebalanced_pct'] for w in fullwindows),
   'worst_drawdown':max(ep['results'][name]['base']['max_daily_close_drawdown_pct'] for ep in episodes),
   'total_fills':sum(ep['results'][name]['base']['fills'] for ep in episodes),
   'all_block_CI_positive':all(ep['bootstrap'][str(b)][name]['annualized_log_mean_ci95_pct'][0]>0 for ep in episodes for b in (15,30,60)),
   'all_block_familywise_p_pass':all(ep['bootstrap'][str(b)][name]['familywise_p_positive_mean']<=.05 for ep in episodes for b in (15,30,60)),
   'walk_forward_positive_all':all(ep['walk_forward']['result'] and ep['walk_forward']['result']['return_pct']>0 for ep in episodes),
  }
  checks={'not_baseline':name not in BASELINES,'positive_double_all':metrics['positive_double_all'],'window_fraction':metrics['positive_full_windows_fraction']>=.6,'window_count':metrics['full_windows']>=8,'median_excess':metrics['median_window_excess']>0,'drawdown':metrics['worst_drawdown']<=15,'fills':metrics['total_fills']>=40,'CI':metrics['all_block_CI_positive'],'FWER':metrics['all_block_familywise_p_pass'],'walk_forward':metrics['walk_forward_positive_all']}
  gates[name]={'metrics':metrics,'checks':checks,'passed':all(checks.values())}
 return gates


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--download',action='store_true')
 parser.add_argument('--batch',type=int,choices=(1,2),default=2)
 parser.add_argument('--output',type=Path,default=ROOT/'data/chronological')
 parser.add_argument('--resamples',type=int,default=1000)
 args=parser.parse_args()
 if args.resamples<1000:parser.error('Use >=1000 bootstrap resamples for reported research')
 args.output.mkdir(parents=True,exist_ok=True)
 specs={**STRATEGY_SPECS,**(MODEL_SPECS if args.batch>=2 else {})}
 protocol={**PROTOCOL,'batch':args.batch,'batch_note':('Batch 2 retains all ten batch-1 hypotheses and adds six fixed models after reviewing batch-1 results. All periods remain discovery. Family correction covers all models in this run.' if args.batch>=2 else 'First fixed batch of ten hypotheses. All periods remain retrospective discovery.'),'bootstrap':{**PROTOCOL['bootstrap'],'resamples':args.resamples},'strategies':specs,'execution':asdict(PortfolioConfig())}
 raw=json.dumps(protocol,indent=2,sort_keys=True,allow_nan=False)
 (args.output/'protocol.json').write_text(raw+'\n')
 sources=['chronological_research.py','autotrading/portfolio_research.py','autotrading/research_data.py','autotrading/research_strategies.py','autotrading/indicators.py','autotrading/history.py','autotrading/market.py','autotrading/research_models.py']
 provenance={'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sources},'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'git_status':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines()}
 report={'provenance':provenance,'protocol':protocol,'protocol_sha256':hashlib.sha256(raw.encode()).hexdigest(),'episodes':[]}
 rows=[]
 for ep in PROTOCOL['episodes']:
  result,details,rs=run_episode(ep,args.resamples,args.download,args.batch)
  report['episodes'].append(result);rows+=rs
  (args.output/(ep['name']+'-ledgers.json')).write_text(json.dumps(details,separators=(',',':'),allow_nan=False))
 report['candidate_gates']=candidate_gates(report['episodes'])
 report['qualified_candidates']=[s for s,g in report['candidate_gates'].items() if g['passed']]
 report['conclusion']='Provisional historical candidates require future validation.' if report['qualified_candidates'] else 'No candidate satisfies the preregistered robustness gates.'
 (args.output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
 with (args.output/'summary.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 print(report['conclusion'],report['qualified_candidates'],flush=True)

if __name__=='__main__':main()
