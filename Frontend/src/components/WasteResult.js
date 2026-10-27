import React from 'react';
import ResultCountdown from '@/components/ResultCountdown';
import PreparationFlow from '@/components/PreparationFlow';
import BinSticker from '@/components/BinSticker';
import { getSafeThaiItemName } from '@/lib/wasteClassification';
import { getAuthoritativeBin } from '@/lib/resultPresentation';

/** Shared result presentation for the in-page kiosk flow and /result route. */
export default function WasteResult({
  result = null,
  wasteType,
  objectNameTh = '',
  itemNameTh = '',
  confidence,
  countdown,
}) {
  // Mapping is already resolved by the backend normalizer. This component
  // deliberately has no category-to-bin fallback logic of its own.
  const bin = getAuthoritativeBin(result);
  const isApiError = result?.status === 'api_error';
  const isMissingMapping = result?.status === 'missing_mapping' ||
    result?.classification_status === 'unavailable';
  const displayObjectName = getSafeThaiItemName(objectNameTh || itemNameTh, wasteType);
  const hasPreparation = Boolean(result?.preparation?.required);

  return (
    <main className={`analysis-screen${hasPreparation ? ' analysis-screen--preparation' : ''}`}>
      <section className="analysis-screen__content" aria-live="polite">
        <header className="analysis-screen__heading">
          <p>ผลการวิเคราะห์</p>
          {!isApiError && (
            <h1>{displayObjectName}</h1>
          )}
        </header>

        {hasPreparation ? (
          <PreparationFlow
            preparation={result?.preparation}
          />
        ) : (
          <section
            className={`analysis-screen__result-card${bin?.fallback ? ' analysis-screen__result-card--fallback' : ''}`}
            aria-label="คำแนะนำการทิ้ง"
          >
            {isApiError ? (
              <div className="analysis-screen__unknown" role="alert">
                <p>ไม่สามารถเชื่อมต่อระบบวิเคราะห์ได้</p>
                <small>กรุณาลองใหม่อีกครั้ง</small>
              </div>
            ) : bin ? (
              <>
                <span className="analysis-screen__destination-label">ทิ้งลงถัง</span>
                <BinSticker binNumber={bin.number} binName={bin.category} />
                <h2 className="analysis-screen__result-title">
                  <strong>{bin.category}</strong>
                </h2>
                {bin.fallback && (
                  <small className="analysis-screen__fallback-note" role="status">
                    ใช้ถังทั่วไปเป็นคำแนะนำสำรอง
                  </small>
                )}
              </>
            ) : isMissingMapping ? (
              <div className="analysis-screen__unknown" role="status">
                <p>ยังไม่มีคำแนะนำการทิ้งสำหรับจุดนี้</p>
                <small>โปรดสอบถามเจ้าหน้าที่</small>
              </div>
            ) : (
              <div className="analysis-screen__unknown" role="status">
                <p>ไม่สามารถระบุประเภทขยะได้</p>
              </div>
            )}
          </section>
        )}
      </section>

      <ResultCountdown seconds={countdown} />
    </main>
  );
}
