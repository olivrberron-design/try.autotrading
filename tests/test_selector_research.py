from dataclasses import replace
import unittest
from tests.test_chronological_research import fixture
from selector_research import build_selectors,training_quarter_returns

class SelectorResearchTests(unittest.TestCase):
 def test_quarter_filter_includes_final_training_day_loss(self):
  returns=[0.0]*365
  for i,r in ((0,.01),(91,.01),(182,-.01),(273,.01),(364,-.05)):returns[i]=r
  quarters=training_quarter_returns(returns)
  self.assertEqual(sum(q>0 for q in quarters),2)
  self.assertLess(quarters[-1],0)

 def test_model_weights_and_targets_are_bounded(self):
  series,targets,cfg=fixture()
  streams,folds=build_selectors(series,targets,1,cfg)
  for f in folds:
   for weights in f['weights'].values():self.assertLessEqual(sum(weights.values()),1+1e-10)
  for rows in streams.values():
   for row in rows:self.assertLessEqual(sum(row.values()),.2+1e-10)
  self.assertEqual(folds[0]['weights']['uniform_fixed_family'],{'alpha':.5,'loser':.5})

 def test_future_prices_cannot_affect_earlier_model_weights(self):
  series,targets,cfg=fixture()
  before=build_selectors(series,targets,1,cfg)[1]
  changed={s:[replace(b,open=b.open*8,high=b.high*8,low=b.low*8,close=b.close*8) if i>=500 else b for i,b in enumerate(rows)] for s,rows in series.items()}
  after=build_selectors(changed,targets,1,cfg)[1]
  self.assertEqual(before[:2],after[:2])

if __name__=='__main__':unittest.main()
