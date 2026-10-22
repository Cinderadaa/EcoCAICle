import React from 'react';
import ResultCountdown from '@/components/ResultCountdown';
import PreparationFlow from '@/components/PreparationFlow';
import { getClassificationPresentation } from '@/lib/wasteClassification';
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
  const classification = getClassificationPresentation(wasteType);
  // Mapping is already resolved by the backend normalizer. This component
  // deliberately has no category-to-bin fallback logic of its own.
  const bin = getAuthoritativeBin(result);
  const isApiError = result?.status === 'api_error';
  const isMissingMapping = result?.status === 'missing_mapping' ||
    result?.classification_status === 'unavailable';
  const identifiedObjectName = objectNameTh || itemNameTh;
  const displayObjectName = identifiedObjectName || classification.title;
  const hasPreparation = Boolean(result?.preparation?.required);
  const preparationSuffix = 'มีเศษอาหาร';
  const preparationObjectName = displayObjectName.endsWith(preparationSuffix)
    ? displayObjectName
    : `${displayObjectName}${preparationSuffix}`;

  return (
    <main className={`analysis-screen${hasPreparation ? ' analysis-screen--preparation' : ''}`}>
      <section className="analysis-screen__content" aria-live="polite">
        <header className="analysis-screen__heading">
          <p>ผลการวิเคราะห์</p>
          {!isApiError && identifiedObjectName && (
            <h1>{hasPreparation ? preparationObjectName : displayObjectName}</h1>
          )}
        </header>

        {hasPreparation ? (
          <PreparationFlow
            preparation={result?.preparation}
          />
        ) : (
          <section
            className="analysis-screen__result-card"
            aria-label="คำแนะนำการทิ้ง"
          >
            {isApiError ? (
              <div className="analysis-screen__unknown" role="alert">
                <p>ไม่สามารถเชื่อมต่อระบบวิเคราะห์ได้</p>
                <small>กรุณาลองใหม่อีกครั้ง</small>
              </div>
            ) : bin ? (
              <>
                <span className="analysis-screen__destination-label">กรุณาทิ้งลง</span>
                <div
                  className="analysis-screen__bin-number-wrap"
                  aria-label={`หมายเลขถัง ${bin.number}`}
                >
                  <strong className="analysis-screen__bin-number">{String(bin.number)}</strong>
                </div>
                <h2 className="analysis-screen__result-title">
                  <strong>{bin.category}</strong>
                </h2>
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
