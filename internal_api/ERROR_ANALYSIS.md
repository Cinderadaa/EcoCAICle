# Error Analysis: Qwen inference history

**Scope:** offline, read-only review of existing Scan History/inference logs, reviewer annotations, saved evaluation outputs, and the existing visual-screen manifest. No model/API calls were made. No production config, prompt, mapping, or threshold was changed.

## Observed evidence and interpretation

This report treats only fields recorded in the source files as observed evidence. A blank field means “not recorded,” not that the event did not occur. Hypotheses are marked explicitly and are not included as measured causes.

## Sources and sample coverage

| Source | Records | Use / limits |
|---|---:|---|
| `/var/lib/ecoaicle-data/logs/inference.jsonl` | 154 | Historical inference records; includes all tags |
| `/tmp/ecoaicle-phase4-data/logs/inference.jsonl` | 347 | Historical inference records; includes all tags |
| Exact requested model tags, combined | 497 | qwen3-vl:4b 89; qwen3.8:27b 371; qwen2.5vl:3b 37; 497 unique request IDs |
| Reviewer annotations, two `ground_truth_annotations.jsonl` files | 339 raw rows; 336 unique request IDs after latest-revision selection | 216 request IDs have a final, reviewed, confirmed/correct non-null category; 185/29/1 join to the three model histories respectively |
| `/var/lib/ecoaicle-data/datasets/kiosk_v1/human_review_decisions.jsonl` | 36 | One valid human bin label; it does not join to these three model histories |
| `internal_api/evaluations/qwen3_8_27b_results.csv` | 8 | Existing offline Qwen3.8 golden run with directly reviewed expected category/subtype/condition/bin; evaluated separately from historical logs |
| `internal_api/tests/golden/results.json` | 8 × 2 provider routes | Existing Qwen3-VL:4b run; provider-specific results, not a paired model comparison |
| `derived/kiosk_local_training_prep_2026-11-05/manifest.jsonl` | 513 screening rows | Contact-sheet visual screen; its candidate labels are not ground truth and flags are not linked to model-specific failures |

Other model strings `qwen2.5:3b` (1), `qwen3.8:27` (1), and `qwen3.8:27ิb` (2) are excluded; they were not silently normalized to a requested tag. The three exact-tag cohorts share **zero request IDs**, so cross-model differences are not a paired comparison.

## Serving and response metrics

Historical logs do not store raw response bodies or HTTP status codes for `HTTPError`. The “response success” metric below means the application stored a classification status (`success`, `unknown`, or `no_waste_detected`). Raw valid-JSON rate cannot be reconstructed from these logs. HTTPError is not counted as 5xx without a recorded status.

| Model | Scans | App response success | Valid JSON in history | Timeout | HTTPError, status unknown | Explicit HTTP 5xx | Median latency | P95 latency |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| `qwen3-vl:4b` | 89 | 35/89 (39.3%) | not stored | 44/89 (49.4%) | 0/89 (0.0%) | 0 with explicit evidence | 40.04s | 300.11s |
| `qwen3.8:27b` | 371 | 353/371 (95.2%) | not stored | 14/371 (3.8%) | 2/371 (0.5%) | 0 with explicit evidence | 9.01s | 36.40s |
| `qwen2.5vl:3b` | 37 | 20/37 (54.0%) | not stored | 3/37 (8.1%) | 14/37 (37.8%) | 0 with explicit evidence | 2.51s | 40.05s |

Historical inference logs have no raw `finish_reason`, content/reasoning lengths, JSON parse diagnostics, or stable top-candidate contract. Thus empty content, reasoning-only response, `finish_reason=length`, invalid JSON, and exact 5xx counts are **not inferable** from those logs. Existing saved evaluations do preserve some of these signals: in the Qwen3.8 golden run, the one empty response had `finish_reason=length`, content length 0, and reasoning length 4,581 (reasoning-only); the run also records one invalid-JSON/empty-content row. That is a single observed case, not a rate estimate for other runs.

| Saved evaluation | Response success | Valid JSON | Timeout / 5xx | Empty content | Category accuracy | Bin accuracy | Median / P95 latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen3.8:27b`, offline golden (n=8) | 8/8 | 7/8 (87.5%) | 0 / 0 | 1/8 | 5/7 (71.4%) | 6/7 (85.7%) | 17.844s / 27.728s |
| `qwen3-vl:4b`, saved golden route `litellm` (n=8) | 1/8 | 0/8 | 7 timeouts / 0 explicit 5xx | 1/8 | 0/8 (no valid parsed output) | 0/8 (no valid parsed output) | 60.045s median / 60.069s P95 |
| `qwen3-vl:4b`, saved golden route `ollama_native` (n=8) | 0/8 | 0/8 | 8 timeouts / 0 explicit 5xx | 0/8 | 0/8 (no valid parsed output) | 0/8 (no valid parsed output) | 60.062s median / 60.063s P95 |

No comparable saved golden evaluation was found for `qwen2.5vl:3b`. The Qwen3-VL route runs had different provider settings and should not be interpreted as an intrinsic model-only comparison.

## Human-verified classification and bin metrics

Category metrics use only latest-per-request reviewer annotations with `review_status=reviewed`, `is_final=true`, action `confirm` or `correct`, and non-null category. Accuracy/macro F1 use rows with a returned category; operational failures with no prediction remain included in serving metrics and are excluded from this classification denominator.

| Model | Joined reviewer scans | Scored category denominator | Correct | Category accuracy | Macro F1 | Human bin denominator | Bin accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen3-vl:4b` | 29 | 26 | 21 | 80.8% | 0.5471 | 0 | unavailable (0 labeled scans joined) |
| `qwen3.8:27b` | 185 | 183 | 159 | 86.9% | 0.8075 | 0 | unavailable (0 labeled scans joined) |
| `qwen2.5vl:3b` | 1 | 1 | 1 | 100.0% | 1.0000 | 0 | unavailable (0 labeled scans joined) |

The single explicit bin label is a valid kiosk review decision but has no matching request ID in these model logs. The Qwen3.8 golden run has direct human-reviewed bins and is reported separately above (6/7 valid mapped outputs). Among its five category-correct valid predictions, all five bins matched the reviewed expected bin; the one bin error co-occurred with an incorrect/unknown classification, so there is no observed pure mapping-only bin error in this tiny run. It is not combined with production-history metrics. The expected bins reflect the evaluation’s direct visual review and recorded ICT-001 rule mapping; this does not independently validate the policy. Subtype and condition accuracy cannot be measured on historical model scans because reviewer labels for those fields are absent.

## Error groups by model

Counts below are observed evidence from each model’s historical log unless marked otherwise. Percentages use that model’s total scan count as denominator. Categories without per-scan evidence are shown as unavailable rather than inferred.

| Error group / recorded signal | qwen3-vl:4b (n=89) | qwen3.8:27b (n=371) | qwen2.5vl:3b (n=37) |
|---|---:|---:|---:|
| Operational: Timeout (`error_type=timeout`) | 44 (49.4%) | 14 (3.8%) | 3 (8.1%) |
| Operational: HTTPError, status not recorded | 0 (0.0%) | 2 (0.5%) | 14 (37.8%) |
| Recorded `ModelResponseError` (specific cause absent) | 10 (11.2%) | 2 (0.5%) | 0 (0.0%) |
| Operational: Explicit connection error | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Operational: Explicit HTTP 5xx | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Latency above that model’s historical P95 | 4 (4.5%) | 18 (4.9%) | 1 (2.7%) |
| classification/returned unknown on reviewer-labeled non-unknown scan (reviewed subset) | 0 (0.0%) | 21 (5.7%) | 0 (0.0%) |
| classification/other category mismatch (reviewed subset) | 5 (5.6%) | 3 (0.8%) | 0 (0.0%) |
| classification/high confidence (≥0.90) and wrong (reviewed subset) | 3 (3.4%) | 0 (0.0%) | 0 (0.0%) |
| Mapping fallback, any recorded `mapping_fallback=true` | 12 (13.5%) | 159 (42.9%) | 10 (27.0%) |
| Fallback reason exactly `low_confidence_fallback` | 0 (0.0%) | 15 (4.0%) | 1 (2.7%) |
| Fallback reason exactly unknown-category | 0 (0.0%) | 10 (2.7%) | 7 (18.9%) |
| Fallback reason explicitly condition unknown/contaminated | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| Object-name override logged true / false | 0 / 4 | 4 / 25 | 3 / 8 |
| Category matches but subtype is wrong | unavailable (no joined subtype labels) | unavailable (no joined subtype labels) | unavailable (no joined subtype labels) |
| Condition wrong/unknown against reviewer condition | unavailable (no joined condition labels) | unavailable (no joined condition labels) | unavailable (no joined condition labels) |
| Class outside supported taxonomy | not measurable from joined taxonomy labels | not measurable from joined taxonomy labels | not measurable from joined taxonomy labels |

A class-level “visible object but model answered unknown” event cannot be separated from no-object/ambiguous captures in the historical logs; the reviewed-label proxy is the “returned unknown on reviewer-labeled non-unknown scan” row above.

Raw `top_candidates` arrays exist for only 24/89, 74/371, and 15/37 historical scans; among those rows, fewer than three candidates were recorded in 12/24, 14/74, and 0/15 respectively. Missing arrays are not counted as incomplete candidates. No historical per-scan record explicitly identifies reasoning-only or invalid JSON.

| Capture-quality/error subtype | Observed evidence |
|---|---|
| No target object | 79 visual-screen rows have exclusion `no_presented_target_object`; screen manifest n=513, not joined to model errors |
| Object too small / outside usable frame | 61 visual-screen rows have exclusion text matching too-small/outside-usable-frame; screen manifest n=513, not joined to model errors |
| Multiple objects / screen photo | 23 visual-screen rows have exclusion `photo_of_screen_or_multiple_objects`; screen manifest n=513, not joined to model errors |
| Blur | No structured blur count was found; screen manifest n=513, not joined to model errors |
| Hand occlusion | No structured hand-occlusion count was found; screen manifest n=513, not joined to model errors |
| Lighting / shadow / reflection | No structured per-scan flag was found; screen manifest n=513, not joined to model errors |
| Angle / distracting background | No structured per-scan flag was found; screen manifest n=513, not joined to model errors |
| Image dimensions / ratio / file bytes | Computed from image headers/files for historical inference rows; dimensions exist for all 497 scans; screen manifest n=513, not joined to model errors |

The screening manifest reports 127 valid, 205 ambiguous, and 181 invalid capture screens. Its notes are a pre-existing contact-sheet audit that ignored Qwen fields; they are not per-model error labels. No model-specific causal relationship to blur, occlusion, angle, lighting, or object size can be calculated.

### Image metadata versus serving failure

Image files were found for all 497 historical scans. JPEG/PNG dimensions were read from file headers using the standard library; longest-edge and byte-size bands are descriptive strata, not thresholds from production. The following are association rates in non-paired cohorts, not evidence that image size caused an error.

| Model | Metadata stratum | n | API errors | Failure rate |
|---|---|---:|---:|---:|
| `qwen3-vl:4b` | longest_edge: 641–1280px | 85 | 54 | 63.5% |
| `qwen3-vl:4b` | longest_edge: >1280px | 4 | 0 | 0.0% |
| `qwen3-vl:4b` | aspect_ratio: landscape >1.33 | 82 | 54 | 65.8% |
| `qwen3-vl:4b` | aspect_ratio: near-square 0.75–1.33 | 4 | 0 | 0.0% |
| `qwen3-vl:4b` | aspect_ratio: portrait <0.75 | 3 | 0 | 0.0% |
| `qwen3-vl:4b` | file_size: 100KB–500KB | 77 | 50 | 64.9% |
| `qwen3-vl:4b` | file_size: >500KB | 4 | 0 | 0.0% |
| `qwen3-vl:4b` | file_size: ≤100KB | 8 | 4 | 50.0% |
| `qwen3.8:27b` | longest_edge: 641–1280px | 361 | 16 | 4.4% |
| `qwen3.8:27b` | longest_edge: >1280px | 9 | 2 | 22.2% |
| `qwen3.8:27b` | longest_edge: ≤640px | 1 | 0 | 0.0% |
| `qwen3.8:27b` | aspect_ratio: landscape >1.33 | 365 | 18 | 4.9% |
| `qwen3.8:27b` | aspect_ratio: near-square 0.75–1.33 | 1 | 0 | 0.0% |
| `qwen3.8:27b` | aspect_ratio: portrait <0.75 | 5 | 0 | 0.0% |
| `qwen3.8:27b` | file_size: 100KB–500KB | 278 | 15 | 5.4% |
| `qwen3.8:27b` | file_size: >500KB | 9 | 2 | 22.2% |
| `qwen3.8:27b` | file_size: ≤100KB | 84 | 1 | 1.2% |
| `qwen2.5vl:3b` | longest_edge: 641–1280px | 33 | 16 | 48.5% |
| `qwen2.5vl:3b` | longest_edge: >1280px | 4 | 1 | 25.0% |
| `qwen2.5vl:3b` | aspect_ratio: landscape >1.33 | 34 | 17 | 50.0% |
| `qwen2.5vl:3b` | aspect_ratio: near-square 0.75–1.33 | 2 | 0 | 0.0% |
| `qwen2.5vl:3b` | aspect_ratio: portrait <0.75 | 1 | 0 | 0.0% |
| `qwen2.5vl:3b` | file_size: 100KB–500KB | 31 | 15 | 48.4% |
| `qwen2.5vl:3b` | file_size: >500KB | 4 | 1 | 25.0% |
| `qwen2.5vl:3b` | file_size: ≤100KB | 2 | 1 | 50.0% |

Resolution was available for all scans. Blur and occlusion flags were not available as structured, per-scan annotations. File size/shape bands have small and highly imbalanced cells (especially qwen2.5vl:3b); no causal conclusion is supported.

## Confidence bucket versus category correctness

Only human-verified scans with a returned category and confidence are included. Denominator is shown per bucket; confidence is the model’s recorded score, not a calibrated probability.

| Model | Confidence bucket | n | Correct | Accuracy |
|---|---|---:|---:|---:|
| `qwen3-vl:4b` | <0.50 | 0 | 0 | n/a |
| `qwen3-vl:4b` | 0.50–<0.70 | 0 | 0 | n/a |
| `qwen3-vl:4b` | 0.70–<0.90 | 5 | 3 | 60.0% |
| `qwen3-vl:4b` | ≥0.90 | 21 | 18 | 85.7% |
| `qwen3.8:27b` | <0.50 | 25 | 4 | 16.0% |
| `qwen3.8:27b` | 0.50–<0.70 | 25 | 24 | 96.0% |
| `qwen3.8:27b` | 0.70–<0.90 | 90 | 88 | 97.8% |
| `qwen3.8:27b` | ≥0.90 | 43 | 43 | 100.0% |
| `qwen2.5vl:3b` | <0.50 | 0 | 0 | n/a |
| `qwen2.5vl:3b` | 0.50–<0.70 | 0 | 0 | n/a |
| `qwen2.5vl:3b` | 0.70–<0.90 | 1 | 1 | 100.0% |
| `qwen2.5vl:3b` | ≥0.90 | 0 | 0 | n/a |

Observed pattern: qwen3.8:27b had 4/25 correct below 0.50 and 155/158 correct at confidence ≥0.50 across the scored reviewer subset; qwen3-vl:4b had 3/5 correct in 0.70–<0.90 and 18/21 at ≥0.90; qwen2.5vl:3b has only one scored point (1/1 at 0.70–<0.90). Small samples and selective human review limit comparison; these are not threshold recommendations.

## Ten evidence cases per model

Tables prioritize reviewer-confirmed category disagreements, then explicit serving errors, then recorded fallback/unknown outputs. “No verified label” is intentional. Paths are relative to the source root shown; the machine-readable per-scan file contains resolved local paths.

### `qwen3-vl:4b`

| request_id | image path | expected | predicted | bin | confidence | error category | observed evidence |
|---|---|---|---|---:|---:|---|---|
| 432af079-dd9a-467a-a707-b4e026da5348 | images/2026/11/03/432af079-dd9a-467a-a707-b4e026da5348.jpg | fuel | plastic | 1 | 0.8 | reviewed category mismatch | reviewed category comparison; mismatch |
| 52ec2904-8f61-41f5-9a16-973acb472cc9 | images/2026/11/03/52ec2904-8f61-41f5-9a16-973acb472cc9.jpg | fuel | plastic | 1 | 0.9 | reviewed category mismatch | reviewed category comparison; mismatch |
| 2f032093-f1e3-47c2-8158-ca5d136116e0 | images/2026/11/03/2f032093-f1e3-47c2-8158-ca5d136116e0.jpg | metal | plastic | 1 | 0.8 | reviewed category mismatch | reviewed category comparison; mismatch |
| eab5edb9-67b9-4079-b154-ea790aa6a2ad | images/2026/11/03/eab5edb9-67b9-4079-b154-ea790aa6a2ad.jpg | plastic | no response | — | — | serving | status=api_error; error_type=timeout; reviewer label attached |
| 4cb231a7-d19f-4e86-823c-a6f3453d3db9 | images/2026/11/03/4cb231a7-d19f-4e86-823c-a6f3453d3db9.jpg | metal | plastic | 8 | 0.9 | reviewed category mismatch | reviewed category comparison; mismatch |
| 72357a7c-5afe-45df-b21d-b40ccaef0d89 | images/2026/11/03/72357a7c-5afe-45df-b21d-b40ccaef0d89.jpg | general | no response | — | — | serving | status=api_error; error_type=timeout; reviewer label attached |
| 7840cb87-961f-490b-a14f-81502cd858ec | images/2026/11/03/7840cb87-961f-490b-a14f-81502cd858ec.jpg | general | plastic | 8 | 0.9 | reviewed category mismatch | reviewed category comparison; mismatch |
| 6eb7c362-8f02-4e94-89a2-e9864d11bb04 | images/2026/11/03/6eb7c362-8f02-4e94-89a2-e9864d11bb04.jpg | general | no response | — | — | serving | status=api_error; error_type=timeout; reviewer label attached |
| e9bce56d-e3e3-4bb0-94b8-0e5126c29165 | images/2026/10/30/e9bce56d-e3e3-4bb0-94b8-0e5126c29165.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=timeout |
| 90de74f0-20be-40c9-9b11-8ccf9e147d23 | images/2026/10/30/90de74f0-20be-40c9-9b11-8ccf9e147d23.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=timeout |

### `qwen3.8:27b`

| request_id | image path | expected | predicted | bin | confidence | error category | observed evidence |
|---|---|---|---|---:|---:|---|---|
| ca34c8fd-8829-430c-9ca6-da7823ba5d25 | images/2026/10/29/ca34c8fd-8829-430c-9ca6-da7823ba5d25.jpg | hazardous | plastic | 3 | 0.7 | reviewed category mismatch | reviewed category comparison; mismatch |
| 3cf826a8-fb4f-465f-904a-c3d30237cbbd | images/2026/10/29/3cf826a8-fb4f-465f-904a-c3d30237cbbd.jpg | plastic | unknown | 1 | 0.3 | reviewed category mismatch | reviewed category comparison; mismatch |
| 6680211c-0292-4d93-971d-bd0e10257433 | images/2026/10/30/6680211c-0292-4d93-971d-bd0e10257433.jpg | plastic | no response | — | — | serving | status=api_error; error_type=timeout; reviewer label attached |
| 7cfec266-420a-4f95-a656-6898b61a45fd | images/2026/10/30/7cfec266-420a-4f95-a656-6898b61a45fd.jpg | pet_bottle | plastic | 1 | 0.5 | reviewed category mismatch | reviewed category comparison; mismatch |
| 691e4b97-0847-4c32-bf13-1e15bde48e18 | images/2026/10/21/691e4b97-0847-4c32-bf13-1e15bde48e18.jpg | glass | pet_bottle | 2 | 0.7 | reviewed category mismatch | reviewed category comparison; mismatch |
| 10ccb3f1-74da-430b-8381-7563716fd147 | images/2026/10/22/10ccb3f1-74da-430b-8381-7563716fd147.jpg | plastic | unknown | 1 | 0.3 | reviewed category mismatch | reviewed category comparison; mismatch |
| ee93ba6d-1617-4aea-a7cf-c76b3d9f7826 | images/2026/10/22/ee93ba6d-1617-4aea-a7cf-c76b3d9f7826.jpg | plastic | no response | — | — | serving | status=api_error; error_type=HTTPError; reviewer label attached |
| 3b1fcc0f-ecc4-41cf-be05-6f5c4d739c7a | images/2026/10/22/3b1fcc0f-ecc4-41cf-be05-6f5c4d739c7a.jpg | paper | unknown | 1 | 0.1 | reviewed category mismatch | reviewed category comparison; mismatch |
| 9b908d1e-56d6-4ee4-94f1-45e3174c704a | images/2026/10/22/9b908d1e-56d6-4ee4-94f1-45e3174c704a.jpg | plastic | unknown | 1 | 0.4 | reviewed category mismatch | reviewed category comparison; mismatch |
| 0335ff0f-544a-4e50-a20a-d2464ceb17ea | images/2026/10/27/0335ff0f-544a-4e50-a20a-d2464ceb17ea.jpg | plastic | unknown | 1 | 0.1 | reviewed category mismatch | reviewed category comparison; mismatch |

### `qwen2.5vl:3b`

| request_id | image path | expected | predicted | bin | confidence | error category | observed evidence |
|---|---|---|---|---:|---:|---|---|
| 994f4ed2-29c5-42eb-b2bb-b877933dfd69 | images/2026/10/22/994f4ed2-29c5-42eb-b2bb-b877933dfd69.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=HTTPError |
| 74d0640a-a39b-4cd4-8974-5bda4f882fcb | images/2026/10/22/74d0640a-a39b-4cd4-8974-5bda4f882fcb.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=HTTPError |
| 09579079-1b53-4965-97fa-e4d7a9ec1a97 | images/2026/10/22/09579079-1b53-4965-97fa-e4d7a9ec1a97.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=HTTPError |
| 53833950-8fae-470b-b2df-ed0903998628 | images/2026/10/22/53833950-8fae-470b-b2df-ed0903998628.jpg | no verified label | no response | — | — | serving | status=api_error; error_type=HTTPError |
| e6393be8-5b0d-425d-91f1-b630169053e9 | images/2026/10/22/e6393be8-5b0d-425d-91f1-b630169053e9.jpg | glass | glass | 5 | 0.8 | no verified error | reviewed category comparison; correct |
| 949e0c04-2886-46a7-9904-d3858028e1af | images/2026/11/05/949e0c04-2886-46a7-9904-d3858028e1af.jpg | no verified label | unknown | 1 | 0.35 | fallback/unknown | mapping_fallback=true; reason=unknown_category_fallback |
| 657de47c-1b44-444f-8973-02d5a6c91e89 | images/2026/11/05/657de47c-1b44-444f-8973-02d5a6c91e89.jpg | no verified label | unknown | 1 | 0.35 | fallback/unknown | mapping_fallback=true; reason=unknown_category_fallback |
| d203ef27-ea5b-479d-9744-4d8808695bb6 | images/2026/11/05/d203ef27-ea5b-479d-9744-4d8808695bb6.jpg | no verified label | unknown | 1 | 0.35 | fallback/unknown | mapping_fallback=true; reason=unknown_category_fallback |
| 10aba8b9-71db-4cc7-bc01-7554b2cbe070 | images/2026/11/05/10aba8b9-71db-4cc7-bc01-7554b2cbe070.jpg | no verified label | unknown | 1 | 0.35 | fallback/unknown | mapping_fallback=true; reason=unknown_category_fallback |
| 7ab937db-d427-4977-a7a5-cbb5e1b57fa7 | images/2026/11/05/7ab937db-d427-4977-a7a5-cbb5e1b57fa7.jpg | no verified label | unknown | 1 | 0.35 | fallback/unknown | mapping_fallback=true; reason=unknown_category_fallback |

## Network evidence, limits, and hypotheses

**Network attribution rule:** “network/internet” is only supported for scans with explicit connection/connect failure, timeout, or recorded HTTP 5xx. Historical logs show explicit timeout counts of 44/89, 14/371, and 3/37; zero explicit connect errors; and zero records with an explicit 5xx status. There are two Qwen3.8 and fourteen Qwen2.5 `HTTPError` records whose status code was not retained, so they cannot be labeled 5xx. The timeout evidence indicates a serving/request-timeout event, but does not by itself prove the public internet was the cause. Classification disagreements are not attributed to network.

### Limitations separated by layer

- **Model evidence:** reviewer-verified category disagreements occur in the scored subsets; Qwen3.8’s separate 8-image offline golden has 5/7 category-correct valid outputs and 6/7 bin-correct outputs. Do not compare these small, unpaired cohorts as a controlled model ranking.
- **System evidence:** recorded timeout and HTTPError events, missing raw response diagnostics, missing HTTP status, variable logging schemas, and provider-specific Qwen3-VL golden failures are serving/observability limits. Root causes beyond the stored error type are unconfirmed.
- **Dataset evidence:** zero request overlap among model cohorts; only 216 unique reviewed categories against 497 historical scans; only one explicit bin review and no joined bin labels; historical reviewed subtype/condition labels are absent; quality-screen flags are not joined to model runs. qwen2.5vl:3b has only one reviewed category example.
- **Hypothesis (not established):** a higher failure rate in certain image-size bands may reflect workload or capture cohort differences, but current logs cannot distinguish image processing effects from model/provider/time/config differences. No blur/occlusion cause is asserted.

## Recommendations by impact

1. **Fixable without training:** retain per-request raw diagnostic metadata safely (HTTP status, finish reason, response/content/reasoning lengths, candidate count, and separate fallback reason); add image-quality annotations tied to request IDs; make the existing taxonomy/unknown and mapping decisions auditable. These are observability/data handling recommendations, not changes made here.
2. **Serving / LLM team:** investigate the explicitly timed-out scans and status-unknown HTTPError records using their request IDs and server-side logs; preserve response status and finish metadata so empty/reasoning-only/invalid-JSON cases can be separated. Historical evidence does not establish a common root cause.
3. **Collect more images or labels:** prioritize paired model runs on the same human-labeled images; label category, subtype, condition, expected bin, capture validity, blur/occlusion, object size, lighting, and multi-object status. Expand qwen2.5vl:3b reviewed examples and bin-level review labels.
4. **Fine-tune later:** consider fine-tuning only after paired, reviewed examples show repeatable category/subtype errors with adequate class coverage; keep serving/JSON and mapping-policy failures out of training labels.

## Reproducibility artifacts

- Machine-readable summary: [`derived/error_analysis_2026-11-06/summary.json`](../derived/error_analysis_2026-11-06/summary.json)
- Per-scan rows: [`derived/error_analysis_2026-11-06/per_scan.csv`](../derived/error_analysis_2026-11-06/per_scan.csv)
- Derived output is ignored by the added scoped `.gitignore` entry.
