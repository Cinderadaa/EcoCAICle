export const TT_STATES = {
  IDLE: 'idle',
  SCAN: 'scan',
  RESULT: 'result',
};

export const RESULT_RESET_SECONDS = 10;

export function getTTState() {
  if (typeof window === 'undefined') return TT_STATES.IDLE;
  return sessionStorage.getItem('tt:state') || TT_STATES.IDLE;
}

export function setTTState(state) {
  sessionStorage.setItem('tt:state', state);
}
