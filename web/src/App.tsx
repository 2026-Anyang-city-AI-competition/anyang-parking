import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Car,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Footprints,
  Info,
  Map,
  MapPin,
  Navigation,
  Search,
  Settings,
  Star,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react';
import { ApiProblem, listBenefits, recommend, searchPlaces } from './api/client';
import type { Benefit, ParkingCard, Place, RankedCard, RecommendResponse } from './api/types';
import {
  clearPreferences,
  DEFAULT_PREFERENCES,
  loadPreferences,
  savePreferences,
  samePlace,
  toggleFavorite,
  withRecent,
  type Preferences,
  type SavedPlace,
} from './api/prefs';
import {
  accessWarning,
  currentFree,
  demotionNote,
  fareLabel,
  predictedFree,
  predictionNote,
  project,
  resultNotice,
  sortedBy,
  toneOf,
  won,
} from './api/present';

type Screen = 'home' | 'results' | 'detail' | 'settings';
type SortMode = 'recommend' | 'fare' | 'walk';

const durations = [30, 60, 120, 180];

function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark"><MapPin size={22} strokeWidth={1.8} /></div>
      <div>
        <strong>안양 공영주차</strong>
        <span>도착할 때 비어 있을 자리를 미리 찾아드려요</span>
      </div>
    </div>
  );
}

function Notice({ children, tone = 'info' }: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <p className={`state-notice ${tone}`}>
      {tone === 'warn' ? <TriangleAlert size={14} /> : <Info size={14} />}
      <span>{children}</span>
    </p>
  );
}

/** 실제 좌표로 핀을 찍는 지도. 실제 지도 타일 대신 상대 위치만 보여준다. */
function ResultMap({
  cards,
  destination,
  selected,
  onSelect,
}: {
  cards: ParkingCard[];
  destination: Place | null;
  selected?: number;
  onSelect?: (id: number) => void;
}) {
  const points = useMemo(
    () => [...cards, ...(destination ? [{ lat: destination.lat, lng: destination.lng }] : [])],
    [cards, destination],
  );
  return (
    <div className="mock-map compact">
      {destination && (
        <span
          className="destination-pin"
          aria-label={`목적지 ${destination.name}`}
          style={project(points, destination.lat, destination.lng)}
        >
          <MapPin size={20} fill="currentColor" />
        </span>
      )}
      {cards.map((card, index) =>
        card.lat === null || card.lng === null ? null : (
          <button
            key={card.parking_id}
            aria-label={`${card.name} 도보 ${card.walk_min ?? '?'}분`}
            className={`parking-pin ${toneOf(card)} ${selected === card.parking_id ? 'selected' : ''}`}
            style={project(points, card.lat, card.lng)}
            onClick={() => onSelect?.(card.parking_id)}
          >
            {index + 1}
          </button>
        ),
      )}
    </div>
  );
}

function Availability({ card }: { card: ParkingCard }) {
  const note = predictionNote(card);
  const now = currentFree(card);
  const predicted = predictedFree(card);
  return (
    <>
      <div className="availability">
        <div className={`availability-box current ${toneOf(card)}`}>
          <span><i /> 지금 {card.is_live ? '· 실시간' : '· 미제공'}</span>
          <strong>
            {now === null ? '—' : now}
            <small>{card.cell_cnt ? `면 / ${card.cell_cnt}` : '면'}</small>
          </strong>
        </div>
        <div className="availability-box future">
          <span><Sparkles size={11} /> {card.arrive_at} 도착 · AI 예측</span>
          <strong>
            {predicted === null ? '—' : predicted}
            <small>
              면{' '}
              {card.full_prob !== null && <em>만차 {Math.round(card.full_prob * 100)}%</em>}
            </small>
          </strong>
        </div>
      </div>
      {note && <p className="model-message"><CircleHelp size={14} /> {note}</p>}
    </>
  );
}

function ParkingCardView({ card, onOpen }: { card: RankedCard; onOpen: () => void }) {
  const fare = fareLabel(card);
  const warning = accessWarning(card);
  const demotion = demotionNote(card);
  return (
    <article className="parking-card" onClick={onOpen}>
      <div className="card-title">
        <span className={`rank ${toneOf(card)}`}>{card.rank}</span>
        <div>
          <h3>{card.name}</h3>
          <p>
            <span>{card.grade ? `${card.grade}급지` : '급지 미상'}</span>
            {card.cell_cnt ? <small>총 {card.cell_cnt}면</small> : null}
          </p>
        </div>
        <ChevronRight size={20} />
      </div>
      <Availability card={card} />
      {demotion && <Notice tone="warn">{demotion}</Notice>}
      {warning && <Notice tone="warn">{warning}</Notice>}
      {card.estimated && <Notice tone="warn">실시간 경로 조회 실패 — 이동 시간은 추정치예요.</Notice>}
      <div className="card-footer">
        <span><Car size={14} /> {card.drive_min ?? '—'}분</span>
        <span><Footprints size={14} /> {card.walk_min ?? '—'}분</span>
        <strong>
          {card.fare.total === null ? '요금 계산 불가' : <>후불 <b>{fare.main}</b></>}
          {fare.sub && <small>{fare.sub}</small>}
        </strong>
      </div>
      {fare.note && <Notice tone="warn">{fare.note}</Notice>}
    </article>
  );
}

function Home({
  destination,
  onDestination,
  minutes,
  onMinutes,
  departInMinutes,
  onDepart,
  onSubmit,
  busy,
  error,
  benefitCount,
  onSettings,
  favorites,
  recents,
  onPick,
  onToggleFavorite,
}: {
  destination: Place | null;
  onDestination: (place: Place | null) => void;
  minutes: number;
  onMinutes: (value: number) => void;
  departInMinutes: number;
  onDepart: (value: number) => void;
  onSubmit: () => void;
  busy: boolean;
  error: string | null;
  benefitCount: number;
  onSettings: () => void;
  favorites: SavedPlace[];
  recents: SavedPlace[];
  onPick: (place: SavedPlace) => void;
  onToggleFavorite: (place: SavedPlace) => void;
}) {
  const [query, setQuery] = useState(destination?.name ?? '');
  const [places, setPlaces] = useState<Place[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const term = query.trim();
    if (!open || term.length < 2) {
      setPlaces([]);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSearching(true);
      setSearchError(null);
      try {
        const found = await searchPlaces(term, controller.signal);
        setPlaces(found.places);
        setStale(found.stale);
      } catch (problem) {
        if (controller.signal.aborted) return;
        setPlaces([]);
        setSearchError(problem instanceof ApiProblem ? problem.userMessage : '검색에 실패했어요.');
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 300); // 입력마다 부르지 않는다. 서버 쪽에도 요청 제한이 걸려 있다.
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query, open]);

  return (
    <main className="screen home-screen">
      <header className="hero">
        <Brand />
        <div className="proof-row">
          <span>안양시 공공데이터 기반</span>
          <span><Sparkles size={12} /> 실시간 + AI 예측</span>
        </div>
      </header>

      <section className="search-card">
        <label className="field-label" htmlFor="destination">어디로 가세요?</label>
        <div className="search-field">
          <Search size={20} />
          <input
            id="destination"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setOpen(true); onDestination(null); }}
            onFocus={() => setOpen(true)}
            placeholder="장소명 또는 주소 검색"
            autoComplete="off"
          />
          {query && (
            <button aria-label="입력 지우기" onClick={() => { setQuery(''); onDestination(null); }}>
              <X size={14} />
            </button>
          )}
        </div>
        {destination && <p className="address">{destination.road_address ?? destination.address ?? ''}</p>}

        {open && query.trim().length >= 2 && (
          <div className="suggestions">
            {searching && <p className="suggestion-state">검색 중…</p>}
            {!searching && searchError && <p className="suggestion-state warn">{searchError}</p>}
            {!searching && !searchError && !places.length && (
              <p className="suggestion-state">검색 결과가 없어요.</p>
            )}
            {places.map((place) => (
              <button
                key={`${place.name}-${place.lat}-${place.lng}`}
                onClick={() => { onDestination(place); setQuery(place.name); setOpen(false); }}
              >
                <MapPin size={17} />
                <span>
                  <strong>{place.name}</strong>
                  <small>{place.road_address ?? place.address ?? ''}</small>
                </span>
              </button>
            ))}
            {stale && !searching && (
              <p className="suggestion-state warn">검색 서버 응답이 늦어 최근 결과를 보여드려요.</p>
            )}
          </div>
        )}

        {(favorites.length > 0 || recents.length > 0) && (
          <div className="saved-places">
            {favorites.map((place) => (
              <button key={`fav-${place.name}-${place.lat}`} onClick={() => onPick(place)}>
                <Star size={13} fill="currentColor" /> {place.name}
              </button>
            ))}
            {recents
              .filter((place) => !favorites.some((fav) => samePlace(fav, place)))
              .map((place) => (
                <button key={`recent-${place.name}-${place.lat}`} onClick={() => onPick(place)}>
                  <Clock3 size={13} /> {place.name}
                </button>
              ))}
          </div>
        )}

        {destination && (
          <button
            className="favorite-toggle"
            onClick={() => onToggleFavorite({
              name: destination.name,
              address: destination.road_address ?? destination.address,
              lat: destination.lat,
              lng: destination.lng,
            })}
          >
            <Star
              size={15}
              fill={favorites.some((fav) => fav.name === destination.name) ? 'currentColor' : 'none'}
            />
            {favorites.some((fav) => fav.name === destination.name) ? '즐겨찾기에서 빼기' : '즐겨찾기에 넣기'}
          </button>
        )}

        <div className="divider" />
        <span className="field-label">얼마나 주차하세요?</span>
        <div className="duration-grid">
          {durations.map((minute) => (
            <button key={minute} className={minutes === minute ? 'active' : ''} onClick={() => onMinutes(minute)}>
              {minute < 60 ? `${minute}분` : `${minute / 60}시간`}
            </button>
          ))}
        </div>

        <div className="divider" />
        <span className="field-label">언제 출발하세요?</span>
        <div className="depart-grid">
          <button className={departInMinutes === 0 ? 'active' : ''} onClick={() => onDepart(0)}>
            <span><Clock3 size={15} /> 지금 출발</span><small>현재 시각 기준</small>
          </button>
          <button className={departInMinutes === 30 ? 'active' : ''} onClick={() => onDepart(30)}>
            <span><CalendarDays size={15} /> 30분 뒤</span><small>도착 시점으로 예측</small>
          </button>
        </div>
      </section>

      <button className="nearby-card" onClick={onSettings}>
        <Settings size={18} />
        <span>
          <strong>내 할인 설정</strong>
          <small>
            {benefitCount > 0 ? `${benefitCount}개 선택됨 · 가장 저렴한 하나를 적용해요` : '해당하는 할인을 고르면 요금에 반영돼요'}
          </small>
        </span>
        <ChevronRight size={20} />
      </button>

      {error && <Notice tone="warn">{error}</Notice>}

      <button className="primary-cta" disabled={!destination || busy} onClick={onSubmit}>
        {busy ? '추천을 계산하고 있어요…' : <>주차장 찾기 <ArrowRight size={20} /></>}
      </button>
      {!destination && <p className="ai-note">목적지를 검색해 선택하면 추천을 시작해요</p>}
      <p className="ai-note">
        차단기가 없어 계측되지 않는 주차장은<br />예측 없이 위치와 요금만 안내해요
      </p>
    </main>
  );
}

/** 로그인 없는 마이페이지. 값은 이 브라우저에만 남고 서버로는 코드만 나간다. */
function SettingsScreen({
  preferences,
  onChange,
  onBack,
}: {
  preferences: Preferences;
  onChange: (next: Preferences) => void;
  onBack: () => void;
}) {
  const [benefits, setBenefits] = useState<Benefit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listBenefits(controller.signal)
      .then((body) => setBenefits(body.benefits))
      .catch((problem) => {
        if (controller.signal.aborted) return;
        setError(problem instanceof ApiProblem ? problem.userMessage : '감면 목록을 불러오지 못했어요.');
      });
    return () => controller.abort();
  }, []);

  const toggle = (code: string) => {
    const chosen = preferences.benefitCodes.includes(code)
      ? preferences.benefitCodes.filter((value) => value !== code)
      : [...preferences.benefitCodes, code].slice(0, 5);
    const next = { ...preferences, benefitCodes: chosen };
    onChange(next);
    setNote(savePreferences(next) ? null : '이 브라우저에 설정을 저장할 수 없어요. 이번 방문에만 적용돼요.');
  };

  return (
    <main className="screen detail-screen">
      <header className="light-header sticky">
        <button className="icon-button" aria-label="뒤로" onClick={onBack}><ArrowLeft /></button>
        <h1>내 할인 설정</h1>
      </header>

      <section className="detail-section">
        <h3>해당하는 할인을 골라주세요</h3>
        <p className="settings-lead">
          고른 항목으로 요금을 각각 계산해 가장 저렴한 하나를 적용해요. 실제 적용은 현장에서
          증빙을 제시해야 돼요.
        </p>
        {error && <Notice tone="warn">{error}</Notice>}
        {note && <Notice tone="warn">{note}</Notice>}
        <div className="benefit-list">
          {benefits.map((benefit) => {
            const on = preferences.benefitCodes.includes(benefit.code);
            return (
              <button
                key={benefit.code}
                className={`benefit-item ${on ? 'on' : ''}`}
                aria-pressed={on}
                onClick={() => toggle(benefit.code)}
              >
                <span className="benefit-check">{on ? <Check size={14} /> : null}</span>
                <span>
                  <strong>{benefit.label}</strong>
                  <small>{benefit.note}</small>
                  <small className="evidence">현장 증빙: {benefit.evidence}</small>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="detail-section">
        <h3>이 설정에 대해</h3>
        <ul className="privacy-list">
          <li>선택한 <strong>할인 코드만</strong> 이 브라우저에 저장돼요. 계정은 만들지 않아요.</li>
          <li>장애 정도·증명서 번호·주민등록번호 같은 <strong>증빙 정보는 받지 않아요.</strong></li>
          <li>요금을 계산할 때 서버로 보내는 것도 할인 코드뿐이에요.</li>
        </ul>
        <button
          className="danger-button"
          onClick={() => {
            clearPreferences();
            onChange({ ...DEFAULT_PREFERENCES });
            setNote('설정을 모두 지웠어요.');
          }}
        >
          <Trash2 size={16} /> 설정 전체 삭제
        </button>
      </section>
    </main>
  );
}

function Results({
  result,
  destination,
  minutes,
  busy,
  onBack,
  onSelect,
}: {
  result: RecommendResponse;
  destination: Place | null;
  minutes: number;
  busy: boolean;
  onBack: () => void;
  onSelect: (card: RankedCard) => void;
}) {
  const [sort, setSort] = useState<SortMode>('recommend');
  const cards = sortedBy(result, sort);
  const notice = resultNotice(result);
  const gateBlocked = cards.length > 0 && cards.every((c) => c.prediction_status !== 'available');

  return (
    <main className="screen results-screen">
      <header className="results-header">
        <button className="icon-button" aria-label="뒤로" onClick={onBack}><ArrowLeft /></button>
        <div>
          <h1>{destination?.name ?? '검색 결과'}</h1>
          <p>{destination?.road_address ?? destination?.address ?? ''}</p>
        </div>
        <button className="outline-button" onClick={onBack}>조건 변경</button>
        <div className="result-meta">
          <span><Clock3 size={13} /> {result.depart_at} 출발</span>
          <span>{minutes < 60 ? `${minutes}분` : `${minutes / 60}시간`} 주차</span>
          <em>{result.candidate_count}곳 · 반경 {result.radius_used / 1000}km</em>
        </div>
      </header>

      <div className="sort-tabs">
        <button className={sort === 'recommend' ? 'active' : ''} onClick={() => setSort('recommend')}>추천순</button>
        <button className={sort === 'fare' ? 'active' : ''} onClick={() => setSort('fare')}>요금 낮은순</button>
        <button className={sort === 'walk' ? 'active' : ''} onClick={() => setSort('walk')}>도보 짧은순</button>
      </div>

      <div className="results-map">
        <ResultMap cards={cards} destination={destination} />
      </div>

      <section className="result-list">
        {busy && <p className="state-notice">최신 정보를 다시 불러오는 중이에요…</p>}
        {notice && <Notice tone="warn">{notice}</Notice>}
        {gateBlocked && <Notice>현재 시간대에는 혼잡도 예측을 제공하지 않아요. 위치와 요금을 기준으로 안내해요.</Notice>}
        {result.service?.data_status === 'stale' && (
          <Notice tone="warn">실시간 관측이 최신이 아니에요. 현재 값은 참고만 해주세요.</Notice>
        )}

        {cards.map((card) => (
          <ParkingCardView key={card.parking_id} card={card} onOpen={() => onSelect(card)} />
        ))}

        {result.excluded.length > 0 && (
          <section className="excluded-list">
            <h4>이용할 수 없어 제외한 {result.excluded.length}곳</h4>
            {result.excluded.map((lot) => (
              <p key={lot.parking_id}><strong>{lot.name}</strong> {lot.message}</p>
            ))}
          </section>
        )}

        {result.live_unavailable.length > 0 && (
          <section className="excluded-list">
            <h4>실시간 정보를 제공하지 않는 {result.live_unavailable.length}곳</h4>
            {result.live_unavailable.map((card) => (
              <p key={card.parking_id}>
                <strong>{card.name}</strong> 도보 {card.walk_min ?? '—'}분 · 후불 {won(card.fare.total)}
              </p>
            ))}
          </section>
        )}
      </section>
    </main>
  );
}

function Detail({ card, minutes, onBack }: { card: RankedCard; minutes: number; onBack: () => void }) {
  const fare = fareLabel(card);
  const demotion = demotionNote(card);
  return (
    <main className="screen detail-screen">
      <header className="light-header sticky">
        <button className="icon-button" aria-label="뒤로" onClick={onBack}><ArrowLeft /></button>
        <h1>주차장 상세</h1>
      </header>

      <section className="detail-hero">
        <span className={`rank large ${toneOf(card)}`}>{card.rank}</span>
        <div>
          <p>추천 {card.rank}순위</p>
          <h2>{card.name}</h2>
          <span>{card.grade ? `${card.grade}급지` : '급지 미상'} · 총 {card.cell_cnt ?? '—'}면</span>
        </div>
      </section>

      <section className="detail-section highlight">
        <h3>도착 시점 예상</h3>
        <Availability card={card} />
        {demotion && <Notice tone="warn">{demotion}</Notice>}
        {accessWarning(card) && <Notice tone="warn">{accessWarning(card)}</Notice>}
      </section>

      <section className="detail-section">
        <h3>예상 이동 시간</h3>
        <div className="journey">
          <div><span><Car /></span><strong>차로 {card.drive_min ?? '—'}분</strong><small>출발지 → 주차장</small></div>
          <ArrowRight />
          <div><span><Footprints /></span><strong>도보 {card.walk_min ?? '—'}분</strong><small>주차장 → 목적지</small></div>
        </div>
        {card.estimated && <Notice tone="warn">실시간 경로 조회 실패 — 추정치예요.</Notice>}
      </section>

      <section className="detail-section">
        <h3>예상 주차 요금</h3>
        <div className="fare-main">
          <span>{minutes < 60 ? `${minutes}분` : `${minutes / 60}시간`} 주차 · 후불</span>
          <strong>{fare.main}</strong>
        </div>
        {card.fare.total !== null && (
          <dl className="fare-list">
            {/* 무료구간 → 감면 면제 → 누진 → 일 상한 → 감면율 → 절사 순서로 그대로 보여준다. */}
            {card.fare.breakdown.map((row, index) => (
              <div key={`${row.kind}-${row.seg}-${index}`} className={row.amt < 0 ? 'deduction' : undefined}>
                <dt>{row.seg}{row.min > 0 ? ` (${row.min}분)` : ''}</dt>
                <dd>{row.amt === 0 ? '0원' : `${row.amt < 0 ? '−' : ''}${Math.abs(row.amt).toLocaleString()}원`}</dd>
              </div>
            ))}
            <div className="fare-total"><dt>합계</dt><dd>{won(card.fare.total)}</dd></div>
            {card.fare.total_prepaid !== null && (
              <div><dt>선불 일일권</dt><dd>{won(card.fare.total_prepaid)}</dd></div>
            )}
          </dl>
        )}
        {card.fare.paid_days > 1 && (
          <Notice>
            유료 시간이 {card.fare.paid_days}일에 걸쳐 있어 날짜별로 누진과 일 최대 상한을 따로 계산했어요.
          </Notice>
        )}
        {card.fare.prepaid_reason && <Notice tone="warn">{card.fare.prepaid_reason}</Notice>}
        {card.fare.total_prepaid !== null && (
          <Notice>
            후불과 선불 일일권은 다른 상품이에요. 일일권은 입차 당일의 유료시간에 쓰는 상품이라
            입차할 때 구매해야 하고, 판매 여부는 현장에서 확인해 주세요.
          </Notice>
        )}
        {fare.note && <Notice tone="warn">{fare.note}</Notice>}
        {card.fare.reason && <Notice tone="warn">{card.fare.reason}</Notice>}
      </section>

      {card.benefit && card.benefit.options.length > 0 && (
        <section className="detail-section">
          <h3>내 할인 적용</h3>
          <div className="benefit-list">
            {card.benefit.options.map((option) => (
              <div key={option.code} className={`benefit-result ${option.applied ? 'on' : ''}`}>
                <div>
                  <strong>{option.label}</strong>
                  <span>{option.total === null ? '계산 불가' : won(option.total)}</span>
                </div>
                {option.combined_from && (
                  <small>조례가 정한 결합이에요 ({option.combined_from.join(' + ')}).</small>
                )}
                {option.applied
                  ? <small className="applied-tag">이 할인을 적용했어요 · 현장 증빙: {option.evidence}</small>
                  : <small>{option.rejected_reason}</small>}
              </div>
            ))}
          </div>
          <Notice tone="warn">{card.benefit.evidence_note}</Notice>
        </section>
      )}

      <section className="detail-section">
        <h3>주차장 정보</h3>
        <div className="info-grid">
          <span>평일 운영<strong>{card.weekday_hours}</strong></span>
          <span>주말 운영<strong>{card.weekend_hours}</strong></span>
          <span>예상 출차<strong>{card.expected_departure_at?.slice(11, 16) ?? '—'}</strong></span>
          <span>
            데이터 상태
            <strong className={card.prediction_status === 'available' ? 'safe' : ''}>
              {card.prediction_status === 'available' ? '정상' : '예측 미제공'}
            </strong>
          </span>
        </div>
      </section>

      <div className="bottom-action">
        <button className="secondary-cta" onClick={onBack}><Map size={19} /> 목록으로</button>
        <a
          className="primary-cta"
          href={card.lat !== null && card.lng !== null
            ? `https://map.kakao.com/link/to/${encodeURIComponent(card.name)},${card.lat},${card.lng}`
            : undefined}
          target="_blank"
          rel="noreferrer"
        >
          <Navigation size={18} /> 길찾기
        </a>
      </div>
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('home');
  const [destination, setDestination] = useState<Place | null>(null);
  const [preferences, setPreferences] = useState<Preferences>(() => loadPreferences());
  const [minutes, setMinutes] = useState(() => loadPreferences().defaultMinutes);
  const [departInMinutes, setDepartInMinutes] = useState(0);
  const [result, setResult] = useState<RecommendResponse | null>(null);
  const [selected, setSelected] = useState<RankedCard | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }); }, [screen]);

  const run = useCallback(async () => {
    if (!destination) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setError(null);
    try {
      const found = await recommend(
        {
          destination: { lat: destination.lat, lng: destination.lng },
          parkingMinutes: minutes,
          departInMinutes,
          // 코드만 보낸다. 증빙 정보는 애초에 갖고 있지 않다.
          benefitCodes: preferences.benefitCodes,
        },
        controller.signal,
      );
      setResult(found);
      // 추천까지 성공한 곳만 최근 목적지에 남긴다. 오타로 검색만 한 건 남기지 않는다.
      const next = withRecent(preferences, {
        name: destination.name,
        address: destination.road_address ?? destination.address,
        lat: destination.lat,
        lng: destination.lng,
      });
      setPreferences(next);
      savePreferences(next);
      setScreen('results');
    } catch (problem) {
      if (controller.signal.aborted) return;
      setError(problem instanceof ApiProblem ? problem.userMessage : '추천을 불러오지 못했어요.');
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }, [destination, minutes, departInMinutes, preferences]);

  return (
    <div className="app-shell">
      <div className="phone-frame">
        {screen === 'home' && (
          <Home
            destination={destination}
            onDestination={setDestination}
            minutes={minutes}
            onMinutes={(value) => {
              setMinutes(value);
              savePreferences({ ...preferences, defaultMinutes: value });
            }}
            departInMinutes={departInMinutes}
            onDepart={setDepartInMinutes}
            onSubmit={run}
            busy={busy}
            error={error}
            benefitCount={preferences.benefitCodes.length}
            onSettings={() => setScreen('settings')}
            favorites={preferences.favorites}
            recents={preferences.recents}
            onPick={(place) => setDestination({
              name: place.name, road_address: place.address, address: place.address,
              lat: place.lat, lng: place.lng, in_anyang: true,
              distance_from_anyang_m: 0, category: null,
            })}
            onToggleFavorite={(place) => {
              const next = toggleFavorite(preferences, place);
              setPreferences(next);
              savePreferences(next);
            }}
          />
        )}
        {screen === 'results' && result && (
          <Results
            result={result}
            destination={destination}
            minutes={minutes}
            busy={busy}
            onBack={() => setScreen('home')}
            onSelect={(card) => { setSelected(card); setScreen('detail'); }}
          />
        )}
        {screen === 'settings' && (
          <SettingsScreen
            preferences={preferences}
            onChange={setPreferences}
            onBack={() => setScreen('home')}
          />
        )}
        {screen === 'detail' && selected && (
          <Detail card={selected} minutes={minutes} onBack={() => setScreen('results')} />
        )}
      </div>
    </div>
  );
}
