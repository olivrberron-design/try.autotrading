#!/usr/bin/env python3
"""A fixed third research batch: ensembles instead of selecting one past winner.

Runs only earlier permitted discovery episodes. Never replaces or relaxes the
previous candidate gates. Comparisons use the common selector test interval.
"""
from pathlib import Path
from dataclasses import asdict
from statistics import median,pstdev
import argparse
import hashlib
import json
import math

from chronological_research import PROTOCOL,episode_series,utc,BASELINES
from autotrading.research_strategies import build_targets,WARMUP_BARS
from autotrading.research_models import build_model_targets
from autotrading.portfolio_research import simulate_portfolio,PortfolioConfig,compact,bootstrap_family

SELECTOR_SPECS={
 'uniform_fixed_family':'Equal weight across all14 nonbaseline hypotheses, including losing hypotheses; no past-performance ranking.',
 'equal_positive_train':'Equal weight among hypotheses with positive365-day training net return, >=12 fills and <=15% daily-close drawdown; otherwise cash.',
 'inverse_risk_positive_train':'Same eligibility; inverse training daily-return volatility weights, weight ratio limited10:1; otherwise cash.',
 'quarter_consistent_train':'Equal weights if >=3 of the4 consecutive ~91-day training subperiods have positive compounded return, training return positive, >=12 fills and <=15% drawdown; otherwise cash.',
}


def training_quarter_returns(returns):
 if len(returns)!=365:raise ValueError('Expected365 training daily returns')
 return [math.prod(1+x for x in returns[a:b])-1 for a,b in ((0,91),(91,182),(182,273),(273,365))]


def build_selectors(series,targets,first,cfg=PortfolioConfig()):
 n=len(next(iter(series.values())))
 names=[s for s in targets if s not in BASELINES]
 streams={s:[{} for _ in range(n)] for s in SELECTOR_SPECS}
 folds=[]
 for a in range(first+365,n,90):
  b=min(a+90,n)
  training={name:simulate_portfolio(series,targets[name],a-365,a,cfg) for name in names}
  eligible=[s for s,r in training.items() if r['return_pct']>0 and r['fills']>=12 and r['max_daily_close_drawdown_pct']<=15]
  consistent=[]
  for s in eligible:
   rd=training[s]['daily_returns']
   qs=training_quarter_returns(rd)
   if sum(q>0 for q in qs)>=3:consistent.append(s)
  inverse={s:1/max(pstdev(training[s]['daily_returns']),.0001) for s in eligible}
  if inverse:
   floor=min(inverse.values())
   inverse={s:min(v,10*floor) for s,v in inverse.items()}
  choices={
   'uniform_fixed_family':{s:1/len(names) for s in names},
   'equal_positive_train':{s:1/len(eligible) for s in eligible},
   'inverse_risk_positive_train':{s:v/sum(inverse.values()) for s,v in inverse.items()},
   'quarter_consistent_train':{s:1/len(consistent) for s in consistent},
  }
  for method,weights in choices.items():
   for i in range(a,b):
    day={}
    for model,w in weights.items():
     for symbol,exposure in targets[model][i-1].items():day[symbol]=day.get(symbol,0)+w*exposure
    streams[method][i-1]=day
  folds.append({'training_start':a-365,'training_stop_exclusive':a,'test_start':a,'test_stop_exclusive':b,'weights':choices})
 return streams,folds


def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--output',type=Path,default=Path('data/selectors-v3'))
 args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
 protocol={'batch':3,'exploratory_after_batches':[1,2],'selection_specs':SELECTOR_SPECS,'train_days':365,'test_days':90,'costs':asdict(PortfolioConfig()),'episodes':PROTOCOL['episodes'],'interpretation':'Never overrides earlier failed gates. No independent forward-validation claim.'}
 (args.output/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
 report={'protocol':protocol,'source_sha256':{f:hashlib.sha256((Path(__file__).resolve().parent/f).read_bytes()).hexdigest() for f in ('selector_research.py','chronological_research.py','autotrading/portfolio_research.py','autotrading/research_strategies.py','autotrading/research_models.py','autotrading/research_data.py','autotrading/indicators.py','autotrading/market.py')},'episodes':[]}
 for ep in PROTOCOL['episodes']:
  series,manifest=episode_series(ep)
  targets={**build_targets(series),**build_model_targets(series)}
  first=WARMUP_BARS+1
  streams,folds=build_selectors(series,targets,first)
  start=first+365
  outcomes={s:simulate_portfolio(series,t,start,cfg=PortfolioConfig()) for s,t in streams.items()}
  stress_streams,stress_folds=build_selectors(series,targets,first,PortfolioConfig(fee_bps=20,impact_bps=12))
  stressed={s:simulate_portfolio(series,t,start,cfg=PortfolioConfig(fee_bps=20,impact_bps=12)) for s,t in stress_streams.items()}
  # Include all earlier14 nonbaseline hypotheses and equal-weight control in
  # the same-period comparison family, not just this new batch's four models.
  references={s:simulate_portfolio(series,t,start) for s,t in targets.items()}
  all_returns={**{s:r['daily_returns'] for s,r in references.items()},**{s:r['daily_returns'] for s,r in outcomes.items()}}
  inference=bootstrap_family(all_returns,repetitions=1000,block=30)
  result={'episode':ep['name'],'start_utc':utc(next(iter(series.values()))[start].open_time_ms),'data_manifest':manifest,'folds':folds,'double_cost_folds':stress_folds,'results':{s:{'base':compact(r),'double':compact(stressed[s]),'bootstrap30':inference[s]} for s,r in outcomes.items()},'comparison_family':{s:compact(r) for s,r in references.items()},'family_size':len(all_returns)}
  report['episodes'].append(result)
  (args.output/(ep['name']+'-ledgers.json')).write_text(json.dumps({'base':outcomes,'double':stressed},separators=(',',':'),allow_nan=False))
  for s,r in outcomes.items():print(ep['name'],s,'net',round(r['return_pct'],3),'double',round(stressed[s]['return_pct'],3),'familyP',inference[s]['familywise_p_positive_mean'],flush=True)
 (args.output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')

if __name__=='__main__':main()
