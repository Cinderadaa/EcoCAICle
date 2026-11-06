# Classifier configuration comparison

## Scope and live state

This comparison is preparatory and records the earlier state. No running service
was changed or restarted during that comparison.

**Configuration update (2026-11-06):** after the separate offline error analysis,
`QWEN_MODEL` was set to `qwen3.8:27b` in the root and backend local `.env` files;
the application and Docker defaults were also changed to `qwen3.8:27b`. This
prepares the next process start/image build. The running process was not restarted,
so its active model was not verified or changed by these file edits.

The successful historical request `3a00534d-75be-4611-afb2-80f63e6b9a03`
records `qwen3-vl:4b`, prompt version `qwen-waste-classification-v2`, an
`ewaste` classification at confidence `0.9`, and Bin 7. At the time of the
comparison, the backend and root environment files had different model tags;
the local model configuration update above now selects `qwen3.8:27b` in both
files. The working tree prompt version remains
`qwen-waste-classification-v9`. Deployment/runtime environment precedence still
needs to be checked before claiming the running process uses the updated tag.

## Git provenance and restore boundary

No reachable Git commit contains `qwen-waste-classification-v2`; searching
reachable history for the exact version string returned no match. Git's
unreachable objects also contain no matching prompt text or commit. The latest
reachable `internal_api/app.py` version is in commit `0c32cf5` and labels its
prompt `qwen-waste-classification-v7`. Therefore an exact commit hash for v2
cannot be identified from this repository, and the historical v2 prompt text
cannot be restored faithfully from its Git history here.

The prompt-related code surface to compare, once the original v2 text/source is
available, is limited to:

- `internal_api/app.py`: `PROMPT_VERSION` and the `PROMPT` string.
- `internal_api/app.py`: the user message's text content built from `PROMPT`
  in the `/predict` LiteLLM payload.
- `ECOCAICLE_PROMPT_VERSION` controls the version label only; setting it to
  `qwen-waste-classification-v2` does not restore or alter prompt text.

No mapping, camera, frontend, storage, or Scan History code is part of this
prompt comparison.

## Controlled route check

One request was sent to the configured LiteLLM route with only the request's
model overridden to `qwen3-vl:4b`. It used the current v9 prompt and the
repository fixture `real_test/other.jpg` (a drink cup with a straw). The call
timed out at the configured 40-second limit: there was no HTTP response, no
`message.content`, and no JSON classification to validate. This verifies that
the old model route is currently blocked or too slow for the required limit;
it does not compare mobile-phone classification and does not validate prompt
v2.

Do not switch the live model based on this result. The historical prompt may
only be restored for a meaningful comparison after its exact text is recovered
from the historical deployment/source. The model route must also pass a
controlled request with non-empty content, valid JSON classification, and
latency below 40 seconds.

## Environment switch and rollback

The model selection is `QWEN_MODEL` in the backend process environment. A
future controlled comparison would set `QWEN_MODEL=qwen3-vl:4b` for that
backend process and restart it. The current value to restore is
`QWEN_MODEL=qwen3.8:27b`. `ECOCAICLE_PROMPT_VERSION` should be set to v2 only
when the matching v2 prompt text is actually installed; otherwise keep
`qwen-waste-classification-v9`.

If a future comparison is installed and fails, rollback is limited to
restoring `QWEN_MODEL=qwen3.8:27b`, restoring the saved current `PROMPT` text
and `PROMPT_VERSION=qwen-waste-classification-v9`, then restarting the
backend. No mapping/configuration changes are needed.
