import React from 'react';

/** Shared result-screen countdown presentation. */
export default function ResultCountdown({ seconds, className = '' }) {
  const numericSeconds = Number(seconds);
  const remainingSeconds = Number.isFinite(numericSeconds)
    ? Math.max(0, Math.floor(numericSeconds))
    : 0;

  return (
    <footer className={`analysis-screen__footer ${className}`.trim()}>
      รีเซ็ตอัตโนมัติใน <strong>{remainingSeconds}</strong> วินาที
    </footer>
  );
}
