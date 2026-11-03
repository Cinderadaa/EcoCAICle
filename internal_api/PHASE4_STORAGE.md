# Phase 4 Image Storage and Inference Logging

Phase 4 is local-development-only. It stores the original accepted upload and
one append-only JSONL inference record for each generated `request_id`. It does
not add an HTTP image route, database, dashboard, retraining job, or food-waste
destination.

## Local configuration

Start FastAPI from the repository root with:

```bash
export ECOAICLE_LOCATION_ID=ICT-001
export ECOAICLE_MAPPING_CONFIG_DIR="$PWD/internal_api/config/locations"
export ECOAICLE_STORAGE_ROOT=/tmp/ecoaicle-phase4-data

python -m uvicorn internal_api.app:app --host 127.0.0.1 --port 8001
```

For local use, the browser-facing app is Next.js at
`http://127.0.0.1:3002`. FastAPI on port 8001 is an internal service used by
the Next.js API proxy; users do not open the backend port directly.

Supported settings:

| Setting | Default | Behavior |
| --- | --- | --- |
| `ECOAICLE_STORAGE_ROOT` | `/tmp/ecoaicle-phase4-data` | Approved filesystem root; must be outside the source repository. |
| `ECOAICLE_MAX_IMAGE_BYTES` | `10485760` | Maximum accepted upload size; hard limit is `104857600`. |
| `ECOAICLE_RETENTION_DAYS` | unset | Advisory metadata only. No automatic deletion is performed. |
| `ECOCAICLE_PROMPT_VERSION` | `qwen-waste-classification-v5` | Optional prompt identifier override; returned by `/predict` and persisted with inference/Scan History metadata. |

The backend reads the storage configuration at process startup. Configuration
changes require a controlled backend restart; there is no hot reload.

## Filesystem layout and permissions

```text
<ECOAICLE_STORAGE_ROOT>/
  images/YYYY/MM/DD/<request_id>.<detected-extension>
  logs/inference.jsonl
  review/ground_truth.jsonl
```

Directories are created with mode `0700`; image and JSONL files use mode
`0600`. Filenames are generated from the backend UUID and the detected image
type. Client filenames are never used as storage paths. `image_id` and
`image_path` are relative to `ECOAICLE_STORAGE_ROOT` so records are portable
and joinable without exposing an absolute server path.

Only JPEG, PNG, GIF, BMP, TIFF, and WebP signatures are accepted. The raw
validated bytes are stored unchanged. Continuous video and extra camera frames
are not collected.

No public web route serves this directory. Access is restricted to the service
account and approved local operators. Backups, encryption at rest, and the
permanent storage volume remain deployment decisions.

## Inference record

`logs/inference.jsonl` contains one record per valid image upload. It includes
the classification and mapping outcome, image association, actual model
identifier, optional prompt version, and measured latencies. Values that are
not available are `null`; model predictions are never copied into the ground
truth fields.

Example sanitized record:

```json
{
  "request_id": "46f4c68b-ef03-4061-8933-13d24547528a",
  "timestamp": "2026-10-21T07:14:49.870218Z",
  "location_id": "ICT-001",
  "image_id": "images/2026/10/21/46f4c68b-ef03-4061-8933-13d24547528a.jpg",
  "image_path": "images/2026/10/21/46f4c68b-ef03-4061-8933-13d24547528a.jpg",
  "image_save_status": "saved",
  "object_name_th": "แก้วน้ำแก้ว",
  "waste_category": "glass",
  "material_subtype": "glass",
  "resin_identification_code": "unknown",
  "visible_condition": "clean_dry",
  "normalized_waste_category": "glass",
  "confidence": 0.9,
  "has_food_scraps": false,
  "has_liquid": false,
  "top_candidates": [
    { "object_name_th": "แก้วน้ำแก้ว", "waste_category": "glass", "material_subtype": "glass", "confidence": 0.9 },
    { "object_name_th": "แก้ว", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.4 },
    { "object_name_th": "ภาชนะ", "waste_category": "unknown", "material_subtype": "unknown", "confidence": 0.2 }
  ],
  "bin_id": "ICT-001-B05",
  "bin_number": 5,
  "bin_name": "ขวดแก้ว",
  "mapping_version": "ICT-10-bin-v3",
  "classification_status": "confirmed",
  "mapping_fallback": false,
  "mapping_reason": "clean_dry_recyclable_material",
  "review_flags": {
    "glass_pet_review_required": false,
    "glass_pet_candidates_present": false,
    "glass_pet_confidence_gap": null,
    "glass_pet_confidence_close": false,
    "glass_pet_review_reason": null
  },
  "status": "success",
  "model_identifier": "qwen3.8:27b",
  "prompt_version": "qwen-waste-classification-v5",
  "inference_latency_ms": 120.324,
  "total_latency_ms": 137.338,
  "error_type": null,
  "storage_error_type": null
}
```

Inference records also persist `mapping_reason`. For ICT-001, an allowlisted
infectious item accepted at confidence `0.50` to below `0.70` is still recorded
with `classification_status: "low_confidence"`, `mapping_fallback: false`, and
`mapping_reason: "infectious_safety_override"`. Scan History includes the field
in metadata and marks the record with an `infectious safety override` chip.

Prediction startup requires the configured storage root to initialize. Each
valid image is saved before it is sent to the model. If saving fails, the
request is rejected and an `api_error` inference row is attempted in the same
root; the model is not called. If the inference row cannot be appended, the
request returns HTTP 503 instead of reporting a successful classification.
When the model or mapping fails, the saved image is retained and the record
uses `status: "api_error"`; it is never converted to `unknown`.

Inference rows also preserve the normalized `waste_category`, `material_subtype`,
`resin_identification_code` (`"1"` through `"7"` or `"unknown"`),
`visible_condition`, `has_liquid`, exactly three sanitized `top_candidates` on
complete model responses (an incomplete response is preserved with a structured
diagnostic and does not trigger retry), and
`review_flags`. `mapping_reason` is recorded for successful, fallback, and
API-error outcomes (`mapping_not_resolved` for transport/model errors). When glass and PET
bottle candidates both appear, `glass_pet_review_required` is true and the
confidence gap is recorded. A gap of 0.15 or less is marked as close.

`material_subtype` is persisted as the normalized model subtype, including the
explicit `plastic_straw` and `plastic_spoon` values. For ICT-001, only a clean,
dry plastic straw or disposable plastic spoon uses the matching item-specific
RDF reason to Bin 8; generic `rigid_plastic` remains ineligible.

## Ground-truth review preparation

`review/ground_truth.jsonl` contains an independent seed row for each saved
image:

```json
{
  "request_id": "46f4c68b-ef03-4061-8933-13d24547528a",
  "image_id": "images/2026/10/21/46f4c68b-ef03-4061-8933-13d24547528a.jpg",
  "review_status": "unreviewed",
  "ground_truth_object_name_th": null,
  "ground_truth_waste_category": null,
  "ground_truth_has_food_scraps": null,
  "reviewer_id": null,
  "reviewed_at": null,
  "notes": null
}
```

Human review tooling and any update workflow are intentionally deferred. The
model output must not be used as ground truth, and no automatic retraining is
performed.

## Operational decisions before deployment

The organization must approve:

1. A persistent storage volume outside the application source tree.
2. Service-account and reviewer access controls, including audit requirements.
3. Encryption and backup policy for images and JSONL records.
4. A retention period. `ECOAICLE_RETENTION_DAYS` is currently advisory and
   does not delete existing data.
5. Whether the fixed camera view creates any meaningful person/hand/background
   privacy risk. No face detection or blurring is currently added because the
   stated camera is aimed at the waste-placement surface.
6. A review import/export or annotation process for the ground-truth file.

Before any non-local deployment, verify that the runtime user can write the
approved root and that the image directory is not mounted or routed through a
public web server.
