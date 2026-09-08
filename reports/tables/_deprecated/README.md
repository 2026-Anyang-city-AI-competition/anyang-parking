# 라우터 폐기 기록 (Router Deprecation Rationale)

## 폐기 사유 (정본 §8.7 확정)
5-fold rolling origin 평가결과 (`u9_rolling_origin_simple.csv`), ML이 **전 fold × 전 horizon × 전 구간 (40/40 셀)**에서 persistence를 일률적으로 능가하였다.
기존 검증 기반 하이브리드 라우터는 4개 셀만 살아남았으며 (`outside|we` 15, 30, 60분은 persistence, 120분은 ml), 롤링 오리진 실측 결과 ML이 persistence보다 3.0%~36.1% 뛰어남에도 라우터가 비합리적으로 persistence를 강제하여 모델 성능을 저하시키고 있었다.

따라서 Predictor의 라우팅 분기를 완전 제거하고 항상 ML 예측 (`source="ml"`)을 사용하도록 보정하였다.
persistence는 라우팅 대상이 아니나, 평가 기준선(baseline)으로는 계속 유지된다.
