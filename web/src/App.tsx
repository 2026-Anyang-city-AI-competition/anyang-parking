import { useEffect, useMemo, useState } from 'react';
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
  Minus,
  Navigation,
  Plus,
  Search,
  Sparkles,
  X,
} from 'lucide-react';
import { parkingLots, searchSuggestions, type ParkingLot } from './data';

type Screen = 'home' | 'map' | 'results' | 'detail';
type SortMode = 'recommend' | 'fare' | 'walk';

const durations = [30, 60, 120, 180];

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

function MockMap({ interactive = false, selected, onSelect }: { interactive?: boolean; selected?: number; onSelect?: (id: number) => void }) {
  return (
    <div className={`mock-map ${interactive ? 'interactive' : 'compact'}`}>
      <div className="river" />
      <div className="park-area">평촌중앙공원</div>
      <span className="road-name road-one">시민대로</span>
      <span className="road-name road-two">관평로</span>
      <span className="map-label city-hall">안양시청</span>
      <button className="destination-pin" aria-label="선택한 목적지"><MapPin size={24} fill="currentColor" /></button>
      {parkingLots.map((lot, index) => (
        <button
          key={lot.id}
          aria-label={`${lot.name} ${lot.current}면 여유`}
          className={`parking-pin ${lot.color} pin-${index + 1} ${selected === lot.id ? 'selected' : ''}`}
          onClick={() => onSelect?.(lot.id)}
        >
          {interactive ? 'P' : lot.id}
        </button>
      ))}
    </div>
  );
}

function Home({ onNavigate }: { onNavigate: (screen: Screen) => void }) {
  const [destination, setDestination] = useState('안양시청');
  const [duration, setDuration] = useState(120);
  const [schedule, setSchedule] = useState<'now' | 'later'>('now');
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showTime, setShowTime] = useState(false);

  const filteredSuggestions = searchSuggestions.filter((item) => item.name.includes(destination) || item.address.includes(destination));

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
        <p className="address">안양시 동안구 시민대로 235</p>

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
            <span><Clock3 size={15} /> 지금 출발</span><small>약 14:00 도착 예상</small>
          </button>
          <button className={schedule === 'later' ? 'active' : ''} onClick={() => { setSchedule('later'); setShowTime(true); }}>
            <span><CalendarDays size={15} /> 시간 지정</span><small>날짜 · 시간 선택</small>
          </button>
        </div>
      </section>

      <button className="nearby-card" onClick={() => onNavigate('results')}>
        <span className="status-dot green"><span /></span>
        <span><strong>안양시청 주변 공영주차장 8곳</strong><small>지금 평균 주차면 62% · 노상 3곳 포함</small></span>
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
            <div className="date-options"><button className="active">오늘<br /><small>9월 15일</small></button><button>내일<br /><small>9월 16일</small></button><button>직접 선택<br /><small>날짜 열기</small></button></div>
            <label htmlFor="time">출발 시간</label>
            <input id="time" className="time-input" type="time" defaultValue="16:30" />
            <button className="primary-cta" onClick={() => setShowTime(false)}>이 시간으로 설정</button>
          </section>
        </div>
      )}
    </main>
  );
}

function MapScreen({ onNavigate }: { onNavigate: (screen: Screen) => void }) {
  const [selected, setSelected] = useState(1);
  const selectedLot = parkingLots.find((lot) => lot.id === selected)!;
  return (
    <main className="screen map-screen">
      <header className="light-header"><IconButton label="뒤로" onClick={() => onNavigate('home')}><ArrowLeft /></IconButton><h1>지도에서 목적지 선택</h1></header>
      <div className="floating-search"><Search size={19} /><input aria-label="장소 검색" placeholder="장소명 또는 주소 검색" /></div>
      <div className="map-legend"><strong>주차장 혼잡도</strong><div><span className="dot green" />여유 <span className="dot yellow" />보통</div><div><span className="dot orange" />혼잡 <span className="dot red" />만차</div><small><span className="dash-dot" /> 점선 = 노상 · AI 추정</small></div>
      <MockMap interactive selected={selected} onSelect={setSelected} />
      <div className="map-controls"><button aria-label="확대"><Plus /></button><button aria-label="축소"><Minus /></button><button aria-label="내 위치"><LocateFixed /></button></div>
      <button className="toggle-density"><Info size={15} /> 혼잡도 끄기</button>
      <section className="map-sheet">
        <span>선택한 위치</span>
        <h2>안양시 동안구 시민대로 235</h2>
        <p>안양시청 인근 · 반경 1km 공영주차장 8곳</p>
        <div className="selected-lot"><span className={`rank ${selectedLot.color}`}>P</span><div><strong>{selectedLot.name}</strong><small>{selectedLot.current}면 여유 · 도보 {selectedLot.walk}분</small></div></div>
        <button className="primary-cta" onClick={() => onNavigate('results')}>이 위치를 목적지로 설정</button>
      </section>
    </main>
  );
}

function Availability({ lot }: { lot: ParkingLot }) {
  return (
    <div className="availability">
      <div className={`availability-box current ${lot.color}`}><span><i /> 지금 · {lot.type.includes('AI') ? 'AI 추정' : '실시간'}</span><strong>{lot.current}<small>면 / {lot.spaces}</small></strong></div>
      <div className="availability-box future"><span><Sparkles size={11} /> {lot.arrival} 도착 · AI 예측</span><strong>{lot.predicted}<small>면 <em>만차 {lot.fullProbability}%</em></small></strong></div>
    </div>
  );
}

function ParkingCard({ lot, onOpen }: { lot: ParkingLot; onOpen: () => void }) {
  const ratio = Math.max(8, Math.round((lot.predicted / lot.spaces) * 100));
  return (
    <article className="parking-card" onClick={onOpen}>
      <div className="card-title"><span className={`rank ${lot.color}`}>{lot.id}</span><div><h3>{lot.name}</h3><p><span>{lot.type}</span><span>{lot.grade}</span><small>총 {lot.spaces}면</small></p></div><ChevronRight size={20} /></div>
      <Availability lot={lot} />
      {lot.type.includes('AI') && <p className="model-message"><CircleHelp size={14} /> 차단기가 없어 실시간 계측이 안 되는 구간이에요. 인근 계측 주차장 53곳과 패턴으로 추정한 값입니다.</p>}
      <div className="capacity-row"><div><span className={lot.color} style={{ width: `${ratio}%` }} /></div><small>도착 시점 잔여 {ratio}%</small></div>
      <div className="card-footer"><span><Car size={14} /> {lot.drive}분</span><span><Footprints size={14} /> {lot.walk}분</span><span>· {lot.distance}km</span><strong>2시간 후 <b>{lot.fare.toLocaleString()}원</b><small>일일권 선불 {lot.peakFare.toLocaleString()}원</small></strong></div>
    </article>
  );
}

function Results({ onNavigate, onSelect }: { onNavigate: (screen: Screen) => void; onSelect: (lot: ParkingLot) => void }) {
  const [sort, setSort] = useState<SortMode>('recommend');
  const sorted = useMemo(() => [...parkingLots].sort((a, b) => sort === 'fare' ? a.fare - b.fare : sort === 'walk' ? a.walk - b.walk : a.id - b.id), [sort]);
  return (
    <main className="screen results-screen">
      <header className="results-header">
        <IconButton label="뒤로" onClick={() => onNavigate('home')}><ArrowLeft /></IconButton>
        <div><h1>안양시청</h1><p>안양시 동안구 시민대로 235</p></div>
        <button className="outline-button" onClick={() => onNavigate('home')}>조건 변경</button>
        <div className="result-meta"><span><Clock3 size={13} /> 평일 14:00 도착</span><span>2시간 주차</span><em>8곳 · 노상 3</em></div>
      </header>
      <div className="sort-tabs">
        <button className={sort === 'recommend' ? 'active' : ''} onClick={() => setSort('recommend')}>추천순</button>
        <button className={sort === 'fare' ? 'active' : ''} onClick={() => setSort('fare')}>요금 낮은순</button>
        <button className={sort === 'walk' ? 'active' : ''} onClick={() => setSort('walk')}>도보 짧은순</button>
      </div>
      <div className="results-map"><MockMap /><button onClick={() => onNavigate('map')}><Navigation size={15} /> 지도 크게 보기</button></div>
      <section className="result-list">
        <div className="recommend-note"><Sparkles size={15} /><span><strong>도착 시 여유</strong>와 <strong>주차 요금</strong>을 함께 비교했어요.</span></div>
        {sorted.map((lot) => <ParkingCard key={lot.id} lot={lot} onOpen={() => { onSelect(lot); onNavigate('detail'); }} />)}
        <button className="more-button">다른 공영주차장 5곳 더 보기</button>
      </section>
    </main>
  );
}

function Detail({ lot, onNavigate }: { lot: ParkingLot; onNavigate: (screen: Screen) => void }) {
  return (
    <main className="screen detail-screen">
      <header className="light-header sticky"><IconButton label="뒤로" onClick={() => onNavigate('results')}><ArrowLeft /></IconButton><h1>주차장 상세</h1></header>
      <section className="detail-hero">
        <span className={`rank large ${lot.color}`}>{lot.id}</span>
        <div><p>AI 추천 {lot.id}순위</p><h2>{lot.name}</h2><span>{lot.address}</span></div>
      </section>
      <section className="detail-section highlight"><h3>왜 추천했나요?</h3><p>{lot.reason}</p><Availability lot={lot} /></section>
      <section className="detail-section">
        <h3>예상 이동 시간</h3>
        <div className="journey"><div><span><Car /></span><strong>차로 {lot.drive}분</strong><small>현재 위치 → 주차장</small></div><ArrowRight /><div><span><Footprints /></span><strong>도보 {lot.walk}분</strong><small>주차장 → 목적지</small></div></div>
      </section>
      <section className="detail-section">
        <h3>예상 주차 요금</h3>
        <div className="fare-main"><span>2시간 주차</span><strong>{lot.fare.toLocaleString()}원</strong></div>
        <dl className="fare-list"><div><dt>기본 30분</dt><dd>600원</dd></div><div><dt>추가 10분당</dt><dd>300원</dd></div><div><dt>선불 일일권</dt><dd>{lot.peakFare.toLocaleString()}원</dd></div></dl>
        <p className="notice"><Info size={14} /> 실제 요금은 입·출차 시각과 감면 여부에 따라 달라질 수 있어요.</p>
      </section>
      <section className="detail-section"><h3>주차장 정보</h3><div className="info-grid"><span>운영시간<strong>24시간</strong></span><span>전체 주차면<strong>{lot.spaces}면</strong></span><span>주차장 유형<strong>{lot.type.split('·')[0]}</strong></span><span>데이터 상태<strong className="safe">정상</strong></span></div></section>
      <div className="bottom-action"><button className="secondary-cta" onClick={() => onNavigate('map')}><Map size={19} /> 지도 보기</button><button className="primary-cta"><Navigation size={18} /> 길찾기</button></div>
    </main>
  );
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('home');
  const [selectedLot, setSelectedLot] = useState(parkingLots[0]);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' });
  }, [screen]);

  return (
    <div className="app-shell">
      <div className="phone-frame">
        {screen === 'home' && <Home onNavigate={setScreen} />}
        {screen === 'map' && <MapScreen onNavigate={setScreen} />}
        {screen === 'results' && <Results onNavigate={setScreen} onSelect={setSelectedLot} />}
        {screen === 'detail' && <Detail lot={selectedLot} onNavigate={setScreen} />}
      </div>
    </div>
  );
}
