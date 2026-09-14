"""실시간 값이 움직이는 주차장의 출입조건 조사 우선순위표를 만든다."""
import math
import sqlite3
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/access_survey_priority.md"
# 이름이나 DB 운영시간으로 실제 개폐장·차단기 유무를 단정하지 않는다.
FIRST = {
    10: "평일·주말 모두 00:00~00:00. 현재 영업·일반차량 이용 여부부터 확인",
    16: "안양시청·범계역 주요 데모 후보. 지하 출입과 평일 종료/주말 표기의 의미 확인",
    108: "석수역 강등 데모의 핵심. 주말 일반차량 이용 가능 여부 확인",
    102: "평일·주말 14:22~14:22. 시간값 이상 여부를 운영기관에 확인",
    7: "주말 00:00~18:00. 평일과 다른 시작 시각의 의미 확인",
    15: "대형화물 명칭. 일반 승용차 이용 자격·24시간 표기의 의미 확인",
    112: "임시 주차장. 현재 존치·영업·이용 대상부터 확인",
    31: "시청 데모 대안인 지하 주차장. 야간 입차·출차·주말 규칙 확인",
    12: "석수역 대안. 24시간 표기와 시간제/월정기 이용 조건 확인",
    13: "석수역 대안. 주말 00:00~00:00의 의미와 입출차 확인",
    24: "석수역 대안. 같은 환승 명칭이라도 독립적으로 입출차 확인",
    25: "석수역 대안. 같은 환승 명칭이라도 독립적으로 입출차 확인",
    46: "범계역 실시간 미제공 카드. 주말·야간 출입과 일반 이용 조건 확인",
    106: "시청앞 실시간 미제공 카드. 주말·야간 이용 가능 여부 확인",
    45: "평촌역 실시간 미제공 카드. 주말·야간 이용 가능 여부 확인",
}
DESTS = [(37.394259,126.956861),(37.435093,126.902321),(37.389784,126.950783)]


def distance(r):
    if r['lat'] is None or r['lng'] is None:
        return float('inf')
    def hav(la,lo):
        a,b=math.radians(r['lat']),math.radians(la)
        return 6371000*2*math.asin(math.sqrt(math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(lo-r['lng'])/2)**2))
    return min(hav(*d) for d in DESTS)


def classify(r):
    if r['parking_id'] in FIRST:
        return 1, FIRST[r['parking_id']]
    name=r['name']
    # 혼합/이름 모호함도 출입 제한 확인을 먼저 한다.
    enclosed = r['div']=='노외' or any(s in name for s in ('지하','노외','공영','환승'))
    if enclosed:
        why="별도 부지·지하 또는 혼합 유형 후보. 폐문/차단기·입차와 출차시간 분리 확인"
        if '초교' in name or '복지관' in name:
            why="학교·시설 인접. 일반차량 이용 시간과 행사·휴일 제한 확인"
        if r['wdays_start']=='00:00' and r['wdays_end'] in ('23:59','24:00'):
            why+="; 24시간 표기가 실제 출입에도 적용되는지 확인"
        return 2,why
    return 3,"노상·고가밑 후보. 과금 종료 후 이용, 시간제/월정기 구분, 야간 통제·임시 폐쇄 확인"


def main():
    with sqlite3.connect(f"file:{ROOT/'data/raw/parking.db'}?mode=ro",uri=True) as c:
        c.row_factory=sqlite3.Row
        lots=[dict(r) for r in c.execute('SELECT * FROM lots ORDER BY parking_id')]
        # 전체 수집 구간에서 park_count가 한 번도 바뀌지 않은 피드는
        # 입출차 조건 조사 대상에서 제외한다. 향후 값이 움직이면 자동 재포함된다.
        fixed_ids={r['parking_id'] for r in c.execute(
            'SELECT parking_id FROM obs GROUP BY parking_id '
            'HAVING MIN(park_count) = MAX(park_count)'
        )}
        fixed_values=dict(c.execute(
            'SELECT parking_id, MIN(park_count) FROM obs '
            'GROUP BY parking_id HAVING MIN(park_count) = MAX(park_count)'
        ))
        obs_end=c.execute('SELECT MAX(ts_kst) AS ts FROM obs').fetchone()['ts']
    assert len(lots)==89 and len({r['parking_id'] for r in lots})==89
    fixed_lots=[r for r in lots if r['parking_id'] in fixed_ids]
    lots=[r for r in lots if r['parking_id'] not in fixed_ids]
    first_order={pid:i for i,pid in enumerate(FIRST)}
    lots.sort(key=lambda r:(classify(r)[0],first_order.get(r['parking_id'],999),distance(r),r['parking_id']))
    counts={p:sum(classify(r)[0]==p for r in lots) for p in (1,2,3)}
    out=[f'# 안양 {len(lots)}곳 · 실제 입출차 조건 조사 우선순위','',
         f'작성 기준: 로컬 parking.db lots 89곳 · 관측 종료 `{obs_end}`. 아래 시간은 DB 원문이며 실제 입출차 시간으로 검증된 값이 아니다.',
         f'전체 89곳 중 수집 기간 내 `park_count`가 한 번도 바뀌지 않은 {len(fixed_lots)}곳은 조사 대상에서 제외했다. 값이 움직이는 {len(lots)}곳만 조사한다.',
         f'조사 완료 0/{len(lots)}로 시작하는 체크리스트다. 현재 조사 사실·차단기 유무를 확인했다는 보고가 아니다. 기존 조사 결과가 있으면 근거와 함께 반영한다.','',
         f"P1 즉시 확인 {counts[1]}곳 → P2 출입 제한 확인 우선 {counts[2]}곳 → P3 일반 노상 등 {counts[3]}곳. 총 {sum(counts.values())}곳.",
         '우선순위는 조사 판단이다. P1은 표기 이상·영업/이용대상 불확실성과 핵심 데모를 먼저 배치했다. P2/P3 안에서는 3개 데모 목적지까지의 최소 직선거리가 가까운 순, 동률은 ID순이다.',
         '직선거리는 조사 순서를 정하는 보조값이며 실제 추천 빈도나 방문 최적 동선이 아니다.',
         '같은 시설군을 묶어 문의·방문해도 확인 결과는 parking_id별로 따로 남긴다. 노상/노외만으로 차단기 유무를 결정하지 않는다.','',
         '## 조사 방법','',
         '1. 공식 주차장 안내·최근 공지 검색 → 일반 시간제 차량 기준 입차/출차 시간 확인.',
         f'2. 시간 표기가 모호하거나 출처가 충돌하면 운영기관에 전화·문의. 조사 대상 {len(lots)}곳을 묶어 확인해도 된다.',
         '3. 답변을 얻지 못한 곳·현장과 다른 곳만 방문해 안내판과 출입구 확인. 낮에 차단기가 열려 있다는 사실만으로 야간 개방을 확정하지 않는다.',
         '4. 현재 영업 여부, 시간제/월정기 전용, 차량 종류 제한, 과금 시간, 평일/토/일/공휴일 입차·출차 시간, 야간 주차·임시폐쇄를 구분해 기록한다.',
         '5. 확인 불가는 미확인으로 남긴다. 차단기 없음·입출차 24시간으로 채우지 않는다.','',
         '공통 질문: “일반 시간제 승용차가 이용할 수 있나요? 안내된 운영시간이 끝나도 들어갈 수 있나요? 주차한 차는 언제든 나올 수 있나요? 토요일·일요일·공휴일도 같은가요?”','',
         f'## 조사 대상 {len(lots)}곳','',
         '| 순서 | 급 | ID | 주차장 | DB 구분 | DB 평일 시간 | DB 주말 시간 | 우선 확인 이유 | 확인 |',
         '|---:|---|---:|---|---|---|---|---|---|']
    for i,r in enumerate(lots,1):
        p,why=classify(r)
        out.append(f"| {i} | P{p} | {r['parking_id']} | {r['name']} | {r['div']} | {r['wdays_start']}~{r['wdays_end']} | {r['wend_start']}~{r['wend_end']} | {why} | ⬜ |")
    out+=['',f'## 조사 제외 {len(fixed_lots)}곳 — 데이터 고정','',
          f'관측 종료 `{obs_end}` 기준 전체 수집 구간에서 `park_count`의 최솟값과 최댓값이 같은 곳이다. 출입조건 조사 대상에는 포함하지 않는다.','',
          '| ID | 주차장 | DB 구분 | 고정값 |','|---:|---|---|---:|']
    fixed_lots.sort(key=lambda r:r['parking_id'])
    for r in fixed_lots:
        out.append(f"| {r['parking_id']} | {r['name']} | {r['div']} | {fixed_values[r['parking_id']]} |")
    out+=['','## 주소·검색 바로가기','',
          'DB 주소는 현장 위치를 찾는 단서다. 이름이 바뀌었거나 구역이 나뉜 경우 ID·좌표·주소를 함께 대조한다. 검색 링크는 조사 도구이며 확인된 근거 링크가 아니다.','',
          '| 순서 | ID | 주차장 | DB 주소 | 검색 |','|---:|---:|---|---|---|']
    for i,r in enumerate(lots,1):
        q=quote('안양 '+r['name']+' 주차장 입출차 운영시간')
        addr=str(r.get('addr') or '주소 없음').replace('|','/')
        out.append(f"| {i} | {r['parking_id']} | {r['name']} | {addr} | [검색](https://www.google.com/search?q={q}) |")
    out+=['','## 조사 결과 기록 양식','',
          '아래 양식을 주차장별로 복사한다. 실제 제한이 없으면 명시적으로 24시간, 모르면 미확인이라고 적는다.','',
          '- ID / 주차장명:', '- 확인 날짜 / 방법(공식웹·전화·현장):',
          '- 출처 URL / 안내판 사진 / 답변 기관·부서:',
          '- 현재 영업 여부 / 일반 시간제 승용차 이용 여부:',
          '- 차단기·문 유무:', '- 요금 징수 시간:',
          '- 평일 입차 / 출차 가능 시간:', '- 토요일 입차 / 출차 가능 시간:',
          '- 일요일·공휴일 입차 / 출차 가능 시간:',
          '- 야간 주차·장시간 주차·월정기·차종 제한:',
          '- 임시 폐쇄·공사 / 적용 기간:', '- 충돌 정보·추가 확인 필요 사항:','']
    OUT.write_text('\n'.join(out),encoding='utf-8')
    print(f'89 unique IDs verified; target={len(lots)} fixed_excluded={len(fixed_lots)}; tiers:',counts)
    for i,r in enumerate(lots[:15],1):print(i,r['parking_id'],r['name'])
    print(OUT)


if __name__=='__main__':
    main()
