/**
 * 응답을 화면 문구로 바꾸는 규칙. 컴포넌트가 아니라 여기에 모아 둔다.
 *
 * ★ null 을 0 이나 "정보 없음"으로 뭉개지 않는다. 왜 없는지를 보여준다.
 * ★ 후불과 선불 일일권을 자동으로 합치지 않는다(조례 별표1 비고 8).
 */
import type { ParkingCard, RankedCard, RecommendResponse } from './types';

export type Tone = 'green' | 'yellow' | 'orange' | 'red' | 'unknown';

/** 예측이 없으면 색으로 혼잡도를 암시하지 않는다. */
export function toneOf(card: ParkingCard): Tone {
  if (card.prediction_status !== 'available' || card.full_prob === null) return 'unknown';
  if (card.full_prob >= 0.8) return 'red';
  if (card.full_prob >= 0.5) return 'orange';
  if (card.full_prob >= 0.25) return 'yellow';
  return 'green';
}

export const PREDICTION_UNAVAILABLE =
  '현재 시간대에는 혼잡도 예측을 제공하지 않아요. 위치와 요금을 기준으로 안내해요.';

const PREDICTION_NOTE: Record<string, string> = {
  dead_feed: '이 주차장은 실시간 정보를 제공하지 않아요. 위치와 요금만 안내해요.',
  accuracy_not_certified:
    '이 주차장은 이 시간대 예측 정확도가 기준에 못 미쳐 혼잡도를 안내하지 않아요. 위치와 요금은 그대로 확인하실 수 있어요.',
  frozen: PREDICTION_UNAVAILABLE,
  anomaly: PREDICTION_UNAVAILABLE,
  evaluation_pending: PREDICTION_UNAVAILABLE,
  unavailable: PREDICTION_UNAVAILABLE,
};

export function predictionNote(card: ParkingCard): string | null {
  if (card.prediction_status === 'available') return null;
  return PREDICTION_NOTE[card.prediction_status] ?? PREDICTION_UNAVAILABLE;
}

/** 만차 가능성 문구. 보정이 어긋난 예측시간에서는 숫자를 인용하지 않는다.
 *  순위에는 그대로 쓰였지만(A25: 피해 0건), "78%" 같은 수치는 과신이라 내보내지 않는다. */
export function fullnessLabel(card: ParkingCard): string | null {
  if (card.full_prob === null) return null;
  if (card.full_prob_calibrated) return `만차확률 ${Math.round(card.full_prob * 100)}%`;
  if (card.full_prob >= 0.8) return '만차 가능성 높음';
  if (card.full_prob >= 0.5) return '혼잡 예상';
  if (card.full_prob >= 0.25) return '보통';
  return '여유 예상';
}

/** 도착 시점 예측 잔여 면수. 예측이 없으면 null 이고, 현재값으로 대신하지 않는다. */
export function predictedFree(card: ParkingCard): number | null {
  if (card.prediction_status !== 'available' || card.avail_pred === null || !card.cell_cnt) {
    return null;
  }
  return Math.max(0, Math.round(card.cell_cnt - (card.avail_pred / 100) * card.cell_cnt));
}

export function currentFree(card: ParkingCard): number | null {
  if (card.avail_now === null || !card.cell_cnt) return null;
  return Math.max(0, card.cell_cnt - card.avail_now);
}

export function won(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toLocaleString()}원`;
}

/** 요금 문구. 계산하지 못했으면 금액을 지어내지 않고 사유를 보여준다. */
export function fareLabel(card: ParkingCard): { main: string; sub: string | null; note: string | null } {
  const { fare } = card;
  if (fare.total === null) {
    return { main: '요금 계산 불가', sub: null, note: fare.reason ?? '요금을 계산할 수 없어요.' };
  }
  const sub = fare.total_prepaid !== null ? `선불 일일권 ${won(fare.total_prepaid)}` : null;
  const note =
    fare.fee_source === 'survey'
      ? null
      : '조사로 확정되지 않은 운영시간으로 계산한 값이라 실제와 다를 수 있어요.';
  return { main: won(fare.total), sub, note };
}

export function accessWarning(card: ParkingCard): string | null {
  if (card.access_status === 'unknown') {
    return '이 시간대 입·출차 조건이 아직 확인되지 않았어요.';
  }
  return null;
}

/** 후보가 부족하거나 조건에 맞는 곳이 없을 때의 안내. 실패를 빈 목록으로 감추지 않는다. */
export function resultNotice(result: RecommendResponse): string | null {
  if (result.message) return result.message;
  if (!result.by_walk.length && result.excluded.length) {
    return '입출차 조건에 맞는 주변 공영주차장이 없어요.';
  }
  if (!result.by_walk.length) return '주변에서 추천할 공영주차장을 찾지 못했어요.';
  return null;
}

export function demotionNote(card: RankedCard): string | null {
  if (!card.demoted || !card.demotion_reason) return null;
  const { message, full_prob } = card.demotion_reason;
  if (full_prob !== undefined) {
    return `${message} (만차 확률 ${Math.round(full_prob * 100)}%)`;
  }
  return message;
}

export function sortedBy(result: RecommendResponse, mode: 'recommend' | 'fare' | 'walk'): RankedCard[] {
  return mode === 'fare' ? result.by_fare : result.by_walk;
}

/** 위경도를 지도 상자 안의 % 좌표로. 핀 위치를 실제 좌표에서 만든다. */
export function project(
  points: { lat: number | null; lng: number | null }[],
  lat: number,
  lng: number,
): { left: string; top: string } {
  const lats = points.map((p) => p.lat).filter((v): v is number => v !== null);
  const lngs = points.map((p) => p.lng).filter((v): v is number => v !== null);
  if (!lats.length || !lngs.length) return { left: '50%', top: '50%' };
  const pad = 0.0008;
  const minLat = Math.min(...lats) - pad;
  const maxLat = Math.max(...lats) + pad;
  const minLng = Math.min(...lngs) - pad;
  const maxLng = Math.max(...lngs) + pad;
  const x = maxLng === minLng ? 0.5 : (lng - minLng) / (maxLng - minLng);
  // 위도는 위로 갈수록 커지므로 화면 y 와 반대다.
  const y = maxLat === minLat ? 0.5 : 1 - (lat - minLat) / (maxLat - minLat);
  return { left: `${10 + x * 80}%`, top: `${12 + y * 74}%` };
}
