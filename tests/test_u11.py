import unittest
from unittest.mock import Mock
import numpy as np
import pandas as pd

from src.models.u11_evaluate import coverage_rows, segment_masks, split_frame, FEATURES
from src.report.u11_report import validate_coverage, rank_summary
from src.serve.predictor import Predictor
from src.serve.ranking import rank_cards



class AllowAll:
    """게이트 규칙이 전부 열려 있는 상태."""
    def refresh(self, force=False): return {"rules_loaded": 1}
    def validity_mask(self, pids, obs, targets): return [True]*len(pids)


class BlockAfternoon:
    """12시 이후를 막는 게이트."""
    def refresh(self, force=False): return {"rules_loaded": 1}
    def validity_mask(self, pids, obs, targets):
        return [o.hour < 12 and t.hour < 12 for o, t in zip(obs, targets)]


class EmptyGate:
    """행이 없는 게이트. fail-closed 라 전부 막히므로 마스크를 적용하면 안 된다."""
    def refresh(self, force=False): return {"rules_loaded": 0}
    def validity_mask(self, pids, obs, targets): return [False]*len(pids)

class EvaluationTests(unittest.TestCase):
    def test_query_denominator_not_candidate_count(self):
        q=pd.DataFrame(dict(axis=['walk','walk'],n_candidates=[20,30],changed=[1,0],
             top1_changed=[1,0],a_fail=[1,0],b_fail=[0,0],c_fail=[1,0],rescued=[1,0],
             harmed=[0,0],a_flagged=[1,0],extra_walk=[3,0],extra_fare=[0,0]))
        r=rank_summary(q,['axis']).iloc[0]
        self.assertEqual(r.changed_rate,.5)
        self.assertEqual(r.rescue_rate,1)

    def test_integer_operating_mask_and_aggregate(self):
        p=pd.DataFrame(dict(opr_is_operating=[0,1,1,0],
            target_time=pd.to_datetime(['2026-09-04','2026-09-04','2026-09-05','2026-09-05']),
            nx=[20,50,90,10],p10=[0,0,0,0],p90=[100,60,60,100],p50=[50]*4,occ_now=[50]*4))
        self.assertTrue(all(m.dtype==bool for m in segment_masks(p).values()))
        c=pd.DataFrame(coverage_rows(p,1,15))
        validate_coverage(c)
        self.assertEqual(c.set_index('segment').loc['운영중','coverage'],.5)
        c.loc[c.segment.eq('운영중'),'inside']=2
        with self.assertRaises(AssertionError): validate_coverage(c)

    def _frame(self):
        ts=pd.date_range('2026-09-01',periods=24*12*4,freq='5min')
        t=pd.DataFrame({c:np.ones(len(ts)) for c in FEATURES})
        t['ts_kst']=ts;t['target_time']=ts+pd.Timedelta(minutes=120);t['nx']=0
        t['parking_id']=39
        return t

    def test_split_purges_target_boundary(self):
        a,b,c=split_frame(self._frame(),pd.Timestamp('2026-09-04'),gate=AllowAll())
        self.assertLess(a.target_time.max(),b.ts_kst.min())
        self.assertLess(b.target_time.max(),c.ts_kst.min())

    def test_split_drops_rows_the_service_gate_blocks(self):
        # 서비스가 예측을 막는 시간대는 학습·보정·평가에서도 빠져야 한다.
        t=self._frame()
        full=sum(len(x) for x in split_frame(t,pd.Timestamp('2026-09-04'),gate=AllowAll()))
        gated=sum(len(x) for x in split_frame(t,pd.Timestamp('2026-09-04'),gate=BlockAfternoon()))
        self.assertGreater(full,gated)
        self.assertGreater(gated,0)

    def test_empty_gate_does_not_silently_drop_everything(self):
        t=self._frame()
        kept=sum(len(x) for x in split_frame(t,pd.Timestamp('2026-09-04'),gate=EmptyGate()))
        self.assertEqual(kept,sum(len(x) for x in split_frame(
            t,pd.Timestamp('2026-09-04'),gate=AllowAll())))

    def test_rank_modes_and_fallback(self):
        cards=[dict(parking_id=1,walk_min=1,fare_payg=0,occ_now=20,full_prob=.9),
               dict(parking_id=2,walk_min=2,fare_payg=0,occ_now=20,full_prob=.1)]
        self.assertEqual(rank_cards(cards,'walk',mode='A')[0]['parking_id'],1)
        self.assertEqual(rank_cards(cards,'walk',mode='B')[0]['parking_id'],2)
        self.assertEqual(rank_cards(cards,'walk',mode='C')[0]['parking_id'],1)
        cards[0]['estimated']=True
        self.assertEqual(rank_cards(cards,'walk',mode='B')[0]['parking_id'],1)

    def test_probability_uses_positive_class_and_gate(self):
        p=Predictor.__new__(Predictor);p.ok=True;p.dead=set();p.meta={};p.model_version='test'
        p.feat_cols=['occ_now'];p.full_calibrators={};p.cqr={'15|operating|wd':2}
        p.interval_gate={};p.models={}
        for a,v in ((.1,-5),(.5,0),(.9,5)):
            m=Mock();m.predict.return_value=np.array([v]);p.models[(15,a)]=m
        clf=Mock();clf.predict_proba.return_value=np.array([[.1,.9]]);p.full_models={15:clf}
        p._row=Mock(return_value=(pd.Series({'occ_now':80}),80))
        r=p.predict(1,pd.Timestamp('2026-09-08 12:00'),15)
        self.assertEqual(r['full_prob'],.9)
        self.assertIsNone(r['p10']);self.assertEqual(r['p50'],80)
        p.interval_gate={'15|operating|wd':True}
        r=p.predict(1,pd.Timestamp('2026-09-08 12:00'),15)
        self.assertEqual((r['p10'],r['p90']),(73,87))


if __name__=='__main__':
    unittest.main()
