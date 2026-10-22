/** Return the remaining whole seconds used by the existing result countdown. */
export function getRemainingResultSeconds(deadline, now = Date.now()) {
  const remainingMilliseconds = Number(deadline) - Number(now);
  if (!Number.isFinite(remainingMilliseconds)) return 0;
  return Math.max(0, Math.ceil(remainingMilliseconds / 1000));
}

export function shouldResetResult(deadline, now = Date.now()) {
  return getRemainingResultSeconds(deadline, now) === 0;
}
