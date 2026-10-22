import React from 'react';

/**
 * Render the dedicated two-step food preparation screen.
 * The destination values are still validated from the backend response, but
 * this presentation intentionally does not render the standard result card.
 */
export default function PreparationFlow({
  preparation = null,
}) {
  if (!preparation?.required || !Array.isArray(preparation.steps) || preparation.steps.length !== 2) {
    return null;
  }

  const firstStep = preparation.steps[0];
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

  const firstAction = 'เทเศษอาหาร';

  return (
    <section className="tt-food-flow__card" aria-label="ขั้นตอนการแยกก่อนทิ้ง">
      <div className="tt-food-flow__step">
        <span className="tt-food-flow__number" aria-hidden="true">1</span>
        <strong>{firstAction}</strong>
        <img src={firstStep?.icon || "/เทไอคอน.png"} alt="ไอคอนเทเศษอาหาร" />
      </div>

      <img className="tt-food-flow__arrow" src="/ลูกศร.png" alt="แล้วจึง" />

      <div className="tt-food-flow__step tt-food-flow__step--bin">
        <span className="tt-food-flow__number" aria-hidden="true">2</span>
        <div
          className="analysis-screen__bin-number-wrap"
          aria-label={`หมายเลขถัง ${containerDestination.bin_number}`}
        >
          <strong className="analysis-screen__bin-number">
            {String(containerDestination.bin_number)}
          </strong>
        </div>
        <strong>ทิ้งภาชนะลงถัง</strong>
        <p>{containerDestination.bin_name}</p>
      </div>
    </section>
  );
}
