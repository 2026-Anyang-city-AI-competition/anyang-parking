/**
 * 서버 응답 타입. `web/fixtures/*.json` 과 같은 모양이다.
 *
 * ★ 좌표는 항상 `lat`/`lng` 이름으로만 온다. 서버가 카카오의 x/y 를 한 곳에서
 *   뒤집어 주므로 프런트에서 다시 뒤집지 않는다.
 * ★ null 은 "값이 없다"가 아니라 "계산/예측하지 않았다"는 뜻이다.
 *   0 으로 바꿔 표시하지 말 것 — 이유는 항상 같이 오는 `*_reason` 에 있다.
 */

/** 예측을 내보낼 수 있는 상태인지. `available` 이 아니면 p50·만차확률이 전부 null 이다. */
export type PredictionStatus =
  | 'available'
  | 'unavailable'
  | 'evaluation_pending'
  | 'frozen'
  | 'anomaly'
  | 'dead_feed'
  /** 이 주차장은 이 예측시간에서 정확도가 기준에 못 미쳐 예측을 내보내지 않는다. */
  | 'accuracy_not_certified';

/** 출입 가능 여부. `unknown` 을 24시간 개방으로 간주하지 않는다. */
export type AccessStatus = 'available' | 'unknown';

/** 조사 요금으로 계산했는지. `legacy_db_unverified` 는 미확정 DB 시간으로 계산한 값이다. */
export type FeeSource = 'survey' | 'legacy_db_unverified' | 'mixed';

export type ExcludeReason =
  | 'entry_closed_at_arrival'
  | 'closes_before_departure'
  | 'closed_interval_crossed'
  | 'general_public_not_allowed'
  | 'temporary_closure'
  | 'vehicle_restriction'
  | 'access_schedule_unknown'
  | 'insufficient_exit_margin';

export type AccessDecision = {
  parking_id?: number;
  available: boolean | null;
  excluded: boolean;
  reason: string;
  message: string;
  arrival_at?: string;
  expected_departure_at?: string;
  safety_margin_minutes: number;
  access_closes_at?: string | null;
};

/** 계산 단계 한 줄. `amt` 를 모두 더하면 `total` 이 된다(음수는 상한·감면·절사). */
export type FareBreakdownRow = {
  kind: 'free_window' | 'benefit_free' | 'progressive' | 'daily_cap' | 'benefit_rate' | 'round_down';
  seg: string;
  min: number;
  amt: number;
  /** 하루를 넘는 주차에서만 붙는다. */
  date?: string;
};

export type FeeScheduleDay = {
  date: string;
  source: FeeSource;
  window_minutes: number;
  billable_minutes: number;
  segments: { start: string; end: string; minutes: number }[];
};

export type Fare = {
  /** 후불 누진 총액. null 이면 계산하지 않은 것이고 `reason` 에 사유가 있다. */
  total: number | null;
  reason: string | null;
  breakdown: FareBreakdownRow[];
  capped: boolean;
  billable_min: number;
  free_minutes: number;
  free_minutes_outside_fee_window: number;
  /** 별표 5 정가. 표 밖이면 null 이며 추정하지 않는다. */
  daily_pass: number | null;
  daily_pass_better_after_min: number | null;
  raw_progressive: number | null;
  /** 선불 일일권 금액. 후불과 **병기**하며 자동으로 싼 쪽을 적용하지 않는다.
   *  하루를 넘는 주차에서는 null 이고 `prepaid_reason` 에 사유가 있다. */
  total_prepaid: number | null;
  recommend_prepaid: boolean;
  prepaid_saving: number | null;
  prepaid_reason: string | null;
  /** 유료 주차가 걸친 날짜 수. 2 이상이면 날짜별로 누진·일 상한을 따로 계산했다. */
  paid_days: number;
  daily_pass_days_required: number;
  daily_pass_scope: string;
  fee_schedule: FeeScheduleDay[];
  fee_source: FeeSource;
};

export type DemotionReason = {
  code: 'full_probability_above_cutoff' | 'current_occupancy_above_90';
  message: string;
  full_prob?: number;
  cutoff?: number;
  occ_now?: number;
};

export type ParkingCard = {
  parking_id: number;
  name: string;
  lat: number | null;
  lng: number | null;
  grade: number | null;
  cell_cnt: number | null;
  straight_m: number | null;

  access: AccessDecision;
  access_status: AccessStatus;
  expected_departure_at: string | null;

  drive_min: number | null;
  walk_min: number | null;
  total_min: number | null;
  arrive_at: string;
  arrive_at_iso: string;
  /** 차량 경로 조회 실패로 추정치를 쓴 경우. 기존 클라이언트 호환 필드다. */
  estimated: boolean;
  drive_estimated?: boolean;
  walk_estimated?: boolean;
  route_source?: string | null;
  walk_source?: string | null;
  /** 미래 출발에서 `current`면 현재 교통 기준 ETA다. */
  route_traffic_basis?: 'live' | 'current' | null;
  walk_far_warning: boolean;

  fare: Fare;
  /** 감면 코드를 보냈을 때만 붙는다. */
  benefit: CardBenefit | null;
  fare_payg: number | null;
  fare_daily_pass: number | null;
  daily_pass_better: boolean;
  fare_reason: string | null;
  fee_source: FeeSource;

  prediction_status: PredictionStatus;
  prediction_reason: string | null;
  prediction_source: string | null;
  model_horizon_min: number | null;
  avail_now: number | null;
  avail_pred: number | null;
  occ_now: number | null;
  full_prob: number | null;
  /** 확률을 **숫자로** 보여줘도 되는지. false 면 순위에는 썼지만 수치는 감춘다. */
  full_prob_calibrated: boolean | null;
  pred_p10: number | null;
  pred_p90: number | null;
  interval_status: 'pass' | 'withheld' | 'unverified' | 'unavailable';

  is_live: boolean;
  is_operating: boolean;
  operating_hours: string;
  weekday_hours: string;
  weekend_hours: string;
  observation_at: string | null;
  observation_age_min: number | null;
  observation_status: string;
  unavailable_note: string | null;
};

/** 순위 배열의 카드에는 축별 순위가 더 붙는다. */
export type RankedCard = ParkingCard & {
  rank: number;
  /** 만차확률 강등을 적용하지 않았을 때의 순위. `rank` 와 다르면 강등이 순위를 바꾼 것이다. */
  rank_without_demotion: number | null;
  demoted: boolean;
  demotion_reason: DemotionReason | null;
};

export type ExcludedLot = {
  parking_id: number;
  name: string;
  reason: ExcludeReason | string;
  message: string;
  arrival_at?: string;
  expected_departure_at?: string;
  access_closes_at?: string | null;
};

export type Alternative = {
  name: string;
  lat: number;
  lng: number;
  is_public: boolean;
  distance_m: number;
};

export type RecommendResponse = {
  cards: ParkingCard[];
  by_walk: RankedCard[];
  by_fare: RankedCard[];
  /** 실시간 정보를 제공하지 않는 주차장. 위치·요금은 유효하다. */
  live_unavailable: ParkingCard[];
  /** @deprecated `live_unavailable` 을 쓴다. */
  unavailable: ParkingCard[];
  /** 출입 조건으로 추천에서 뺀 곳. 실시간 미제공과 의미가 다르다. */
  excluded: ExcludedLot[];
  alternatives: Alternative[];
  unlabeled: unknown[];
  dead_feeds: unknown[];
  candidate_count: number;
  radius_used: number;
  /** 3km 까지 넓혔는데도 최소 후보를 못 채운 경우. */
  exhausted: boolean;
  message: string | null;
  depart_at: string;
  depart_at_iso: string;
  park_minutes: number;
  request_id?: string;
  service?: ServiceStatus;
  poll?: PollStatus;
  access_rules?: { status: string; rules_loaded: number; lots_loaded: number };
  prediction_gate?: { status: string; rules_loaded: number; errors: string[] };
};

export type ServiceStatus = {
  model_status: string;
  model_version: string | null;
  data_status: 'fresh' | 'stale' | string;
  observation_at: string | null;
  observation_age_min: number | null;
  refresh_error: string | null;
  live_lots: number | null;
  fixed_feeds: number | null;
  prediction_ready_lots: number | null;
};

export type PollStatus = {
  status: string;
  attempted: boolean;
  reason: string | null;
  observation_at: string | null;
  lots: number | null;
  duration_ms: number;
  error: string | null;
};

export type Place = {
  name: string;
  road_address: string | null;
  address: string | null;
  lat: number;
  lng: number;
  in_anyang: boolean;
  distance_from_anyang_m: number;
  category: string | null;
};

export type PlacesResponse = {
  places: Place[];
  /** `kakao` 는 방금 조회, `cache` 는 캐시. `stale` 이면 카카오 장애 중이다. */
  source: 'kakao' | 'cache';
  stale: boolean;
  cache_age_sec: number;
  query: string;
  count: number;
  request_id?: string;
};

export type ReverseGeocodeResponse = {
  place: Place;
  request_id?: string;
};

export type Benefit = {
  code: string;
  label: string;
  description: string;
  discount_percent: number;
  free_minutes: number;
  note: string;
  /** 현장에서 제시할 증빙. 서비스는 이 서류를 받거나 저장하지 않는다. */
  evidence: string;
  evidence_required: boolean;
};

export type BenefitsResponse = {
  benefits: Benefit[];
  stacking: string;
  evidence_note: string;
  combinations: { codes: string[]; combined_code: string; note: string }[];
  request_id?: string;
};

/** 자격 하나의 계산 결과. 탈락한 자격도 사유와 함께 남는다. */
export type BenefitOption = {
  code: string;
  label: string;
  evidence: string;
  total: number | null;
  total_prepaid: number | null;
  free_minutes: number;
  combined_from: string[] | null;
  applied: boolean;
  rejected_reason: string | null;
};

export type CardBenefit = {
  applied: string | null;
  options: BenefitOption[];
  stacking: string;
  evidence_note: string;
};

export type FareQuoteResponse = {
  parking_id: number;
  name: string;
  arrival_at: string;
  expected_departure_at: string;
  parking_minutes: number;
  access: { available: boolean | null; status: string; reason: string; message: string };
  fare: {
    billable_minutes: number;
    free_minutes_outside_fee_window: number;
    discount_free_minutes: number;
    payg: number | null;
    daily_pass: number | null;
    daily_pass_list_price: number | null;
    /** 판매·매진 정보가 없어 항상 null 이다. 구매 가능으로 단정하지 않는다. */
    daily_pass_purchasable: boolean | null;
    daily_pass_days_required: number;
    daily_pass_scope: string;
    /** 하루를 넘는 주차에서 선불 총액을 주지 않는 이유. */
    daily_pass_note: string | null;
    daily_pass_better_after_min: number | null;
    paid_days: number;
    recommended_option: 'payg' | 'daily_pass' | null;
    saving: number | null;
    capped: boolean;
    raw_progressive: number | null;
    applied_benefit: {
      code: string; label: string; note: string; evidence: string;
      evidence_note: string; combined_from: string[] | null;
    } | null;
    benefit_options: BenefitOption[];
    benefit_stacking_note: string;
    breakdown: FareBreakdownRow[];
    fee_schedule: FeeScheduleDay[];
    fee_source: FeeSource;
    reason: string | null;
  };
  request_id?: string;
};

export type ApiError = {
  error: { code: string; message: string; details?: unknown };
  request_id?: string;
};
