import { useCallback, useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
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
  LocateFixed,
  Map,
  MapPin,
  Maximize2,
  Minimize2,
  Minus,
  Navigation,
  Plus,
  Search,
  Settings,
  Star,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react';
import { ApiProblem, listBenefits, recommend, reverseGeocode, searchPlaces } from './api/client';
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
  fullnessLabel,
  predictedFree,
  predictionNote,
  resultNotice,
  sortedBy,
  toneOf,
  won,
} from './api/present';

type Screen = 'home' | 'map' | 'results' | 'detail' | 'settings';
type SortMode = 'recommend' | 'fare' | 'walk';

const durations = [30, 60, 120, 180];

function durationLabel(minutes: number) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}분`;
  return rest ? `${hours}시간 ${rest}분` : `${hours}시간`;
}

function departureLabel(offset: number) {
  if (offset === 0) return '지금 출발';
  const now = new Date();
  const target = new Date(now.getTime() + offset * 60_000);
  const sameDay = now.getFullYear() === target.getFullYear()
    && now.getMonth() === target.getMonth()
    && now.getDate() === target.getDate();
  const day = sameDay ? '오늘' : '내일';
  return `${day} ${target.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })} 출발`;
}

function TimeSheet({
  kind,
  value,
  onClose,
  onConfirm,
}: {
  kind: 'departure' | 'duration';
  value: number;
  onClose: () => void;
  onConfirm: (value: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const departure = kind === 'departure';
  const min = departure ? 0 : 5;
  const max = departure ? 120 : 1440;
  const quick = departure ? [0, 15, 30, 60, 120] : durations;
  const set = (next: number) => setDraft(Math.min(max, Math.max(min, Math.round(next / 5) * 5)));

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="time-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={departure ? '출발 시각 설정' : '주차 시간 설정'}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="sheet-handle" />
        <div className="sheet-title">
          <div>
            <strong>{departure ? '출발 시각 설정' : '주차 시간 설정'}</strong>
            <span>5분 단위로 선택해요</span>
          </div>
          <button aria-label="닫기" onClick={onClose}><X size={20} /></button>
        </div>

        <div className="time-value">{departure ? departureLabel(draft) : durationLabel(draft)}</div>
        <div className="stepper">
          <button aria-label="5분 줄이기" onClick={() => set(draft - 5)} disabled={draft <= min}><Minus /></button>
          <input
            aria-label={departure ? '출발까지 분' : '주차 분'}
            type="range"
            min={min}
            max={max}
            step={5}
            value={draft}
            onChange={(event) => set(Number(event.target.value))}
          />
          <button aria-label="5분 늘리기" onClick={() => set(draft + 5)} disabled={draft >= max}><Plus /></button>
        </div>
        <div className="quick-times">
          {quick.map((minute) => (
            <button key={minute} className={draft === minute ? 'active' : ''} onClick={() => set(minute)}>
              {departure ? (minute === 0 ? '지금' : `+${minute}분`) : durationLabel(minute)}
            </button>
          ))}
        </div>
        {departure && <p className="sheet-note">주차장 도착이 지금부터 120분을 넘으면 AI 예측 없이 요금과 출입 가능 여부만 안내해요.</p>}
        <button className="primary-cta" onClick={() => { onConfirm(draft); onClose(); }}>이 조건으로 설정</button>
      </section>
    </div>
  );
}

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

const ANYANG_CENTER: [number, number] = [37.394259, 126.956861];

function DestinationMap({
  initial,
  onBack,
  onConfirm,
}: {
  initial: Place | null;
  onBack: () => void;
  onConfirm: (place: Place) => void;
}) {
  const start: [number, number] = initial ? [initial.lat, initial.lng] : ANYANG_CENTER;
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const [point, setPoint] = useState({ lat: start[0], lng: start[1] });
  const [place, setPlace] = useState<Place | null>(initial);
  const [loadingAddress, setLoadingAddress] = useState(!initial);
  const [mapError, setMapError] = useState<string | null>(null);
  const [addressError, setAddressError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  useEffect(() => {
    if (!container.current || map.current) return;
    try {
      const instance = L.map(container.current, { zoomControl: false }).setView(start, 16);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(instance);
      L.control.zoom({ position: 'topright' }).addTo(instance);
      const icon = L.divIcon({
        className: 'destination-marker-wrap',
        html: '<span class="destination-marker-dot"></span>',
        iconSize: [34, 42],
        iconAnchor: [17, 40],
      });
      const pin = L.marker(start, { draggable: true, icon }).addTo(instance);
      const choose = (lat: number, lng: number) => {
        pin.setLatLng([lat, lng]);
        setPoint({ lat, lng });
      };
      instance.on('click', (event: L.LeafletMouseEvent) => choose(event.latlng.lat, event.latlng.lng));
      pin.on('dragend', () => {
        const next = pin.getLatLng();
        choose(next.lat, next.lng);
      });
      map.current = instance;
      marker.current = pin;
      window.setTimeout(() => instance.invalidateSize(), 0);
    } catch {
      setMapError('지도를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.');
    }
    return () => {
      map.current?.remove();
      map.current = null;
      marker.current = null;
    };
    // 지도는 화면을 열 때 한 번만 만든다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoadingAddress(true);
      setAddressError(null);
      try {
        const result = await reverseGeocode(point.lat, point.lng, controller.signal);
        setPlace(result.place);
      } catch (problem) {
        if (controller.signal.aborted) return;
        setPlace(null);
        setAddressError(problem instanceof ApiProblem ? problem.userMessage : '선택한 위치의 주소를 확인하지 못했어요.');
      } finally {
        if (!controller.signal.aborted) setLoadingAddress(false);
      }
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [point.lat, point.lng]);

  const moveToCurrent = () => {
    if (!navigator.geolocation) {
      setMapError('이 브라우저는 현재 위치를 지원하지 않아요.');
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        const next: [number, number] = [coords.latitude, coords.longitude];
        map.current?.setView(next, 17);
        marker.current?.setLatLng(next);
        setPoint({ lat: coords.latitude, lng: coords.longitude });
        setLocating(false);
      },
      () => {
        setMapError('현재 위치를 확인하지 못했어요. 위치 권한을 확인해 주세요.');
        setLocating(false);
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60_000 },
    );
  };

  const confirmed = place ?? {
    name: '지도에서 선택한 위치',
    road_address: null,
    address: null,
    lat: point.lat,
    lng: point.lng,
    in_anyang: false,
    distance_from_anyang_m: 0,
    category: '지도 선택',
  };

  return (
    <main className="screen destination-map-screen">
      <header className="light-header map-header">
        <button className="icon-button" aria-label="뒤로" onClick={onBack}><ArrowLeft /></button>
        <div><h1>목적지를 지도에서 선택</h1><p>지도를 누르거나 핀을 옮겨요</p></div>
      </header>
      <div ref={container} className="destination-map-canvas" aria-label="목적지 선택 지도" />
      <button className="map-locate" onClick={moveToCurrent} disabled={locating}>
        <LocateFixed size={19} /> {locating ? '현재 위치 확인 중' : '내 위치로 이동'}
      </button>
      <section className="map-confirm-sheet">
        <span>선택한 목적지</span>
        <h2>{loadingAddress ? '주소 확인 중…' : (place?.name ?? '지도에서 선택한 위치')}</h2>
        <p>{place?.road_address ?? place?.address ?? `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`}</p>
        {mapError && <Notice tone="warn">{mapError}</Notice>}
        {addressError && <Notice tone="warn">{addressError} 좌표로는 선택할 수 있어요.</Notice>}
        <button
          className="primary-cta"
          disabled={loadingAddress || Boolean(mapError && !map.current)}
          onClick={() => onConfirm({ ...confirmed, lat: point.lat, lng: point.lng })}
        >
          이 위치로 설정 <ArrowRight size={18} />
        </button>
      </section>
    </main>
  );
}

function ResultMap({
  cards,
  destination,
  selected,
  onSelect,
  expanded,
  onExpandedChange,
}: {
  cards: RankedCard[];
  destination: Place | null;
  selected: number | null;
  onSelect: (id: number) => void;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<L.Map | null>(null);
  const markerLayer = useRef<L.LayerGroup | null>(null);
  const parkingMarkers = useRef<Record<number, L.Marker>>({});
  const locationLayer = useRef<L.CircleMarker | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);

  useEffect(() => {
    if (!container.current || map.current) return;
    try {
      const instance = L.map(container.current, { zoomControl: false }).setView(ANYANG_CENTER, 14);
      const tiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      });
      tiles.on('tileerror', () => {
        setMapError('지도 배경을 불러오지 못했어요. 추천 목록은 계속 이용할 수 있어요.');
      });
      tiles.addTo(instance);
      L.control.zoom({ position: 'topright' }).addTo(instance);
      markerLayer.current = L.layerGroup().addTo(instance);
      map.current = instance;
      window.setTimeout(() => instance.invalidateSize(), 0);
    } catch {
      setMapError('지도를 불러오지 못했어요. 추천 목록은 계속 이용할 수 있어요.');
    }
    return () => {
      map.current?.remove();
      map.current = null;
      markerLayer.current = null;
      parkingMarkers.current = {};
      locationLayer.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = map.current;
    const layer = markerLayer.current;
    if (!instance || !layer) return;

    layer.clearLayers();
    parkingMarkers.current = {};
    const bounds: L.LatLngExpression[] = [];

    if (destination) {
      const destinationIcon = L.divIcon({
        className: 'result-destination-marker-wrap',
        html: '<span class="result-destination-marker"><span></span></span>',
        iconSize: [34, 42],
        iconAnchor: [17, 40],
      });
      L.marker([destination.lat, destination.lng], {
        icon: destinationIcon,
        keyboard: true,
        title: `목적지 ${destination.name}`,
      }).addTo(layer);
      bounds.push([destination.lat, destination.lng]);
    }

    cards.forEach((card, index) => {
      if (card.lat === null || card.lng === null) return;
      const icon = L.divIcon({
        className: 'result-parking-marker-wrap',
        html: `<span class="result-parking-marker ${toneOf(card)}${selected === card.parking_id ? ' selected' : ''}">${index + 1}</span>`,
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      });
      const marker = L.marker([card.lat, card.lng], {
        icon,
        keyboard: true,
        title: `${index + 1}위 ${card.name}`,
      });
      marker.on('add', () => {
        marker.getElement()?.setAttribute('aria-label', `${index + 1}위 ${card.name}`);
      });
      marker.on('click', () => onSelect(card.parking_id));
      marker.addTo(layer);
      parkingMarkers.current[card.parking_id] = marker;
      bounds.push([card.lat, card.lng]);
    });

    if (bounds.length === 1) instance.setView(bounds[0], 16);
    if (bounds.length > 1) instance.fitBounds(L.latLngBounds(bounds), { padding: [38, 38], maxZoom: 16 });
  }, [cards, destination, onSelect, selected]);

  useEffect(() => {
    for (const [id, marker] of Object.entries(parkingMarkers.current)) {
      const pin = marker.getElement()?.querySelector('.result-parking-marker');
      pin?.classList.toggle('selected', Number(id) === selected);
      marker.setZIndexOffset(Number(id) === selected ? 1000 : 0);
    }
    const marker = selected === null ? null : parkingMarkers.current[selected];
    if (marker) map.current?.panInside(marker.getLatLng(), { padding: [45, 45], animate: true });
  }, [selected]);

  useEffect(() => {
    const timer = window.setTimeout(() => map.current?.invalidateSize(), 220);
    return () => window.clearTimeout(timer);
  }, [expanded]);

  const moveToCurrent = () => {
    if (!navigator.geolocation) {
      setMapError('이 브라우저는 현재 위치를 지원하지 않아요.');
      return;
    }
    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        const point: L.LatLngExpression = [coords.latitude, coords.longitude];
        if (map.current) {
          locationLayer.current?.remove();
          locationLayer.current = L.circleMarker(point, {
            radius: 8,
            weight: 3,
            color: '#fff',
            fillColor: '#2256b3',
            fillOpacity: 1,
          }).addTo(map.current);
          map.current.setView(point, Math.max(map.current.getZoom(), 15));
        }
        setLocating(false);
      },
      () => {
        setMapError('현재 위치를 확인하지 못했어요. 위치 권한을 확인해 주세요.');
        setLocating(false);
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60_000 },
    );
  };

  return (
    <div className="result-map-shell">
      <div ref={container} className="result-map-canvas" aria-label="추천 주차장 지도" />
      {mapError && <p className="result-map-error">{mapError}</p>}
      <div className="result-map-actions">
        <button onClick={moveToCurrent} disabled={locating} aria-label="지도에서 내 위치 보기">
          <LocateFixed size={15} /> {locating ? '확인 중' : '내 위치'}
        </button>
        <button onClick={() => onExpandedChange(!expanded)} aria-label={expanded ? '지도 줄이기' : '지도 크게 보기'}>
          {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          {expanded ? '목록 보기' : '지도 크게'}
        </button>
      </div>
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
            <small>{card.cell_cnt ? `자리 / ${card.cell_cnt}` : '자리'}</small>
          </strong>
        </div>
        <div className="availability-box future">
          <span><Sparkles size={11} /> {card.arrive_at} 도착 · AI 예측</span>
          <strong>
            {predicted === null ? '—' : predicted}
            <small>
              자리{' '}
              {fullnessLabel(card) && <em>{fullnessLabel(card)}</em>}
            </small>
          </strong>
        </div>
      </div>
      {note && <p className="model-message"><CircleHelp size={14} /> {note}</p>}
    </>
  );
}

function ParkingCardView({
  card,
  selected,
  onMapSelect,
  onOpen,
}: {
  card: RankedCard;
  selected: boolean;
  onMapSelect: () => void;
  onOpen: () => void;
}) {
  const fare = fareLabel(card);
  const demotion = demotionNote(card);
  return (
    <article
      id={`parking-card-${card.parking_id}`}
      className={`parking-card ${selected ? 'selected' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={`${card.name} 지도 핀 강조`}
      onClick={onMapSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onMapSelect();
        }
      }}
    >
      <div className="card-title">
        <span className={`rank ${toneOf(card)}`}>{card.rank}</span>
        <div>
          <h3>{card.name}</h3>
          <p>
            <span>{card.grade ? `${card.grade}급지` : '급지 미상'}</span>
            {card.cell_cnt ? <small>총 {card.cell_cnt}자리</small> : null}
          </p>
        </div>
        <button
          className="card-detail-button"
          aria-label={`${card.name} 상세 보기`}
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
        >
          <ChevronRight size={20} />
        </button>
      </div>
      <Availability card={card} />
      {demotion && <Notice tone="warn">{demotion}</Notice>}
      <div className="card-footer">
        <span><Car size={14} /> {card.drive_min ?? '—'}분</span>
        <span><Footprints size={14} /> {card.walk_min ?? '—'}분</span>
        <strong>
          {card.fare.total === null ? '요금 계산 불가' : <>후불 <b>{fare.main}</b></>}
          {fare.sub && <small>{fare.sub}</small>}
        </strong>
      </div>
    </article>
  );
}

function Home({
  origin,
  onOrigin,
  onUseCurrent,
  locating,
  originError,
  destination,
  onDestination,
  onMap,
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
  origin: Place | null;
  onOrigin: (place: Place | null) => void;
  onUseCurrent: () => void;
  locating: boolean;
  originError: string | null;
  destination: Place | null;
  onDestination: (place: Place | null) => void;
  onMap: () => void;
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
  const [originQuery, setOriginQuery] = useState(origin?.name ?? '');
  const [originPlaces, setOriginPlaces] = useState<Place[]>([]);
  const [originOpen, setOriginOpen] = useState(false);
  const [originSearching, setOriginSearching] = useState(false);
  const [originSearchError, setOriginSearchError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<'departure' | 'duration' | null>(null);

  useEffect(() => {
    setQuery(destination?.name ?? '');
  }, [destination]);

  useEffect(() => {
    setOriginQuery(origin?.name ?? '');
  }, [origin]);

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

  useEffect(() => {
    const term = originQuery.trim();
    if (!originOpen || term.length < 2 || origin?.name === term) {
      setOriginPlaces([]);
      setOriginSearchError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setOriginSearching(true);
      setOriginSearchError(null);
      try {
        const found = await searchPlaces(term, controller.signal);
        setOriginPlaces(found.places);
      } catch (problem) {
        if (controller.signal.aborted) return;
        setOriginPlaces([]);
        setOriginSearchError(problem instanceof ApiProblem ? problem.userMessage : '출발지 검색에 실패했어요.');
      } finally {
        if (!controller.signal.aborted) setOriginSearching(false);
      }
    }, 300);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [originQuery, originOpen, origin]);

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
        <label className="field-label" htmlFor="origin">어디서 출발하세요?</label>
        <div className="origin-row">
          <div className="search-field compact">
            <Navigation size={18} />
            <input
              id="origin"
              value={originQuery}
              onChange={(event) => { setOriginQuery(event.target.value); setOriginOpen(true); onOrigin(null); }}
              onFocus={() => setOriginOpen(true)}
              placeholder="출발지 검색"
              autoComplete="off"
            />
            {originQuery && <button aria-label="출발지 지우기" onClick={() => { setOriginQuery(''); onOrigin(null); }}><X size={14} /></button>}
          </div>
          <button className="locate-button" onClick={onUseCurrent} disabled={locating}>
            <LocateFixed size={18} /><span>{locating ? '확인 중' : '현재 위치'}</span>
          </button>
        </div>
        {originError && <p className="inline-error">{originError}</p>}
        {originOpen && originQuery.trim().length >= 2 && origin?.name !== originQuery.trim() && (
          <div className="suggestions origin-suggestions">
            {originSearching && <p className="suggestion-state">검색 중…</p>}
            {!originSearching && originSearchError && <p className="suggestion-state warn">{originSearchError}</p>}
            {!originSearching && !originSearchError && !originPlaces.length && <p className="suggestion-state">검색 결과가 없어요.</p>}
            {originPlaces.map((place) => (
              <button
                key={`origin-${place.name}-${place.lat}-${place.lng}`}
                onClick={() => { onOrigin(place); setOriginQuery(place.name); setOriginOpen(false); }}
              >
                <Navigation size={17} />
                <span><strong>{place.name}</strong><small>{place.road_address ?? place.address ?? ''}</small></span>
              </button>
            ))}
          </div>
        )}

        <div className="divider" />
        <label className="field-label" htmlFor="destination">어디로 가세요?</label>
        <div className="destination-row">
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
          <button className="destination-map-button" aria-label="지도에서 목적지 선택" onClick={onMap}>
            <Map size={21} /><span>지도</span>
          </button>
        </div>
        {destination && <p className="address">{destination.road_address ?? destination.address ?? ''}</p>}

        {open && query.trim().length >= 2 && (
          <div className="suggestions destination-suggestions">
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
        <div className="section-heading"><span>얼마나 주차하세요?</span><button onClick={() => setSheet('duration')}>직접 설정 <ChevronRight size={15} /></button></div>
        <div className="duration-grid">
          {durations.map((minute) => (
            <button key={minute} className={minutes === minute ? 'active' : ''} onClick={() => onMinutes(minute)}>
              {minute < 60 ? `${minute}분` : `${minute / 60}시간`}
            </button>
          ))}
        </div>
        {!durations.includes(minutes) && <button className="custom-selection" onClick={() => setSheet('duration')}>{durationLabel(minutes)} 주차 <ChevronRight size={15} /></button>}

        <div className="divider" />
        <div className="section-heading"><span>언제 출발하세요?</span><button onClick={() => setSheet('departure')}>직접 설정 <ChevronRight size={15} /></button></div>
        <div className="depart-grid">
          <button className={departInMinutes === 0 ? 'active' : ''} onClick={() => onDepart(0)}>
            <span><Clock3 size={15} /> 지금 출발</span><small>현재 시각 기준</small>
          </button>
          <button className={departInMinutes > 0 ? 'active' : ''} onClick={() => setSheet('departure')}>
            <span><CalendarDays size={15} /> {departInMinutes > 0 ? departureLabel(departInMinutes) : '나중에 출발'}</span>
            <small>{departInMinutes > 0 ? `지금으로부터 ${departInMinutes}분 뒤` : '5분 단위로 선택'}</small>
          </button>
        </div>
      </section>

      {sheet && (
        <TimeSheet
          kind={sheet}
          value={sheet === 'departure' ? departInMinutes : minutes}
          onClose={() => setSheet(null)}
          onConfirm={sheet === 'departure' ? onDepart : onMinutes}
        />
      )}

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

      <button className="primary-cta" disabled={!origin || !destination || busy} onClick={onSubmit}>
        {busy ? '추천을 계산하고 있어요…' : <>주차장 찾기 <ArrowRight size={20} /></>}
      </button>
      {(!origin || !destination) && <p className="ai-note">{!origin ? '출발 위치를 먼저 선택해 주세요' : '목적지를 검색해 선택하면 추천을 시작해요'}</p>}
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
  const [selectedParkingId, setSelectedParkingId] = useState<number | null>(null);
  const [mapExpanded, setMapExpanded] = useState(false);
  const cards = sortedBy(result, sort);
  const notice = resultNotice(result);
  const gateBlocked = cards.length > 0 && cards.every((c) => c.prediction_status !== 'available');
  const hasDriveEstimate = cards.some((card) => card.drive_estimated ?? card.estimated);
  const hasWalkEstimate = cards.some((card) => card.walk_estimated === true);
  const hasUnknownAccess = cards.some((card) => accessWarning(card) !== null);
  const hasUnverifiedFare = cards.some((card) => fareLabel(card).note !== null);

  useEffect(() => {
    setSelectedParkingId((current) => (
      current !== null && cards.some((card) => card.parking_id === current)
        ? current
        : (cards[0]?.parking_id ?? null)
    ));
  }, [cards]);

  const selectFromMap = useCallback((parkingId: number) => {
    setSelectedParkingId(parkingId);
    document.getElementById(`parking-card-${parkingId}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    });
  }, []);

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
          <span>{durationLabel(minutes)} 주차</span>
          <em>{result.candidate_count}곳 · 반경 {result.radius_used / 1000}km</em>
        </div>
      </header>

      <div className="sort-tabs">
        <button className={sort === 'recommend' ? 'active' : ''} onClick={() => setSort('recommend')}>추천순</button>
        <button className={sort === 'fare' ? 'active' : ''} onClick={() => setSort('fare')}>요금 낮은순</button>
        <button className={sort === 'walk' ? 'active' : ''} onClick={() => setSort('walk')}>도보 짧은순</button>
      </div>

      <div className={`results-map ${mapExpanded ? 'expanded' : ''}`}>
        <ResultMap
          cards={cards}
          destination={destination}
          selected={selectedParkingId}
          onSelect={selectFromMap}
          expanded={mapExpanded}
          onExpandedChange={setMapExpanded}
        />
      </div>

      <section className="result-list">
        {busy && <p className="state-notice">최신 정보를 다시 불러오는 중이에요…</p>}
        {notice && <Notice tone="warn">{notice}</Notice>}
        {cards.some((card) => card.route_traffic_basis === 'current') && (
          <Notice>미래 출발의 이동 시간은 현재 교통을 기준으로 각 주차장별로 계산했어요.</Notice>
        )}
        {gateBlocked && <Notice>현재 시간대에는 혼잡도 예측을 제공하지 않아요. 위치와 요금을 기준으로 안내해요.</Notice>}
        {hasDriveEstimate && (
          <Notice tone="warn">일부 주차장의 차량 경로를 불러오지 못해 운전 시간은 거리 기반 추정치예요.</Notice>
        )}
        {hasWalkEstimate && (
          <Notice tone="warn">일부 주차장의 도보 경로를 불러오지 못해 도보 시간은 거리 기반 추정치예요.</Notice>
        )}
        {hasUnknownAccess && (
          <Notice tone="warn">일부 주차장은 입·출차 시간이 아직 확인되지 않아 방문 전 확인이 필요해요.</Notice>
        )}
        {hasUnverifiedFare && (
          <Notice tone="warn">일부 요금은 조사가 확정되지 않은 운영시간 기준으로 계산했어요.</Notice>
        )}
        {result.service?.data_status === 'stale' && (
          <Notice tone="warn">실시간 관측이 최신이 아니에요. 현재 값은 참고만 해주세요.</Notice>
        )}

        {cards.map((card) => (
          <ParkingCardView
            key={card.parking_id}
            card={card}
            selected={selectedParkingId === card.parking_id}
            onMapSelect={() => setSelectedParkingId(card.parking_id)}
            onOpen={() => onSelect(card)}
          />
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
          <span>{card.grade ? `${card.grade}급지` : '급지 미상'} · 총 {card.cell_cnt ?? '—'}자리</span>
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
        {(card.drive_estimated ?? card.estimated) && (
          <Notice tone="warn">차량 경로를 불러오지 못해 운전 시간은 거리 기반 추정치예요.</Notice>
        )}
        {card.walk_estimated && (
          <Notice tone="warn">도보 경로를 불러오지 못해 도보 시간은 거리 기반 추정치예요.</Notice>
        )}
      </section>

      <section className="detail-section">
        <h3>예상 주차 요금</h3>
        <div className="fare-main">
          <span>{durationLabel(minutes)} 주차 · 후불</span>
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
  const [origin, setOrigin] = useState<Place | null>(null);
  const [locating, setLocating] = useState(false);
  const [originError, setOriginError] = useState<string | null>(null);
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

  const useCurrentLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setOriginError('이 브라우저는 현재 위치를 지원하지 않아요. 출발지를 검색해 주세요.');
      return;
    }
    setLocating(true);
    setOriginError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setOrigin({
          name: '현재 위치',
          address: null,
          road_address: null,
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          in_anyang: true,
          distance_from_anyang_m: 0,
          category: '현재 위치',
        });
        setLocating(false);
      },
      (problem) => {
        const message = problem.code === problem.PERMISSION_DENIED
          ? '위치 권한이 꺼져 있어요. 허용하거나 출발지를 검색해 주세요.'
          : '현재 위치를 확인하지 못했어요. 출발지를 검색해 주세요.';
        setOriginError(message);
        setLocating(false);
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60_000 },
    );
  }, []);

  const run = useCallback(async () => {
    if (!origin || !destination) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy(true);
    setError(null);
    try {
      const found = await recommend(
        {
          destination: { lat: destination.lat, lng: destination.lng },
          origin: { lat: origin.lat, lng: origin.lng },
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
  }, [origin, destination, minutes, departInMinutes, preferences]);

  return (
    <div className="app-shell">
      <div className="phone-frame">
        {screen === 'home' && (
          <Home
            origin={origin}
            onOrigin={(place) => { setOrigin(place); setOriginError(null); }}
            onUseCurrent={useCurrentLocation}
            locating={locating}
            originError={originError}
            destination={destination}
            onDestination={setDestination}
            onMap={() => setScreen('map')}
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
        {screen === 'map' && (
          <DestinationMap
            initial={destination}
            onBack={() => setScreen('home')}
            onConfirm={(place) => { setDestination(place); setScreen('home'); }}
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
