export const TT_STATES = {
  IDLE: 'idle',
  SCAN: 'scan',
  RESULT: 'result',
};

/** Internal scan phases used by the kiosk capture state machine. */
export const SCAN_PHASES = Object.freeze({
  SCANNING: 'SCANNING',
  CAPTURING: 'CAPTURING',
  ANALYZING: 'ANALYZING',
  RESULT: 'RESULT',
  ERROR: 'ERROR',
  AUTO_RESET: 'AUTO_RESET',
});

const SCAN_PHASE_NEXT = Object.freeze({
  [SCAN_PHASES.SCANNING]: Object.freeze([SCAN_PHASES.CAPTURING, SCAN_PHASES.AUTO_RESET]),
  [SCAN_PHASES.CAPTURING]: Object.freeze([SCAN_PHASES.ANALYZING, SCAN_PHASES.ERROR]),
  [SCAN_PHASES.ANALYZING]: Object.freeze([SCAN_PHASES.RESULT, SCAN_PHASES.ERROR]),
  [SCAN_PHASES.RESULT]: Object.freeze([SCAN_PHASES.AUTO_RESET]),
  [SCAN_PHASES.ERROR]: Object.freeze([SCAN_PHASES.AUTO_RESET]),
  [SCAN_PHASES.AUTO_RESET]: Object.freeze([SCAN_PHASES.SCANNING]),
});

export function isValidScanPhaseTransition(fromPhase, toPhase) {
  if (fromPhase === toPhase) return true;
  return SCAN_PHASE_NEXT[fromPhase]?.includes(toPhase) === true;
}

export const RESULT_RESET_SECONDS = 10;

export function getTTState() {
  if (typeof window === 'undefined') return TT_STATES.IDLE;
  return sessionStorage.getItem('tt:state') || TT_STATES.IDLE;
}

export function setTTState(state) {
  sessionStorage.setItem('tt:state', state);
}
