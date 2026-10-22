# EcoAICle Phase 1–3 Deployment Runbook

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
ECOAICLE_LOCATION_ID=ICT-001
ECOAICLE_MAPPING_CONFIG_DIR=/app/config/locations
```

`ECOAICLE_MAPPING_CONFIG_DIR` may be omitted because the backend default is
the same directory, but setting it explicitly is recommended for deployment
visibility. Startup validation rejects missing or invalid configuration before
the service becomes ready. Mapping changes require a controlled backend
restart; there is no hot reload.

The frontend runtime should also receive `ECOAICLE_LOCATION_ID=ICT-001` so
proxy-generated `api_error` envelopes use the same static location metadata.
Successful responses still use the backend-provided location ID.

## Observed container topology

Read-only inspection on 2026-10-21 found:

- `ecoaicle-frontend` is attached to Docker network `nginx-proxy`.
- Its configured `MODEL_BASE_URL` is
  `http://host.docker.internal:8000`.
- `host.docker.internal` resolves from the frontend container and port 8000
  responds to `/health`.
- The service currently answering port 8000 identifies as `Image
  Classification API`; its OpenAPI `PredictResponse` contains only
  `label`, `confidence`, and `source`.
- No Phase 1–3 `internal_api` container was present in the inspected container
  set.

The current port 8000 target is therefore reachable but is not the Phase 2
mapping contract. It must not be used for a Phase 1–3 deployment.

## Required target configuration

The repository's FastAPI Dockerfile exposes port 8001. Under the current
host-gateway topology, the expected frontend runtime setting is:

```text
MODEL_BASE_URL=http://host.docker.internal:8001
```

This URL is conditional on the new `internal_api` container being published
on host port 8001. If the backend is instead attached to the same Docker
network, use its approved service DNS name and port, for example:

```text
MODEL_BASE_URL=http://<approved-internal-api-service>:8001
```

The service name must be supplied by the deployment administrator; no service
name is assumed by this repository.

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
   `ECOAICLE_LOCATION_ID=ICT-001` and the mapping directory above.
5. From the frontend container, verify:

   - `GET http://<backend>/health` returns HTTP 200 and `status: ok`.
   - `GET http://<backend>/openapi.json` includes `/predict`.
   - `/predict` exposes the Phase 2 fields `request_id`, `location_id`,
     `bin_id`, `bin_number`, `bin_name`, `mapping_version`,
     `classification_status`, `mapping_fallback`, and `status`.

6. Set the frontend runtime `MODEL_BASE_URL` to the verified Phase 1–3
   backend URL and perform the approved frontend restart.
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

- The currently configured port 8000 service is an older API and does not
  expose the Phase 1–3 response contract.
- The Phase 1–3 FastAPI container is not currently running in the inspected
  environment.
- The correct replacement `MODEL_BASE_URL` cannot be activated without an
  administrator-approved backend deployment/binding on port 8001 or an
  approved service DNS name.
- A live browser smoke test has not been run in this review.
