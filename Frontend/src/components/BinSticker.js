import React from 'react';

/** Render the existing numbered kiosk sticker for a backend-resolved bin. */
export default function BinSticker({ binNumber, binName, className = '' }) {
  if (!Number.isInteger(binNumber) || binNumber < 1 || binNumber > 10) return null;

  const name = typeof binName === 'string' ? binName.trim() : '';

  return (
    <div className={`analysis-screen__bin-sticker-frame ${className}`.trim()}>
      <img
        className="analysis-screen__bin-sticker"
        src={`/New%20UI%20Mapping/${binNumber}.png`}
        alt={`ป้ายหน้าถัง ${binNumber}${name ? ` ${name}` : ''}`}
        onError={(event) => { event.currentTarget.style.display = 'none'; }}
      />
    </div>
  );
}
