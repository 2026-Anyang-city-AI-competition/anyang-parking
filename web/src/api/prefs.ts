/**
 * 로그인 없는 개인 설정 — 브라우저에만 저장한다.
 *
 * ★★ **증빙 정보를 수집하지 않는다.** 장애 세부등급·증명서 번호·주민등록번호 같은 건
 *    받지도, 저장하지도, 서버로 보내지도 않는다. 우리가 보관하는 건 감면 **코드**뿐이고,
 *    실제 자격 확인은 현장에서 증빙을 제시해 이뤄진다.
 * ★  서버로 나가는 것도 코드뿐이다. 이 파일의 값은 계정이 아니라 이 브라우저에 묶인다.
 * ★  `clearPreferences()` 로 전부 지울 수 있어야 한다.
 */

const KEY = 'anyang-parking:preferences:v1';

/** 즐겨찾기·최근 목적지에 담는 최소 정보. 방문 시각 같은 이력은 담지 않는다. */
export type SavedPlace = {
  name: string;
  address: string | null;
  lat: number;
  lng: number;
};

export type Preferences = {
  /** 감면 코드만 담는다. 증빙 내용은 담지 않는다. */
  benefitCodes: string[];
  /** 기본 주차 시간(분). */
  defaultMinutes: number;
  favorites: SavedPlace[];
  /** 최근 목적지. 최신이 앞이고 오래된 것은 밀려난다. */
  recents: SavedPlace[];
};

export const MAX_FAVORITES = 10;
export const MAX_RECENTS = 5;

export const DEFAULT_PREFERENCES: Preferences = {
  benefitCodes: [], defaultMinutes: 120, favorites: [], recents: [],
};

function coercePlaces(value: unknown, limit: number): SavedPlace[] {
  if (!Array.isArray(value)) return [];
  const out: SavedPlace[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    const raw = item as Record<string, unknown>;
    if (typeof raw.name !== 'string') continue;
    if (typeof raw.lat !== 'number' || typeof raw.lng !== 'number') continue;
    if (!Number.isFinite(raw.lat) || !Number.isFinite(raw.lng)) continue;
    out.push({
      name: raw.name.slice(0, 80),
      address: typeof raw.address === 'string' ? raw.address.slice(0, 120) : null,
      lat: raw.lat,
      lng: raw.lng,
    });
    if (out.length >= limit) break;
  }
  return out;
}

export function samePlace(a: SavedPlace, b: SavedPlace): boolean {
  return a.name === b.name
    && Math.abs(a.lat - b.lat) < 1e-6
    && Math.abs(a.lng - b.lng) < 1e-6;
}

/** 최근 목적지에 추가. 같은 곳이면 앞으로 끌어올리고 중복을 만들지 않는다. */
export function withRecent(preferences: Preferences, place: SavedPlace): Preferences {
  const rest = preferences.recents.filter((item) => !samePlace(item, place));
  return { ...preferences, recents: [place, ...rest].slice(0, MAX_RECENTS) };
}

export function toggleFavorite(preferences: Preferences, place: SavedPlace): Preferences {
  const existing = preferences.favorites.find((item) => samePlace(item, place));
  const favorites = existing
    ? preferences.favorites.filter((item) => !samePlace(item, place))
    : [place, ...preferences.favorites].slice(0, MAX_FAVORITES);
  return { ...preferences, favorites };
}

/** 저장된 값이 이 모양이 아닐 수 있다. 남의 키·옛 버전·손으로 고친 값도 들어온다. */
function coerce(value: unknown): Preferences {
  if (!value || typeof value !== 'object') return { ...DEFAULT_PREFERENCES };
  const raw = value as Record<string, unknown>;
  const codes = Array.isArray(raw.benefitCodes)
    ? raw.benefitCodes.filter((code): code is string => typeof code === 'string').slice(0, 5)
    : [];
  const minutes = typeof raw.defaultMinutes === 'number' && raw.defaultMinutes > 0
    ? Math.min(10080, Math.round(raw.defaultMinutes))
    : DEFAULT_PREFERENCES.defaultMinutes;
  return {
    benefitCodes: codes,
    defaultMinutes: minutes,
    favorites: coercePlaces(raw.favorites, MAX_FAVORITES),
    recents: coercePlaces(raw.recents, MAX_RECENTS),
  };
}

export function loadPreferences(): Preferences {
  try {
    const stored = window.localStorage.getItem(KEY);
    return stored ? coerce(JSON.parse(stored)) : { ...DEFAULT_PREFERENCES };
  } catch {
    // 사생활 보호 모드나 저장소 차단. 설정이 없는 것으로 보고 계속 간다.
    return { ...DEFAULT_PREFERENCES };
  }
}

export function savePreferences(preferences: Preferences): boolean {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(coerce(preferences)));
    return true;
  } catch {
    return false;
  }
}

/** 설정 전체 삭제. 남기는 것 없이 키를 지운다. */
export function clearPreferences(): boolean {
  try {
    window.localStorage.removeItem(KEY);
    return true;
  } catch {
    return false;
  }
}
