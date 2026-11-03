/** Prefer the location-specific instruction, then its short action label. */
export function getPreparationStepText(step, fallbackText) {
  for (const value of [step?.instruction_th, step?.action_th]) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return fallbackText;
}
