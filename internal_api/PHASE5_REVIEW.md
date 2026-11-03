# Phase 5 Local Review and Evaluation Dashboard

Phase 5 adds a local-only reviewer surface over the Phase 4 files. It does not
modify inference records, expose the storage directory, introduce a database,
change Qwen inference, or activate food-contamination handling.

## Local startup

Use a development-only reviewer token and bind both services to loopback:

```bash
export ECOAICLE_LOCATION_ID=ICT-001
export ECOAICLE_MAPPING_CONFIG_DIR="$PWD/internal_api/config/locations"
export ECOAICLE_STORAGE_ROOT=/tmp/ecoaicle-phase4-data
export ECOAICLE_REVIEWER_TOKEN='replace-with-a-local-only-token'

python -m uvicorn internal_api.app:app --host 127.0.0.1 --port 8001
```

In a second terminal:

```bash
cd Frontend
MODEL_BASE_URL=http://127.0.0.1:8001 \
ECOAICLE_REVIEWER_TOKEN='replace-with-the-same-local-only-token' \
NEXT_PUBLIC_USE_MOCK_CLASSIFICATION=false \
npm run dev -- --hostname 127.0.0.1
```

Open `http://127.0.0.1:3002/review` for the queue and
`http://127.0.0.1:3002/review/errors` for the error explorer. Port 3002 is the
only browser-facing app port. FastAPI's port 8001 is used internally by the
Next.js proxy and should not be opened in the browser. The reviewer token is
held by the Next.js server route and is not sent by the browser.

## Data model and audit policy

The service reads `logs/inference.jsonl` as immutable input and associates rows
by `request_id`. Reviewer actions are appended to:

```text
<ECOAICLE_STORAGE_ROOT>/review/ground_truth_annotations.jsonl
```

Each annotation contains request ID, image ID, reviewer ID, recording/review
timestamps, action, canonical category when applicable, note, and revision.
The original Phase 4 `review/ground_truth.jsonl` seed file is not rewritten.
Final reviews are revisioned. The client must send the displayed revision; a
stale revision returns HTTP 409. A duplicate final category also returns 409.
Skipped actions remain non-final and therefore pending. A missing image cannot
be labeled and can only be marked unreviewable.

Sanitized annotation example:

```json
{
  "request_id": "46f4c68b-ef03-4061-8933-13d24547528a",
  "image_id": "images/2026/10/21/46f4c68b-ef03-4061-8933-13d24547528a.jpg",
  "revision": 2,
  "reviewer_id": "reviewer.name",
  "reviewed_at": "2026-10-21T08:00:00Z",
  "recorded_at": "2026-10-21T08:00:00Z",
  "review_action": "correct",
  "review_status": "reviewed",
  "is_final": true,
  "ground_truth_waste_category": "paper",
  "note": "Canonical label confirmed by reviewer"
}
```

This record is an audit event, not a replacement for the immutable model
inference row. A later correction appends another revision.

## Evaluation semantics

Metrics include total and successfully recorded scans, saved-image failures,
pending/reviewed/unreviewable counts, classification accuracy, bin
recommendation accuracy, per-category precision/recall/F1, confusion matrix,
error distribution, API failure rates, and mean/P50/P95 latency. Metrics are
computed only from final human labels. Expected destinations are resolved from
the active location JSON configuration; model-provided destinations are never
used as ground truth. A classification error and a routing/mapping error are
reported separately, with a combined error when both occur. `api_error` rows
are failure telemetry and are not ordinary classification mistakes.

The dashboard warns when fewer than 30 reviewed samples exist. An empty queue
and empty evaluation set are explicit states, not zero-accuracy claims.

## Pilot evaluation in Scan History

The pilot evaluation uses the existing `/scan-history` page, selected scan
detail, inference records, and the same
`review/ground_truth_annotations.jsonl` append-only annotation file and
cross-process lock used by reviewer actions. It adds no separate page, labeling
service, scan list, or pilot JSONL file. Original inference rows in
`logs/inference.jsonl` remain immutable.

Enable the mode explicitly in both the FastAPI and Next.js server environments:

```text
ECOAICLE_PILOT_EVALUATION_ENABLED=1
```

The existing Scan History enable flag, loopback/allowlist protections, and
reviewer token remain required. Restart both services after changing their
server environments. When enabled, the existing Scan History detail panel can
record valid capture, expected category, optional subtype, expected bin, and an
optional note. Pilot annotation events include `evaluation_scope: "pilot"` and
are revisioned independently from ordinary review labels while sharing the
same protected append path and lock.

The **ผลทดสอบนำร่อง** panel joins the latest pilot annotation for each request
ID with its immutable inference record. Invalid captures are counted separately
and excluded from valid-capture accuracy and completion denominators. API and
timeout errors are counted separately from classification errors and do not
count as completed predictions. Unknown predictions and fallback bins are
scored as returned: they are incorrect when they differ from the expected
category or bin. Subtype accuracy includes only captures with an entered
expected subtype. Confidence uses the active mapping threshold; latency
median/P95 include completed valid-capture predictions only.

Treat a 20–30 scan pilot as a small operational check, not a production accuracy
claim. Review the item images and ground truth before drawing conclusions.

## Access and privacy limitations

- FastAPI reviewer routes require a loopback client and `X-Reviewer-Token`.
- The Next.js proxy requires `ECOAICLE_REVIEWER_TOKEN` and exposes only the
  allowlisted `/api/review/*` paths.
- Images are served one at a time through the protected route with
  `Cache-Control: no-store` and traversal checks. The storage root is never a
  static folder.
- Reviewer IDs and notes are validated but are not an identity provider. A
  local operator can still impersonate another reviewer if the token and file
  access are shared.
- Image/log files need OS permissions, backup, encryption-at-rest, and an
  approved retention policy before non-local deployment. No automatic deletion
  is performed.
- The fixed camera is assumed to target the waste surface. Face blurring is not
  added without evidence that captured images contain identifiable people.
- `has_food_scraps` metadata remains available in inference records. The
  unresolved food-contamination flow and physical destination remain inactive.
