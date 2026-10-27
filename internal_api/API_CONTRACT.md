# EcoAICle `/predict` API Contract

Phases 1–3 integrate the startup-loaded `MappingService` into the existing
FastAPI inference endpoint. The model identifies waste and validated signals;
the backend maps that classification to the location's physical bin master.
The model is never the source of bin identity or display name.

## Request

```http
POST /predict
Content-Type: multipart/form-data
```

Required multipart field:

```text
file: image file
```

The backend uses the static `ECOAICLE_LOCATION_ID` deployment setting. No
client-supplied location is accepted or trusted.

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
  "location_id": "ICT-001",
  "prediction": {
    "wasteType": "paper",
    "itemNameTh": "กระดาษทิชชู่ใช้แล้ว",
    "hasFoodScraps": false,
    "hasLiquid": false,
    "confidence": 0.9
  },
  "mappedBin": {
    "id": "ICT-001-B04",
    "number": 4,
    "nameTh": "กระดาษ",
    "wasteType": "paper",
    "mappingVersion": "ICT-10-bin-v2",
    "fallback": false
  },
  "mappingDecision": {
    "rawCategory": "paper",
    "normalizedWasteType": "paper",
    "binNumber": 4,
    "binName": "กระดาษ",
    "mappingFallback": false,
    "classificationStatus": "confirmed",
    "mappingKeyUsed": "paper"
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
  "mapping_version": "ICT-10-bin-v2",
  "classification_status": "confirmed",
  "mapping_fallback": false,
  "status": "success"
}
```

Unknown, below-threshold, or unmapped classifications use the configured
organization fallback. For ICT-001 this is bin 1, `ขยะทั่วไป (ขยะฝังกลบ)`,
with `mappedBin.fallback` set to `true`.

Phone object names such as `โทรศัพท์มือถือ`, `สมาร์ตโฟน`, `mobile phone`, and
`cell phone` normalize to `ewaste` before bin lookup. A confidently identified
phone therefore maps to the configured e-waste bin even when the model's
category is missing or `unknown`.

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
is required when `has_food_scraps` is true, the category is explicitly enabled
by the location configuration, and the container mapping is confirmed. The UI
uses the short food-preparation label and does not display the backend's
longer system guidance text.

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
      "mapping_version": "ICT-10-bin-v2"
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
  "mapping_version": "ICT-10-bin-v2",
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
  "mapping_version": "ICT-10-bin-v2",
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
only when `ECOAICLE_REVIEWER_TOKEN` is configured, accepts requests from the
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
