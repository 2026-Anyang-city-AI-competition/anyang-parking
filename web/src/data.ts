export type ParkingLot = {
  id: number;
  name: string;
  address: string;
  type: string;
  grade: string;
  spaces: number;
  current: number;
  predicted: number;
  fullProbability: number;
  drive: number;
  walk: number;
  distance: number;
  fare: number;
  peakFare: number;
  arrival: string;
  color: 'green' | 'yellow' | 'orange';
  reason: string;
};

export const parkingLots: ParkingLot[] = [
  {
    id: 1,
    name: '평촌중앙공원 공영주차장',
    address: '안양시 동안구 관평로 149',
    type: '노외·계측',
    grade: '1급지',
    spaces: 350,
    current: 230,
    predicted: 195,
    fullProbability: 23,
    drive: 15,
    walk: 8,
    distance: 0.8,
    fare: 1200,
    peakFare: 3000,
    arrival: '14:15',
    color: 'green',
    reason: '도착할 때 여유가 가장 많고 요금도 합리적이에요.',
  },
  {
    id: 2,
    name: '시민대로 노상주차장',
    address: '안양시 동안구 시민대로 230',
    type: '노상·AI 추정',
    grade: '2급지',
    spaces: 32,
    current: 11,
    predicted: 7,
    fullProbability: 18,
    drive: 12,
    walk: 3,
    distance: 0.2,
    fare: 1600,
    peakFare: 3200,
    arrival: '14:15',
    color: 'yellow',
    reason: '목적지까지 가장 빨리 걸어갈 수 있어요.',
  },
  {
    id: 3,
    name: '안양시청 지하공영주차장',
    address: '안양시 동안구 시민대로 235',
    type: '노외·계측',
    grade: '도착 시 혼잡',
    spaces: 180,
    current: 41,
    predicted: 22,
    fullProbability: 61,
    drive: 14,
    walk: 2,
    distance: 0.1,
    fare: 2000,
    peakFare: 5000,
    arrival: '14:15',
    color: 'orange',
    reason: '가깝지만 도착 시 혼잡할 가능성이 있어 순위가 내려갔어요.',
  },
];

export const searchSuggestions = [
  { name: '안양시청', address: '안양시 동안구 시민대로 235' },
  { name: '범계역', address: '안양시 동안구 동안로 130' },
  { name: '평촌중앙공원', address: '안양시 동안구 관평로 149' },
  { name: '안양역', address: '안양시 만안구 만안로 232' },
];
