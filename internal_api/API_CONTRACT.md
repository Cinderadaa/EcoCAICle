# EcoCAICle `/predict` API Contract

Phases 1–3 integrate the startup-loaded `MappingService` into the existing
FastAPI inference endpoint. The model identifies waste and validated signals;
the backend maps that classification to the location's physical bin master.
The model is never the source of bin identity or display name.

Qwen returns the visible object/material, `waste_category`, `material_subtype`,
`resin_identification_code`, confidence, visible condition,
food/liquid signals, and exactly three ranked `top_candidates` when the model
provides a complete response. Resin code values
are the strings `1`–`7` or `unknown`; a missing/invalid value normalizes to
`unknown`. It must not return a physical bin number, name, ID, or disposal
decision. The backend resolves all physical-bin fields from the active
location's ordered rulebook.

## Request

```http
POST /predict
Content-Type: multipart/form-data
```

Required multipart field:

```text
file: image file
```

The backend uses the static `ECOCAICLE_LOCATION_ID` deployment setting. No
client-supplied location is accepted or trusted.

Qwen's classification JSON requires `object_name_th`, `waste_category`,
`material_subtype`, `resin_identification_code`, `visible_condition`,
`confidence`, `top_candidates` (exactly three), `has_food_scraps`, and
`has_liquid`. The resin code is one of the strings `1` through `7` or `unknown`;
missing or invalid values normalize to `unknown`. Material subtypes are
`pet_bottle`, `stretch_plastic`, `rigid_plastic`, `multilayer_packaging`,
`plastic_straw`, `plastic_spoon`, `paper`, `cardboard`, `glass`, `metal`, `e_waste`, `hazardous`, `infectious`,
`general`, and `unknown`. `visible_condition` is one of `clean_dry`,
`food_or_liquid_contaminated`, or `unknown`. Each candidate carries its name,
category, subtype, and confidence. Bin or disposal fields from the model are
ignored. The active prompt version is returned at the response root and in
`prediction.prompt_version`, persisted in inference records, and exposed in Scan
History. The default is `qwen-waste-classification-v5`.

Each candidate contains `object_name_th`, `waste_category`,
`material_subtype`, and confidence from 0 to 1. Candidate 1 is normalized to
match the primary prediction exactly. Candidates 2 and 3 are plausible visible
alternatives ranked by non-increasing confidence. A response with fewer than
three usable candidates keeps its primary prediction, returns available
candidates, and emits a structured `top_candidates_incomplete` diagnostic; it
does not trigger a retry or make the scan an API error. Physical-bin mapping
always uses the primary prediction only.

## Successful response

The existing fields remain present:

- `prediction`
- `valid_prediction`
- `disposal`
- `model`

The response additionally contains a canonical `mappedBin` object. Legacy
`bin_id`, `bin_number`, and `bin_name` fields remain for older clients:

```json
{
  "request_id": "uuid",
  "prompt_version": "qwen-waste-classification-v5",
  "location_id": "ICT-001",
  "prediction": {
    "object_name_th": "กระดาษทิชชู่ใช้แล้ว",
    "waste_category": "paper",
    "material_subtype": "paper",
    "resin_identification_code": "unknown",
    "visible_condition": "clean_dry",
    "prompt_version": "qwen-waste-classification-v5",
    "top_candidates": [
      { "object_name_th": "กระดาษทิชชู่ใช้แล้ว", "waste_category": "paper", "material_subtype": "paper", "confidence": 0.9 },
      { "object_name_th": "กระดาษ", "waste_category": "paper", "material_subtype": "cardboard", "confidence": 0.4 },
      { "object_name_th": "กระดาษทิชชู่ใช้แล้ว", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2 }
    ],
    "wasteType": "paper",
    "itemNameTh": "กระดาษทิชชู่ใช้แล้ว",
    "hasFoodScraps": false,
    "hasLiquid": false,
    "confidence": 0.9,
    "has_food_scraps": false,
    "has_liquid": false
  },
  "mappedBin": {
    "id": "ICT-001-B04",
    "number": 4,
    "nameTh": "กระดาษ",
    "wasteType": "paper",
    "mappingVersion": "ICT-10-bin-v3",
    "fallback": false
  },
  "mappingDecision": {
    "rawCategory": "paper",
    "normalizedWasteType": "paper",
    "binNumber": 4,
    "binName": "กระดาษ",
    "mappingFallback": false,
    "classificationStatus": "confirmed",
    "mappingKeyUsed": "paper",
    "mappingReason": "clean_dry_recyclable_material"
  },
  "classificationStatus": "confirmed",
  "rawCategory": "paper",
  "normalizedWasteType": "paper",
  "binNumber": 4,
  "binName": "กระดาษ",
  "mappingFallback": false,
  "bin_id": "ICT-001-B04",
  "bin_number": 4,
  "bin_name": "กระดาษ",
  "mapping_version": "ICT-10-bin-v3",
  "classification_status": "confirmed",
  "mapping_fallback": false,
  "mapping_reason": "clean_dry_recyclable_material",
  "material_subtype": "paper",
  "resin_identification_code": "unknown",
  "visible_condition": "clean_dry",
  "confidence": 0.9,
  "top_candidates": [
    { "object_name_th": "กระดาษทิชชู่ใช้แล้ว", "waste_category": "paper", "material_subtype": "paper", "confidence": 0.9 },
    { "object_name_th": "กระดาษ", "waste_category": "paper", "material_subtype": "cardboard", "confidence": 0.4 },
    { "object_name_th": "กระดาษทิชชู่ใช้แล้ว", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2 }
  ],
  "review_flags": {
    "glass_pet_review_required": false,
    "glass_pet_candidates_present": false,
    "glass_pet_confidence_gap": null,
    "glass_pet_confidence_close": false,
    "glass_pet_review_reason": null
  },
  "status": "success"
}
```

Unknown, below-threshold, or unmapped classifications use the configured
organization fallback. For ICT-001 this is bin 1, `ขยะทั่วไป (ขยะฝังกลบ)`,
with `mappedBin.fallback` set to `true`. Every successful mapping response and
inference record includes `classification_status`, `mapping_fallback`, and a
non-null `mapping_reason`; API errors use `mapping_not_resolved`.

## ICT-001 ordered physical-bin rulebook

The backend applies the ordered rules in `mapping_rulebook.ordered_rules`:

1. `infectious` → bin 10.
2. `hazardous` → bin 9.
3. `ewaste` → bin 7.
4. Clean, dry recyclables map by `material_subtype`: `pet_bottle` → 2,
   `stretch_plastic` → 3, `paper`/`cardboard` → 4, `glass` → 5, and `metal` → 6.
5. Clean, dry `plastic` items with `material_subtype: "plastic_straw"` or
   `"plastic_spoon"` map to bin 8 with subtype-specific reasons
   `clean_dry_plastic_straw_rdf` and `clean_dry_plastic_spoon_rdf`.
   Contaminated items do not match this rule. Other configured RDF materials
   continue to use their explicit allowlist.
6. Unmatched, unsupported, unknown-condition, and invalidly contaminated items
   use bin 1.

`rigid_plastic` is prohibited from the ICT-001 RDF subtype allowlist and does
not map to bin 8. Food/liquid-contaminated straws and spoons do not match the
clean/dry RDF rule; with no supported item-specific preparation rule they
follow the existing safe fallback.

When a beverage-bottle object name is present with validated resin code `1`,
the backend normalizes its category and subtype to `pet_bottle`; a generic
container name alone is insufficient. A contradictory `pet_bottle` category
with `rigid_plastic` subtype is normalized to `plastic`/`rigid_plastic`. The
existing confidence threshold still applies. The global physical-bin mapping
remains `pet_bottle` → Bin 2.

For a location with `mapping_rulebook`, `/predict` does not use the legacy
`category_mapping` shortcut. That field remains for older review/reference
consumers; the rulebook is authoritative for inference responses.

`plastic` alone never selects bin 3. `rigid_plastic` and
`multilayer_packaging` do not map to bin 3. A recyclable object with visible
food/liquid contamination receives the configured preparation flow first; a
material destination is returned only when that flow is enabled for the
category and the subtype has a valid rule. `visible_condition: "unknown"` is
never treated as clean and dry.

The repository has no ICT-001 bin-8 sticker image or verified accepted-material
list. Therefore the live RDF rule currently has empty
`eligible_material_subtypes` and `eligible_object_names_th` lists and safely
falls back to bin 1. Add RDF items only after confirming the physical sticker.

The infectious low-confidence rule is location-configured and exact-name based.
For ICT-001, configured aliases such as `หน้ากากอนามัย`, `ชุดตรวจ ATK`,
`ชุดตรวจโควิด`, and `ชุดตรวจวินิจฉัย` can normalize the category to
`infectious`; the safety mapping can apply from confidence 0.50 to below 0.70.
No phone or glove category is inferred from an object name. `ถุงมือยางใช้แล้ว`
is included only because it is explicitly present in the ICT-001 allowlist;
`ถุงมือยาง` is not.

ICT-001 keeps the global `classification_policy.confidence_threshold` at
`0.70`. Its optional `classification_policy.infectious_low_confidence_override`
rule uses a `0.50` threshold and an exact normalized Thai object-name allowlist.
When the normalized category is exactly `infectious`, confidence is at least
`0.50` and below `0.70`, and `object_name_th` is on that allowlist, the backend
maps through the first configured infectious rule (ICT-001 bin 10).
The response keeps `classification_status: "low_confidence"` and the legacy
`status: "unknown"`, with `mapping_fallback: false`; it adds
`mapping_reason: "infectious_safety_override"` so clients can distinguish this
policy route from both fallback and ordinary confirmed mappings. Other names,
categories, and confidence values continue through normal threshold handling.

For example, `ชุดตรวจวินิจฉัย` at confidence `0.60` returns these mapping
fields (other response fields remain as documented above):

```json
{
  "bin_id": "ICT-001-B10",
  "bin_number": 10,
  "bin_name": "ขยะติดเชื้อ",
  "classification_status": "low_confidence",
  "mapping_fallback": false,
  "mapping_reason": "infectious_safety_override",
  "status": "unknown",
  "mappingDecision": {
    "normalizedWasteType": "infectious",
    "binNumber": 10,
    "mappingFallback": false,
    "classificationStatus": "low_confidence",
    "mappingKeyUsed": "infectious",
    "mappingReason": "infectious_safety_override"
  }
}
```

ICT-001 rule shape:

```json
{
  "infectious_low_confidence_override": {
    "enabled": true,
    "waste_category": "infectious",
    "confidence_threshold": 0.5,
    "object_name_th_allowlist": [
      "หน้ากากอนามัย", "หน้ากากทางการแพทย์", "หน้ากากใช้แล้ว",
      "หน้ากากใช้ครั้งเดียว", "ชุดตรวจวินิจฉัย", "ชุดตรวจ ATK",
      "ชุดตรวจโควิด", "ชุดตรวจโควิด-19", "ชุดตรวจ ATK โควิด",
      "ชุดตรวจแอนติเจน", "ชุดตรวจเร็ว ATK", "ถุงมือยางใช้แล้ว"
    ],
    "mapping_reason": "infectious_safety_override"
  }
}
```

Object names are matched exactly after Unicode NFC normalization, case-folding,
and whitespace normalization; the backend does not use substring matching for
this override.
The same `mapping_reason` is returned at the response top level and under
`mappingDecision.mappingReason`, persisted in the inference record, and
included in Scan History metadata and its status chips.

When the normalized candidates contain both `glass` and `pet_bottle`, the
response and inference record include `review_flags.glass_pet_review_required`
and the confidence gap. A gap of 0.15 or less is marked
`glass_pet_confidence_close`; candidate evidence is retained with the mapped
result for later review.

`prediction.itemNameTh` is validated as Thai script before it is returned. If
the model returns empty, malformed, or mixed-language text, the backend uses a
Thai label for the validated `wasteType`; the frontend applies the same check
before rendering the title.

`has_food_scraps` is true only for visible food residue or leftover food;
`has_liquid` is true only for visible leftover liquid. Preparation remains
based on those validated signals and uses the same `mappedBin` destination.
Older responses may contain only the snake-case fields; clients should accept
both aliases:

```json
{
  "prediction": {
    "has_food_scraps": false,
    "hasFoodScraps": false,
    "has_liquid": false,
    "hasLiquid": false
  },
  "disposal": {
    "hasFoodScraps": false,
    "has_food_scraps": false
  }
}
```

## Preparation response

For ICT-001, `preparation` is returned for every classification response. It
is required when food scraps/liquid are signaled or `visible_condition` is
`food_or_liquid_contaminated`, the category is explicitly enabled by the
location configuration, and a valid material destination exists. The mapped
container destination is the post-preparation destination. Unknown cleanliness
does not qualify for a recyclable rule.

Confirmed preparation example for a glass container:

```json
{
  "preparation": {
    "required": true,
    "reason": "combined_food_liquid_signal",
    "version": "ICT-preparation-v1",
    "signal": "has_food_scraps",
    "signal_value": true,
    "guidance_mode": "combined_food_liquid_contamination",
    "shared_disposal_destination": {
      "destination_id": "ICT-001-FOOD-LIQUID-SEPARATION",
      "type": "designated_disposal_point",
      "label_th": "จุดแยกเศษอาหาร/ของเหลว",
      "bin_id": null,
      "bin_number": null,
      "bin_name": null
    },
    "container_disposal": {
      "type": "mapped_bin",
      "bin_id": "ICT-001-B05",
      "bin_number": 5,
      "bin_name": "ขวดแก้ว",
      "mapping_version": "ICT-10-bin-v3"
    },
    "steps": [
      {
        "step": 1,
        "action_th": "แยกเศษอาหารหรือเทของเหลวที่เหลือ",
        "instruction_th": "แยกเศษอาหารหรือเทของเหลวที่เหลือที่จุดแยกที่กำหนด",
        "icon": "/เทไอคอน.png",
        "destination": { "type": "designated_disposal_point", "bin_id": null, "bin_number": null }
      },
      {
        "step": 2,
        "action_th": "ทิ้งภาชนะ",
        "instruction_th": "ทิ้งภาชนะลงถังที่ระบบแนะนำด้านบน",
        "icon": "/Tang.png",
        "destination": { "type": "mapped_bin", "bin_id": "ICT-001-B05", "bin_number": 5, "bin_name": "ขวดแก้ว" }
      }
    ],
    "limitation_th": "สัญญาณนี้รวมเศษอาหาร ของเหลว ไขมัน หรือการปนเปื้อน ระบบจึงยังแยกชนิดของสิ่งที่ต้องเททิ้งไม่ได้"
  }
}
```

When preparation is not safe to show, `required` is `false`, `steps` is an
empty array, and no destination is presented. This applies to normal scans,
unknown/low-confidence fallback, missing mappings, API errors, disabled
configuration, and invalid/unavailable destinations. The shared designated
point is never a numbered bin; only the second step carries the resolved
location-specific bin.

`classification_status` values are:

- `confirmed`: category met the configured threshold and had a mapping.
- `unknown`: model category was unknown.
- `low_confidence`: category was recognized but confidence was below the threshold.
- `unavailable`: category was confirmed but no location mapping exists.
- `api_error`: inference or mapping infrastructure failed; this is used only in error responses.

`status` values are:

- `success` for confirmed mappings.
- `unknown` for unknown or low-confidence classifications, including the explicit ICT Bin 1 fallback.
- `missing_mapping` when a confirmed category has no configured destination.
- `api_error` for transport, model, or configuration failures.

## Unknown response

```json
{
  "request_id": "uuid",
  "location_id": "ICT-001",
  "bin_id": "ICT-001-B01",
  "bin_number": 1,
  "bin_name": "ขยะทั่วไป (ขยะฝังกลบ)",
  "mapping_version": "ICT-10-bin-v3",
  "classification_status": "unknown",
  "mapping_fallback": true,
  "status": "unknown"
}
```

The nested legacy `prediction` object remains unchanged and continues to carry
the existing classification and food-contamination fields. Phase 2 does not
emit a food-waste bin number, food-waste `bin_id`, or food disposal destination.
The existing food-contamination flow remains inactive until its physical and
policy dependencies are resolved.

## Missing mapping response

```json
{
  "request_id": "uuid",
  "location_id": "TEST-LOCATION",
  "bin_id": null,
  "bin_number": null,
  "bin_name": null,
  "mapping_version": "TEST-LOCATION-v1",
  "classification_status": "unavailable",
  "mapping_fallback": false,
  "status": "missing_mapping"
}
```

The backend does not guess another bin for a missing mapping.

## API error response

Model/API failures return HTTP 502 with a structured response:

```json
{
  "request_id": "uuid",
  "location_id": "ICT-001",
  "waste_category": null,
  "bin_id": null,
  "bin_number": null,
  "bin_name": null,
  "mapping_version": "ICT-10-bin-v3",
  "classification_status": "api_error",
  "mapping_fallback": false,
  "status": "api_error",
  "detail": {
    "error": "Model API is unreachable",
    "type": "URLError",
    "message": "model offline"
  }
}
```

API errors must not be normalized into unknown waste by clients.

The Next.js proxy forwards valid FastAPI JSON and HTTP status codes unchanged.
If the proxy cannot reach FastAPI, or FastAPI returns a non-JSON body, the
proxy creates the same `status: "api_error"` envelope with a proxy-generated
`request_id`, the configured static location ID, and `mapping_version: null`.
Successful backend responses are not rewritten by the proxy.

## Phase 4 local persistence

For each valid image upload, FastAPI generates the `request_id` before reading
the image. The original validated bytes are stored outside the source tree in
date-partitioned storage, and the same `request_id` is written to the
append-only inference JSONL record and the independent unreviewed ground-truth
seed file. This persistence is an internal side effect; the `/predict` response
contract is unchanged.

Image, inference-log, and ground-truth storage failures are isolated from the
classification result. A saved image is retained for successful, unknown,
low-confidence, missing-mapping, and downstream API-error outcomes. If storage
is unavailable, the safe application diagnostic logger records only the
request ID, operation, and exception type; credentials, headers, and image
contents are never logged. No public route exposes the stored files.

## Phase 5 local reviewer API

The reviewer API is separate from the kiosk `/predict` contract. It is enabled
only when `ECOCAICLE_REVIEWER_TOKEN` is configured, accepts requests from the
loopback interface, and is intended to be reached through the local Next.js
review proxy. The token is sent in the server-side `X-Reviewer-Token` header;
it is never exposed to browser JavaScript.

```http
GET /review/queue?review_status=pending&predicted_category=paper&location_id=ICT-001&date_from=2026-10-01&date_to=2026-10-31&limit=50&offset=0
GET /review/metrics
GET /review/errors?error_type=classification_error
POST /review/annotations/{request_id}
GET /review/images/{image_id}
```

Annotation bodies are validated as follows:

```json
{
  "reviewer_id": "reviewer.name",
  "review_action": "label|unreviewable|skip",
  "category": "paper",
  "note": "optional note",
  "expected_revision": 0
}
```

`cardboard` is normalized to canonical `paper`. `label` creates a final
`confirm` or `correct` annotation; `unreviewable` creates a final
`unreviewable` annotation; `skip` is an auditable non-final action. Final
annotations are append-only revisions. A stale `expected_revision` or a
duplicate final label returns HTTP 409 rather than silently overwriting a
review.

`GET /review/metrics` derives expected bins from the startup-loaded location
configuration, never from the model's recommended bin. Accuracy denominators
contain only final reviewed labels with usable canonical categories. API
failures are counted separately and are excluded from ordinary classification
accuracy. Image access is protected by the same local-only token check and
rejects traversal or files outside the storage `images` directory.
