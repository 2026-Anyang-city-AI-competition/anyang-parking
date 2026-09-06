# a09 · 시간대 Shape interaction LOO

- 평가 후보 주차장: 67곳
- 음식 상권 컬럼: L_I2_음식
- 교육 상권 컬럼: L_P1_교육
- 시간대는 임의 구간이 아니라 24시간 sin/cos 주기로 표현
- 모든 평가는 주차장 전체를 hold-out 하는 LOO

| 모델 | 피처 수 | 평가 주차장 | MAE(%p) | Spearman [95% CI] |
|---|---:|---:|---:|---|
| A08 reference (Level + global Shape) | 11 | 67 | **19.99** | +0.753 [+0.63, +0.84] |
| Row BASE + TIME | 14 | 67 | **20.92** | +0.739 [+0.61, +0.83] |
| + HOUSEHOLDS × TIME | 18 | 67 | **21.02** | +0.740 [+0.61, +0.83] |
| + FOOD/EDU × TIME | 20 | 67 | **20.80** | +0.720 [+0.58, +0.82] |
| + ALL interactions | 24 | 67 | **21.00** | +0.719 [+0.58, +0.82] |

- **Row BASE + TIME**: A08 reference 대비 MAE +0.93%p · Spearman -0.015
- **+ HOUSEHOLDS × TIME**: A08 reference 대비 MAE +1.03%p · Spearman -0.013
- **+ FOOD/EDU × TIME**: A08 reference 대비 MAE +0.81%p · Spearman -0.033
- **+ ALL interactions**: A08 reference 대비 MAE +1.01%p · Spearman -0.034
