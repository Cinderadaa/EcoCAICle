import React from 'react';

/**
 * Render only preparation metadata already approved and resolved by FastAPI.
 * The first destination is intentionally shown without a bin number; the
 * second destination is copied from the authoritative mapped container bin.
 */
export default function PreparationFlow({ preparation = null }) {
  if (!preparation?.required || !Array.isArray(preparation.steps) || preparation.steps.length !== 2) {
    return null;
  }

  const firstStep = preparation.steps[0];
  const secondStep = preparation.steps[1];
  const sharedDestination = preparation.shared_disposal_destination;
  const containerDestination = preparation.container_disposal;

  if (
    !sharedDestination ||
    sharedDestination.type !== 'designated_disposal_point' ||
    sharedDestination.bin_id !== null ||
    sharedDestination.bin_number !== null ||
    !containerDestination ||
    containerDestination.type !== 'mapped_bin' ||
    !Number.isInteger(containerDestination.bin_number) ||
    containerDestination.bin_number <= 0 ||
    typeof containerDestination.bin_name !== 'string' ||
    !containerDestination.bin_name.trim()
  ) {
    return null;
  }

  return (
    <section className="tt-disposal-guide" aria-label="ขั้นตอนการแยกก่อนทิ้ง">
      <div className="tt-disposal-guide__steps">
        <div className="tt-disposal-guide__step">
          <span aria-hidden="true">1</span>
          {firstStep?.icon && <img src={firstStep.icon} alt="" />}
          <strong>{firstStep?.action_th || 'แยกเศษอาหารหรือของเหลวที่เหลือ'}</strong>
          <p>{sharedDestination.label_th}</p>
        </div>

        <img className="tt-disposal-guide__arrow" src="/ลูกศร.png" alt="แล้วจึง" />

        <div className="tt-disposal-guide__step">
          <span aria-hidden="true">2</span>
          {secondStep?.icon && <img src={secondStep.icon} alt="" />}
          <strong>{secondStep?.action_th || 'ทิ้งภาชนะ'}</strong>
          <p>
            ลงถัง {containerDestination.bin_number} · {containerDestination.bin_name}
          </p>
        </div>
      </div>

      <p>{preparation.limitation_th}</p>
    </section>
  );
}
