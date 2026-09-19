import { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Car,
  Check,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Clock3,
  Footprints,
  Info,
  LocateFixed,
  Map,
  MapPin,
  Minus,
  Navigation,
  Plus,
  Search,
  Sparkles,
  WifiOff,
  X,
} from 'lucide-react';
import {
  scenarios,
  defaultScenarioKey,
  searchSuggestions,
  severity,
  displayRatio,
  splitLive,
  offlineCardsOf,
  type Card,
  type Scenario,
} from './data';

type Screen = 'home' | 'map' | 'results' | 'detail';
type SortMode = 'fare' | 'walk';

const durations = [30, 60, 120, 180];

function isDevMode() {
  try {
    return new URLSearchParams(window.location.search).get('dev') === '1';
  } catch {
    return false;
  }
}

function IconButton({ children, label, onClick }: { children: React.ReactNode; label: string; onClick?: () => void }) {
  return <button className="icon-button" aria-label={label} onClick={onClick}>{children}</button>;
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

/** 개발용 시나리오 전환 바. ?dev=1 일 때만 보인다(제출 스크린샷엔 안 나옴). */
function ScenarioBar({ scenarioKey, onChange }: { scenarioKey: string; onChange: (key: string) => void }) {
  if (!isDevMode()) return null;
  return (
    <div className="scenario-bar">
      <span>dev</span>
      {scenarios.map((s) => (
        <button key={s.key} className={s.key === scenarioKey ? 'active' : ''} onClick={() => onChange(s.key)}>
          {s.label}
        </button>
      ))}
    </div>
  );
}

function MockMap({
  cards,
  interactive = false,
  selected,
  onSelect,
}: {
  cards: Card[];
  interactive?: boolean;
  selected?: number;
  onSelect?: (id: number) => void;
}) {
  const pins = cards.slice(0, 3);
  return (
    <div className={`mock-map ${interactive ? 'interactive' : 'compact'}`}>
      <div className="river" />
      <div className="park-area">평촌중앙공원</div>
      <span className="road-name road-one">시민대로</span>
      <span className="road-name road-two">관평로</span>
      <span className="map-label city-hall">목적지</span>
      <button className="destination-pin" aria-label="선택한 목적지"><MapPin size={24} fill="currentColor" /></button>
      {pins.map((card, index) => (
        <button
          key={card.parking_id}
          aria-label={`${card.name} ${card.avail_now ?? '정보없음'}대 주차중`}
          className={`parking-pin ${severity(card)} pin-${index + 1} ${selected === card.parking_id ? 'selected' : ''}`}
          onClick={() => onSelect?.(card.parking_id)}
        >
          {interactive ? 'P' : index + 1}
        </button>
      ))}
    </div>
  );
}

function MapLegend() {
  return (
    <div className="map-legend">
      <strong>혼잡도(만차확률 · 없으면 현재 점유율)</strong>
      <div><span className="dot green" />여유</div>
      <div><span className="dot yellow" />보통</div>
      <div><span className="dot orange" />혼잡</div>
      <div><span className="dot grey" />정보없음</div>
    </div>
  );
}

function Home({ scenario, onNavigate }: { scenario: Scenario; onNavigate: (screen: Screen) => void }) {
  const [destination, setDestination] = useState(scenario.destinationName);
  const [duration, setDuration] = useState(scenario.data.park_minutes || 120);
  const [schedule, setSchedule] = useState<'now' | 'later'>('now');
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showTime, setShowTime] = useState(false);

  useEffect(() => setDestination(scenario.destinationName), [scenario.key]);
  useEffect(() => setDuration(scenario.data.park_minutes || 120), [scenario.key]);

  const filteredSuggestions = searchSuggestions.filter((item) => item.name.includes(destination) || item.address.includes(destination));
  const { live } = splitLive(scenario.data.cards);

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
            value={destination}
            onChange={(event) => { setDestination(event.target.value); setShowSuggestions(true); }}
            onFocus={() => setShowSuggestions(true)}
            placeholder="장소명 또는 주소 검색"
          />
          {destination && <button aria-label="입력 지우기" onClick={() => setDestination('')}><X size={14} /></button>}
        </div>
        <p className="address">{scenario.destinationAddress}</p>

        {showSuggestions && destination && (
          <div className="suggestions">
            {(filteredSuggestions.length ? filteredSuggestions : searchSuggestions).map((item) => (
              <button key={item.name} onClick={() => { setDestination(item.name); setShowSuggestions(false); }}>
                <MapPin size={17} /><span><strong>{item.name}</strong><small>{item.address}</small></span>
              </button>
            ))}
          </div>
        )}

        <button className="map-pick" onClick={() => onNavigate('map')}><Map size={18} /> 지도에서 선택</button>

        <div className="divider" />
        <div className="section-heading">
          <span>얼마나 주차하세요?</span>
          <button onClick={() => setDuration(120)}>직접 입력 <ChevronRight size={15} /></button>
        </div>
        <div className="duration-grid">
          {durations.map((minute) => (
            <button key={minute} className={duration === minute ? 'active' : ''} onClick={() => setDuration(minute)}>
              {minute < 60 ? `${minute}분` : `${minute / 60}시간`}
            </button>
          ))}
        </div>

        <div className="divider" />
        <span className="field-label">언제 출발하세요?</span>
        <div className="depart-grid">
          <button className={schedule === 'now' ? 'active' : ''} onClick={() => setSchedule('now')}>
            <span><Clock3 size={15} /> 지금 출발</span><small>약 {scenario.data.depart_at} 도착 예상</small>
          </button>
          <button className={schedule === 'later' ? 'active' : ''} onClick={() => { setSchedule('later'); setShowTime(true); }}>
            <span><CalendarDays size={15} /> 시간 지정</span><small>날짜 · 시간 선택</small>
          </button>
        </div>
      </section>

      <button className="nearby-card" onClick={() => onNavigate('results')}>
        <span className="status-dot green"><span /></span>
        <span><strong>{scenario.destinationName} 주변 공영주차장 {live.length}곳</strong><small>{scenario.dayLabel} {scenario.data.depart_at} 도착 기준</small></span>
        <ChevronRight size={20} />
      </button>

      <button className="primary-cta" onClick={() => onNavigate('results')}>주차장 찾기 <ArrowRight size={20} /></button>
      <p className="ai-note">차단기가 없어 계측되지 않는 노상주차장은<br />AI가 빈자리를 추정해 함께 보여드려요</p>

      {showTime && (
        <div className="modal-backdrop" onClick={() => setShowTime(false)}>
          <section className="time-sheet" onClick={(event) => event.stopPropagation()}>
            <div className="sheet-handle" />
            <div className="sheet-title"><div><strong>출발 시간 지정</strong><span>도착 시점의 혼잡도를 예측해요</span></div><button onClick={() => setShowTime(false)}><X /></button></div>
            <label>날짜</label>
            <div className="date-options"><button className="active">오늘</button><button>내일</button><button>직접 선택<br /><small>날짜 열기</small></button></div>
            <label htmlFor="time">출발 시간</label>
            <input id="time" className="time-input" type="time" defaultValue="16:30" />
            <button className="primary-cta" onClick={() => setShowTime(false)}>이 시간으로 설정</button>
          </section>
        </div>
      )}
    </main>
  );
}

function MapScreen({ scenario, onNavigate }: { scenario: Scenario; onNavigate: (screen: Screen) => void }) {
  const { live } = splitLive(scenario.data.cards);
  const [selected, setSelected] = useState<number | undefined>(live[0]?.parking_id);
  const selectedCard = live.find((c) => c.parking_id === selected) ?? live[0];
  return (
    <main className="screen map-screen">
      <header className="light-header"><IconButton label="뒤로" onClick={() => onNavigate('home')}><ArrowLeft /></IconButton><h1>지도에서 목적지 선택</h1></header>
      <div className="floating-search"><Search size={19} /><input aria-label="장소 검색" placeholder="장소명 또는 주소 검색" defaultValue={scenario.destinationName} /></div>
      <MapLegend />
      <MockMap interactive cards={live} selected={selected} onSelect={setSelected} />
      <div className="map-controls"><button aria-label="확대"><Plus /></button><button aria-label="축소"><Minus /></button><button aria-label="내 위치"><LocateFixed /></button></div>
      <button className="toggle-density"><Info size={15} /> 혼잡도 끄기</button>
      <section className="map-sheet">
        <span>선택한 위치</span>
        <h2>{scenario.destinationAddress}</h2>
        <p>{scenario.destinationName} 인근 · 공영주차장 {live.length}곳</p>
        {selectedCard && (
          <div className="selected-lot" style={{ display: 'flex' }}>
            <span className={`rank ${severity(selectedCard)}`}>P</span>
            <div><strong>{selectedCard.name}</strong><small>{selectedCard.avail_now ?? '정보없음'}대 주차중 · 도보 {selectedCard.walk_min ?? '?'}분</small></div>
          </div>
        )}
        <button className="primary-cta" onClick={() => onNavigate('results')}>이 위치를 목적지로 설정</button>
      </section>
    </main>
  );
}

function Availability({ card }: { card: Card }) {
  const ratio = displayRatio(card);
  return (
    <div className={`availability ${card.avail_pred == null ? 'single' : ''}`}>
      <div className={`availability-box current ${severity(card)}`}>
        <span><i /> 지금 · {card.is_live === false ? '실시간 미제공' : '실시간'}</span>
        <strong>
          {card.avail_now ?? '—'}
          <small>대 / {card.cell_cnt ?? '?'}면</small>
        </strong>
      </div>
      {card.avail_pred != null && (
        <div className="availability-box future">
          <span><Sparkles size={11} /> {card.arrive_at} 도착 · AI 예측</span>
          <strong>
            {ratio}
            <small>
              % 점유
              {card.full_prob != null && <em> · 만차확률 {Math.round(card.full_prob * 100)}%</em>}
            </small>
          </strong>
        </div>
      )}
    </div>
  );
}

function ParkingCard({ card, rank, onOpen }: { card: Card; rank: number; onOpen: () => void }) {
  const ratio = displayRatio(card);
  return (
    <article className="parking-card" onClick={onOpen}>
      <div className="card-title">
        <span className={`rank ${severity(card)}`}>{rank}</span>
        <div>
          <h3>{card.name}</h3>
          <p>
            {card.is_live === false && <span className="badge-tag off"><WifiOff size={9} /> 실시간 미제공</span>}
            {card.estimated && <span className="badge-tag estimated">추정치</span>}
            {card.walk_far_warning && <span className="badge-tag warn">멀어요</span>}
            <small>총 {card.cell_cnt ?? '?'}면</small>
          </p>
        </div>
        <ChevronRight size={20} />
      </div>
      <Availability card={card} />
      {card.is_live === false && card.unavailable_note && (
        <p className="model-message"><CircleHelp size={14} /> {card.unavailable_note}</p>
      )}
      {ratio != null && (
        <div className="capacity-row">
          <div><span className={severity(card)} style={{ width: `${Math.max(8, ratio)}%` }} /></div>
          <small>도착 시점 예상 점유율 {ratio}%</small>
        </div>
      )}
      <div className="card-footer">
        <span><Car size={14} /> {card.drive_min ?? '?'}분</span>
        <span><Footprints size={14} /> {card.walk_min ?? '?'}분</span>
        <strong>
          후불 <b>{card.fare_payg != null ? `${card.fare_payg.toLocaleString()}원` : '계산 불가'}</b>
          <small>
            일일권 {card.fare_daily_pass != null ? `${card.fare_daily_pass.toLocaleString()}원` : '-'}
            {card.daily_pass_better && ' · 입차 시 구매 추천'}
          </small>
        </strong>
      </div>
    </article>
  );
}

function Results({ scenario, onNavigate, onSelect }: { scenario: Scenario; onNavigate: (screen: Screen) => void; onSelect: (card: Card) => void }) {
  const [sort, setSort] = useState<SortMode>('fare');
  const [showOffline, setShowOffline] = useState(false);
  const ranked = sort === 'fare' ? scenario.data.by_fare : scenario.data.by_walk;
  const { live } = splitLive(ranked);
  const offline = useMemo(() => offlineCardsOf(scenario), [scenario]);

  return (
    <main className="screen results-screen">
      <header className="results-header">
        <IconButton label="뒤로" onClick={() => onNavigate('home')}><ArrowLeft /></IconButton>
        <div><h1>{scenario.destinationName}</h1><p>{scenario.destinationAddress}</p></div>
        <button className="outline-button" onClick={() => onNavigate('home')}>조건 변경</button>
        <div className="result-meta">
          <span><Clock3 size={13} /> {scenario.dayLabel} {scenario.data.depart_at} 도착</span>
          <span>{scenario.data.park_minutes}분 주차</span>
          <em>{live.length}곳{offline.length ? ` · 미제공 ${offline.length}` : ''}</em>
        </div>
      </header>
      <div className="sort-tabs sort-tabs-2">
        <button className={sort === 'fare' ? 'active' : ''} onClick={() => setSort('fare')}>최저요금순</button>
        <button className={sort === 'walk' ? 'active' : ''} onClick={() => setSort('walk')}>도보 최단순</button>
      </div>
      <div className="results-map"><MockMap cards={live} /><button onClick={() => onNavigate('map')}><Navigation size={15} /> 지도 크게 보기</button></div>
      <section className="result-list">
        <div className="recommend-note"><Sparkles size={15} /><span><strong>도착 시 여유</strong>와 <strong>주차 요금</strong>을 함께 비교했어요.</span></div>
        {live.map((card, i) => (
          <ParkingCard key={card.parking_id} card={card} rank={i + 1} onOpen={() => { onSelect(card); onNavigate('detail'); }} />
        ))}
        {offline.length > 0 && (
          <div className="unavailable-section">
            <button className="unavailable-toggle" onClick={() => setShowOffline((v) => !v)}>
              <WifiOff size={13} /> 실시간 미제공 {offline.length}곳
              {showOffline ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
            </button>
            {showOffline && offline.map((card) => (
              <ParkingCard key={card.parking_id} card={card} rank={0} onOpen={() => { onSelect(card); onNavigate('detail'); }} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function Detail({ card, scenario, onNavigate }: { card: Card; scenario: Scenario; onNavigate: (screen: Screen) => void }) {
  return (
    <main className="screen detail-screen">
      <header className="light-header sticky"><IconButton label="뒤로" onClick={() => onNavigate('results')}><ArrowLeft /></IconButton><h1>주차장 상세</h1></header>
      <section className="detail-hero">
        <span className={`rank large ${severity(card)}`}><Check size={18} /></span>
        <div><p>{scenario.destinationName} 추천 후보</p><h2>{card.name}</h2><span>도착 {card.arrive_at}</span></div>
      </section>
      <section className="detail-section highlight">
        <h3>지금 · 도착할 때 비교</h3>
        <Availability card={card} />
      </section>
      <section className="detail-section">
        <h3>예상 이동 시간</h3>
        <div className="journey">
          <div><span><Car /></span><strong>차로 {card.drive_min ?? '?'}분</strong><small>현재 위치 → 주차장</small></div>
          <ArrowRight />
          <div><span><Footprints /></span><strong>도보 {card.walk_min ?? '?'}분</strong><small>주차장 → 목적지</small></div>
        </div>
      </section>
      <section className="detail-section">
        <h3>예상 주차 요금</h3>
        <div className="fare-main">
          <span>{scenario.data.park_minutes}분 주차(후불)</span>
          <strong>{card.fare_payg != null ? `${card.fare_payg.toLocaleString()}원` : '계산 불가'}</strong>
        </div>
        <dl className="fare-list">
          <div><dt>선불 일일권</dt><dd>{card.fare_daily_pass != null ? `${card.fare_daily_pass.toLocaleString()}원` : '-'}</dd></div>
        </dl>
        {card.daily_pass_better ? (
          <p className="notice highlight-notice"><Info size={14} /> 이번 주차는 <strong>입차 시 일일권을 구매</strong>하는 쪽이 더 저렴해요.</p>
        ) : (
          <p className="notice"><Info size={14} /> 실제 요금은 입·출차 시각과 감면 여부에 따라 달라질 수 있어요.</p>
        )}
      </section>
      <section className="detail-section">
        <h3>주차장 정보</h3>
        <div className="info-grid">
          <span>운영시간<strong>{card.operating_hours ?? '확인 필요'}</strong></span>
          <span>전체 주차면<strong>{card.cell_cnt ?? '?'}면</strong></span>
          <span>실시간 상태<strong className={card.is_live === false ? '' : 'safe'}>{card.is_live === false ? '미제공' : '정상'}</strong></span>
          <span>이동시간<strong>{card.estimated ? '추정치' : '실측'}</strong></span>
        </div>
      </section>
      <div className="bottom-action"><button className="secondary-cta" onClick={() => onNavigate('map')}><Map size={19} /> 지도 보기</button><button className="primary-cta"><Navigation size={18} /> 길찾기</button></div>
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('home');
  const [scenarioKey, setScenarioKey] = useState(defaultScenarioKey);
  const scenario = scenarios.find((s) => s.key === scenarioKey) ?? scenarios[0];
  const [selectedCard, setSelectedCard] = useState<Card>(scenario.data.cards[0]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [screen]);

  useEffect(() => {
    setSelectedCard(scenario.data.cards[0]);
  }, [scenarioKey]);

  return (
    <div className="app-shell">
      <div className="phone-frame">
        <ScenarioBar scenarioKey={scenarioKey} onChange={setScenarioKey} />
        {screen === 'home' && <Home scenario={scenario} onNavigate={setScreen} />}
        {screen === 'map' && <MapScreen scenario={scenario} onNavigate={setScreen} />}
        {screen === 'results' && <Results scenario={scenario} onNavigate={setScreen} onSelect={setSelectedCard} />}
        {screen === 'detail' && <Detail card={selectedCard} scenario={scenario} onNavigate={setScreen} />}
      </div>
    </div>
  );
}
