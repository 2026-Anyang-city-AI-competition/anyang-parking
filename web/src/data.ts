import cityHall from '../fixtures/anyang_city_hall_weekday.json';
import seoksu from '../fixtures/seoksu_station_saturday.json';
import beomgye from '../fixtures/beomgye_station_sunday.json';
import testManual from '../fixtures/_test_manual_values.json';

// 카드 스키마는 recommend.py 응답 그대로(snake_case). 여기서 필드를 새로 지어내지 않는다.
export type Card = {
  name: string;
  parking_id: number;
  drive_min: number | null;
  walk_min: number | null;
  total_min: number | null;
  fare_payg: number | null;
  fare_daily_pass: number | null;
  daily_pass_better: boolean;
  avail_now: number | null; // 주차된 대수(빈자리 수 아님)
  avail_pred: number | null; // 도착 시점 예측 점유율(%)
  full_prob: number | null; // 만차 확률(0~1)
  walk_far_warning: boolean;
  estimated: boolean;
  cell_cnt: number | null;
  straight_m: number | null;
  grade: number | null;
  is_live: boolean;
  operating_hours: string | null;
  access_status: string | null;
  unavailable_note: string | null;
  arrive_at: string;
  fare_reason: string | null;
};

export type FixtureData = {
  cards: Card[];
  by_fare: Card[];
  by_walk: Card[];
  unavailable: Card[];
  depart_at: string;
  park_minutes: number;
  generated_at: string;
  scenario_note?: string;
};

export type Scenario = {
  key: string;
  label: string;
  destinationName: string;
  destinationAddress: string;
  dayLabel: string;
  isTest?: boolean;
  data: FixtureData;
};

export const scenarios: Scenario[] = [
  {
    key: 'anyang_city_hall_weekday',
    label: '안양시청 · 평일',
    destinationName: '안양시청',
    destinationAddress: '안양시 동안구 시민대로 235',
    dayLabel: '평일',
    data: cityHall as FixtureData,
  },
  {
    key: 'seoksu_station_saturday',
    label: '석수역 · 토요일',
    destinationName: '석수역',
    destinationAddress: '안양시 만안구 석수역 인근',
    dayLabel: '토요일',
    data: seoksu as FixtureData,
  },
  {
    key: 'beomgye_station_sunday',
    label: '범계역 · 일요일',
    destinationName: '범계역',
    destinationAddress: '안양시 동안구 동안로 130',
    dayLabel: '일요일',
    data: beomgye as FixtureData,
  },
  {
    key: '_test_manual_values',
    label: '테스트(손입력 값, 제출용 아님)',
    destinationName: '안양시청(테스트)',
    destinationAddress: 'avail_pred/full_prob를 손으로 채운 렌더 확인용 — 실제 예측 아님',
    dayLabel: '테스트',
    isTest: true,
    data: testManual as FixtureData,
  },
];

export const defaultScenarioKey = scenarios[0].key;

export const searchSuggestions = [
  { name: '안양시청', address: '안양시 동안구 시민대로 235' },
  { name: '범계역', address: '안양시 동안구 동안로 130' },
  { name: '평촌중앙공원', address: '안양시 동안구 관평로 149' },
  { name: '안양역', address: '안양시 만안구 만안로 232' },
];

export type Severity = 'green' | 'yellow' | 'orange' | 'grey';

/** full_prob가 있으면 그걸로, 없으면 avail_now/cell_cnt(현재 점유율)로 색을 정한다. 둘 다 없으면 grey. */
export function severity(card: Card): Severity {
  if (card.full_prob != null) {
    if (card.full_prob >= 0.6) return 'orange';
    if (card.full_prob >= 0.3) return 'yellow';
    return 'green';
  }
  if (card.avail_now != null && card.cell_cnt) {
    const occ = card.avail_now / card.cell_cnt;
    if (occ >= 0.9) return 'orange';
    if (occ >= 0.6) return 'yellow';
    return 'green';
  }
  return 'grey';
}

/** 도착 시점 예상 점유율(%). avail_pred가 없으면 현재 점유율로 대체, 그것도 없으면 null. */
export function displayRatio(card: Card): number | null {
  if (card.avail_pred != null) return Math.round(card.avail_pred);
  if (card.avail_now != null && card.cell_cnt) return Math.round((card.avail_now / card.cell_cnt) * 100);
  return null;
}

/** cards/by_fare/by_walk에 is_live=false가 섞여 있어도 방어적으로 걸러 offline 묶음으로 보낸다. */
export function splitLive(list: Card[]): { live: Card[]; offline: Card[] } {
  const live = list.filter((c) => c.is_live !== false);
  const offline = list.filter((c) => c.is_live === false);
  return { live, offline };
}

/** unavailable[]과 cards 안에 섞인 is_live=false를 parking_id 기준으로 합쳐 중복 제거한다. */
export function offlineCardsOf(scenario: Scenario): Card[] {
  const { offline } = splitLive(scenario.data.cards);
  const seen = new Map<number, Card>();
  for (const c of [...scenario.data.unavailable, ...offline]) seen.set(c.parking_id, c);
  return [...seen.values()];
}
