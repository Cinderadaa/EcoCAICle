# Qwen prompt-first regression experiment

Current prompt version: `qwen-waste-classification-v7` (override with
`ECOCAICLE_PROMPT_VERSION` when deliberately deploying a distinct prompt).
The version is returned by `/predict`, stored in each inference record, and
included in Scan History metadata.

## Audit findings and changes

- PET beverage bottle versus generic rigid plastic: the old prompt already
  prohibited PET bottles from being called rigid plastic, but also suggested
  shape/material cues in a way that could encourage guessing. The revised rules
  make a visible resin 1/PET/PETE mark decisive, prohibit shape-only PET
  inference, and direct unsupported material to `unknown`.
- PET versus glass: the old prompt listed both sets of cues but did not clearly
  require visible glass evidence. The revised rules require body-level glass
  evidence and explicitly reject opacity, bottle shape, contents, reflections,
  caps, and background as proof.
- Paper versus cardboard: cardboard is now explicitly a subtype of `paper`;
  formed boxes/corrugated board use subtype `cardboard`, while flat/thin sheets
  use `paper`.
- Metal versus coated material: metal requires visible metal structure/surface
  evidence. A shiny print, foil-like appearance, or laminate alone is not enough;
  `plastic_coated_paper` requires visible evidence of both layers.
- Item identification versus material evidence: the model can name a visible
  object while returning category/subtype `unknown` when its material is not
  supported. It is instructed to ignore hands, people, background, caps, labels,
  and minor accessories when identifying the primary item.
- Clean disposable plastic straw: a clearly visible thin hollow drinking straw
  with visible plastic body is classified as `plastic`/`plastic_straw`. Cables,
  pens, other product tubes, and background lines are explicitly distinguished;
  uncertain item identity or material uses `unknown`.
- Disposable plastic spoon: a clearly identifiable disposable spoon with a
  visibly plastic body uses `plastic`/`plastic_spoon`; metal/reusable utensils
  and unclear objects are not guessed as plastic spoons. Visible food/liquid
  contamination remains ineligible for the clean/dry RDF route.
- The output schema requires the seven classification fields and keeps existing
  food/liquid observations for compatibility. It contains no bin or disposal
  fields; mapping and preparation remain backend responsibilities.
- The prompt requires exactly three machine-readable candidates. Candidate 1
  matches the primary prediction; alternatives 2 and 3 are evidence-based and
  confidence-ranked. The parser safely normalizes candidate 1 to the primary
  prediction, preserves a usable primary result if fewer than three candidates
  arrive, and logs `top_candidates_incomplete` without retrying the request.
- Face-only or otherwise empty captures use the explicit
  `no_waste_detected`/`invalid_capture` result, have no bin destination, and
  return `ไม่พบขยะในภาพ กรุณาวางขยะในกรอบแล้วสแกนใหม่`. This bypasses unknown
  classification fallback mapping, including Bin 1.
- ICT-001 plastic routing follows the local subtype policy: PET beverage bottles
  use `pet_bottle`; flexible film/bags use `stretch_plastic`; clean rigid plastic
  uses `plastic`/`rigid_plastic` for RDF; clean straws and disposable plastic
  spoons retain their approved RDF subtypes. Contaminated or unknown-condition
  plastic falls back to Bin 1. Resin codes are emitted only for legible marks.

The reported kiosk scan ID `d1e35391-399f-4edc-9130-bbe3c3108938` was not
present in the accessible workspace files, so its saved image could not be
used as a regression image. A face-only/no-waste fixture covers this failure
mode alongside the existing genuine-waste regression set.

## Run the deterministic prompt regression set

The fixed cases come from the existing folder-labeled dataset only. They cover
PET, glass, paper, cardboard, metal, and two examples from source folders whose
labels are explicitly mixed/unsupported (`other` and `trash`). The latter use
expected category `unknown` to check restraint when the source label supplies
no supported material class.

With the normal Qwen endpoint credentials/network available, from the repository
root run:

```bash
python3 internal_api/scripts/run_prompt_regression.py \
  --output /tmp/ecoaicle-prompt-regression-v2.json
```

The JSON output records each raw model response, normalized category/subtype,
confidence, the unchanged configured mapping result, and pass/fail against its
case expectation. Temperature is fixed at zero and the image list/order is
fixed in the script. This is a small prompt regression check, not an accuracy
estimate; its cases do not represent deployment prevalence or independent
held-out evaluation. It sends images to the configured Qwen endpoint and does
not install packages, fetch model weights, create splits, or launch GPU work.

## Decision criteria for fine-tuning

1. Run the prompt regression set after prompt changes and review the per-case
   raw output and failures.
2. Review real kiosk Scan History with images and human-confirmed material
   labels. Group recurring errors by prompt version and visually inspectable
   evidence; do not treat model output or pseudo-labels as ground truth.
3. Consider fine-tuning only if the same visually clear material errors recur
   after the prompt improvement, including in real kiosk review. If errors are
   limited to occluded, low-resolution, or genuinely ambiguous evidence, keep
   the safe unknown/low-confidence behavior and gather better examples first.

The prompt experiment does not change mapping rules or confidence thresholds.
Generic plastic retains its current mapping behavior.
