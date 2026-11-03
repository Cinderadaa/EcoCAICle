# EcoCAICle Phase 1–3 Deployment Runbook

This runbook is prepared for a controlled deployment. It does not restart,
deploy, or change any running service.

## Verified local configuration

The backend image uses `/app` as its working directory and copies `config`
into `/app/config`. Therefore the runtime mapping file is:

```text
/app/config/locations/ICT-001.json
```

Required backend environment:

```text
ECOCAICLE_LOCATION_ID=ICT-001
ECOCAICLE_MAPPING_CONFIG_DIR=/app/config/locations
```

`ECOCAICLE_MAPPING_CONFIG_DIR` may be omitted because the backend default is
the same directory, but setting it explicitly is recommended for deployment
visibility. Startup validation rejects missing or invalid configuration before
the service becomes ready. Mapping changes require a controlled backend
restart; there is no hot reload.

The frontend runtime should also receive `ECOCAICLE_LOCATION_ID=ICT-001` so
proxy-generated `api_error` envelopes use the same static location metadata.
Successful responses still use the backend-provided location ID.

## Port configuration

Use **port 3002 as the single browser-facing app port** for the kiosk,
Scan History, and reviewer pages. Browser requests to `/api/...` stay on this
same origin and Next.js proxies them to FastAPI.

FastAPI listens on port 8001 for internal proxy traffic. Do not publish that
backend port as a user-facing entry point. If frontend and backend run in
separate containers, connect them on a private Docker network and use the
backend service name below:

```text
MODEL_BASE_URL=http://<internal-api-service>:8001
```

For local processes on the same machine, use:

```text
MODEL_BASE_URL=http://127.0.0.1:8001
```

The backend address is consumed only by server-side Next.js proxy routes; users
open `http://localhost:3002`.

## Staged deployment procedure

1. Obtain administrator approval for the backend image, runtime environment
   variables, frontend runtime environment change, and controlled restarts.
2. Run the local validation commands:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s internal_api/tests -p 'test_*.py' -v
   cd Frontend && npm test && npm run build
   ```

3. Build the backend image and verify that
   `/app/config/locations/ICT-001.json` is present inside the image.
4. Start the backend in the staging/change target with:
   `ECOCAICLE_LOCATION_ID=ICT-001` and the mapping directory above.
5. From the frontend container, verify:

   - `GET http://<backend>/health` returns HTTP 200 and `status: ok`.
   - `GET http://<backend>/openapi.json` includes `/predict`.
   - `/predict` exposes the Phase 2 fields `request_id`, `location_id`,
     `bin_id`, `bin_number`, `bin_name`, `mapping_version`,
     `classification_status`, `mapping_fallback`, and `status`.

6. Set the frontend runtime `MODEL_BASE_URL` to the private verified backend
   address and restart the frontend. Publish only the frontend's browser port,
   3002.
7. Run end-to-end smoke tests with a controlled test image/model response:

   - one confirmed prediction for each ICT bin 1–10;
   - unknown classification and low confidence, both resolving to backend
     fallback Bin 1;
   - a test-only location with a missing mapping, returning
     `missing_mapping` and no bin;
   - a model/API outage, returning `api_error` and never `unknown`;
   - a location-specific many-to-one mapping;
   - object name and backend `bin_name` rendering on `/`;
   - sessionStorage restore on `/result`;
   - automatic result reset and return to the idle page;
   - preservation of `has_food_scraps` and `hasFoodScraps` without a food
     destination.

8. Record the deployed `mapping_version` and request IDs from smoke tests.

## Rollback procedure

1. Stop accepting new kiosk requests or place the kiosk in maintenance mode.
2. Restore the previously approved frontend image and its previous
   `MODEL_BASE_URL` value.
3. Restore the previously approved backend image/configuration if the new
   backend was changed.
4. Restart only the affected services with administrator approval.
5. Verify the previous `/health` and `/predict` behavior, then run the
   existing frontend smoke test.
6. Preserve the Phase 1–3 JSON configuration for investigation; do not delete
   or overwrite it during rollback.

## Remaining blockers

- The Phase 1–3 FastAPI container is not currently running in the inspected
  environment.
- The private backend service address must be configured before prediction
  routes can reach FastAPI.
- A live browser smoke test has not been run in this review.
