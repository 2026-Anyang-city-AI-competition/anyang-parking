/**
 * API 클라이언트. 서버 주소는 `VITE_API_BASE` 로 주고, 없으면 같은 오리진이다.
 *
 * ★ 카카오 키는 서버에만 있다. 브라우저에서 카카오를 직접 부르지 않는다.
 * ★ 실패를 빈 결과로 바꾸지 않는다. 호출자가 「없음」과 「못 가져옴」을 구분해야
 *   사용자에게 맞는 문구를 보여줄 수 있다.
 */
import type {
  ApiError,
  BenefitsResponse,
  FareQuoteResponse,
  PlacesResponse,
  RecommendResponse,
} from './types';

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '');

export class ApiProblem extends Error {
  code: string;
  status: number;
  details?: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = 'ApiProblem';
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** 사용자에게 그대로 보여줄 수 있는 문구. 내부 코드나 경로는 넣지 않는다. */
  get userMessage(): string {
    if (this.code === 'network') return '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.';
    if (this.code === 'search_unavailable') return '장소 검색을 지금 사용할 수 없어요. 잠시 후 다시 시도해 주세요.';
    if (this.code === 'timeout') return '응답이 늦어지고 있어요. 다시 시도해 주세요.';
    return this.message || '요청을 처리하지 못했어요.';
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), init?.timeoutMs ?? 20000);
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch (error) {
    const aborted = error instanceof DOMException && error.name === 'AbortError';
    throw new ApiProblem(0, aborted ? 'timeout' : 'network', '서버에 연결하지 못했습니다');
  } finally {
    window.clearTimeout(timer);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const problem = (body as ApiError | null)?.error;
    throw new ApiProblem(
      response.status,
      problem?.code ?? 'unknown',
      problem?.message ?? '요청을 처리하지 못했습니다',
      problem?.details,
    );
  }
  return body as T;
}

export function searchPlaces(query: string, signal?: AbortSignal): Promise<PlacesResponse> {
  return request<PlacesResponse>(`/api/v1/places/search?q=${encodeURIComponent(query)}`, {
    method: 'GET',
    signal,
    timeoutMs: 8000,
  });
}

export function listBenefits(signal?: AbortSignal): Promise<BenefitsResponse> {
  return request<BenefitsResponse>('/api/v1/benefits', { method: 'GET', signal, timeoutMs: 8000 });
}

export type RecommendInput = {
  destination: { lat: number; lng: number };
  origin?: { lat: number; lng: number };
  parkingMinutes: number;
  departInMinutes?: number;
  /** 감면 **코드**만 보낸다. 증빙 정보는 전송하지 않는다. */
  benefitCodes?: string[];
  accessSafetyMarginMinutes?: number;
};

export function recommend(input: RecommendInput, signal?: AbortSignal): Promise<RecommendResponse> {
  return request<RecommendResponse>('/api/v1/recommend', {
    method: 'POST',
    signal,
    timeoutMs: 30000,
    body: JSON.stringify({
      destination: input.destination,
      ...(input.origin ? { origin: input.origin } : {}),
      parking_minutes: input.parkingMinutes,
      depart_in_minutes: input.departInMinutes ?? 0,
      benefit_codes: input.benefitCodes ?? [],
      access_safety_margin_minutes: input.accessSafetyMarginMinutes ?? 0,
      include_alternatives: false,
    }),
  });
}

export function quoteFare(
  parkingId: number,
  arrivalAt: string,
  parkingMinutes: number,
  benefitCodes: string[] = [],
): Promise<FareQuoteResponse> {
  return request<FareQuoteResponse>('/api/v1/fare/quote', {
    method: 'POST',
    timeoutMs: 10000,
    body: JSON.stringify({
      parking_id: parkingId,
      arrival_at: arrivalAt,
      parking_minutes: parkingMinutes,
      benefit_codes: benefitCodes,
    }),
  });
}
