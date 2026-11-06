"""Run the fixed, read-only qwen3.8:27b diagnostic manifest sequentially."""

from __future__ import annotations

import ast
import base64
import csv
import json
import math
import mimetypes
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from internal_api.mapping_config import (
    SUPPORTED_MATERIAL_SUBTYPES,
    SUPPORTED_VISIBLE_CONDITIONS,
    SUPPORTED_WASTE_CATEGORIES,
    configured_object_name_override,
    load_location_config,
    normalize_material_subtype,
    normalize_visible_condition,
    normalize_waste_category,
)
from internal_api.mapping_service import MappingService
from internal_api.model_request import build_model_request_payload


MANIFEST_PATH = HERE / "qwen3_8_27b_manifest.json"
CSV_PATH = HERE / "qwen3_8_27b_results.csv"
REPORT_PATH = HERE / "qwen3_8_27b_report.md"
TIMEOUT_SECONDS = 40
MODEL = "qwen3.8:27b"


def load_dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def production_prompt() -> str:
    source = ast.parse((ROOT / "internal_api" / "app.py").read_text(encoding="utf-8"))
    for node in source.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PROMPT" for target in node.targets
        ):
            prompt = ast.literal_eval(node.value)
            if isinstance(prompt, str):
                return prompt
    raise RuntimeError("Current production PROMPT assignment was not found")


def chat_completions_url(api_url: str) -> str:
    parts = urlsplit(api_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)]
        return "\n".join(parts)
    return ""


def valid_production_contract(value: object) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "top_level_not_object"
    required = {
        "object_name_th", "waste_category", "material_subtype", "visible_object_detected",
        "classification_reason", "resin_identification_code", "visible_condition", "confidence",
        "top_candidates", "has_food_scraps", "has_liquid",
    }
    if not required.issubset(value):
        return False, "missing_required_fields"
    if not isinstance(value["object_name_th"], str) or not value["object_name_th"].strip():
        return False, "invalid_object_name"
    if normalize_waste_category(value["waste_category"]) not in SUPPORTED_WASTE_CATEGORIES:
        return False, "invalid_category"
    if normalize_material_subtype(value["material_subtype"]) not in SUPPORTED_MATERIAL_SUBTYPES:
        return False, "invalid_subtype"
    if not isinstance(value["visible_object_detected"], bool):
        return False, "invalid_visibility"
    if not isinstance(value["classification_reason"], str) or not value["classification_reason"]:
        return False, "invalid_classification_reason"
    if value["resin_identification_code"] not in {"1", "2", "3", "4", "5", "6", "7", "unknown"}:
        return False, "invalid_resin_code"
    if normalize_visible_condition(value["visible_condition"]) not in SUPPORTED_VISIBLE_CONDITIONS:
        return False, "invalid_visible_condition"
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return False, "invalid_confidence"
    if not isinstance(value["has_food_scraps"], bool) or not isinstance(value["has_liquid"], bool):
        return False, "invalid_contamination_fields"
    candidates = value["top_candidates"]
    if not isinstance(candidates, list) or len(candidates) != 3:
        return False, "candidate_count_not_three"
    previous = math.inf
    for candidate in candidates:
        if not isinstance(candidate, dict) or not {
            "object_name_th", "waste_category", "material_subtype", "confidence"
        }.issubset(candidate):
            return False, "invalid_candidate_shape"
        if not isinstance(candidate["object_name_th"], str) or not candidate["object_name_th"].strip():
            return False, "invalid_candidate_name"
        if normalize_waste_category(candidate["waste_category"]) not in SUPPORTED_WASTE_CATEGORIES:
            return False, "invalid_candidate_category"
        if normalize_material_subtype(candidate["material_subtype"]) not in SUPPORTED_MATERIAL_SUBTYPES:
            return False, "invalid_candidate_subtype"
        score = candidate["confidence"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            return False, "invalid_candidate_confidence"
        if score > previous:
            return False, "candidate_scores_not_ranked"
        previous = float(score)
    first = candidates[0]
    if (
        first["object_name_th"] != value["object_name_th"]
        or normalize_waste_category(first["waste_category"]) != normalize_waste_category(value["waste_category"])
        or normalize_material_subtype(first["material_subtype"]) != normalize_material_subtype(value["material_subtype"])
        or float(first["confidence"]) != float(confidence)
    ):
        return False, "primary_candidate_mismatch"
    return True, "valid"


def safe_diagnostics(result: object) -> tuple[int | None, str, int, int, dict | None, str]:
    if not isinstance(result, dict):
        return None, "", 0, 0, None, "invalid_response_envelope"
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "", 0, 0, None, "missing_choice"
    choice = choices[0]
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message_text(message.get("content"))
    reasoning = message.get("reasoning_content")
    if not isinstance(reasoning, str):
        reasoning = message.get("reasoning")
    reasoning_length = len(reasoning) if isinstance(reasoning, str) else 0
    finish = str(choice.get("finish_reason") or "")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    completion_tokens = usage.get("completion_tokens")
    completion_tokens_text = str(completion_tokens) if isinstance(completion_tokens, (int, float)) else ""
    return None, finish, len(content), reasoning_length, usage, completion_tokens_text


def run_one(sample: dict, prompt: str, endpoint: str, api_key: str, token_limit: int, mapper: MappingService, config) -> dict:
    row = dict(sample)
    image_path = Path(sample["image_path"])
    payload = build_model_request_payload(
        model=MODEL,
        prompt=prompt,
        image_data_url=image_data_url(image_path),
        completion_token_limit=token_limit,
    )
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
        method="POST",
    )
    started = time.monotonic()
    status = None
    response_obj = None
    transport_outcome = ""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            response_obj = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        transport_outcome = f"http_{exc.code}"
    except TimeoutError:
        transport_outcome = "timeout"
    except urllib.error.URLError as exc:
        transport_outcome = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
    except Exception:
        transport_outcome = "transport_error"
    latency = time.monotonic() - started

    finish_reason = ""
    content_length = 0
    reasoning_length = 0
    completion_tokens = ""
    usage = {}
    classification = None
    contract_valid = False
    contract_issue = ""
    if isinstance(response_obj, dict):
        choices = response_obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            content = message_text(message.get("content"))
            reasoning = message.get("reasoning_content")
            if not isinstance(reasoning, str):
                reasoning = message.get("reasoning")
            finish_reason = str(choice.get("finish_reason") or "")
            content_length = len(content)
            reasoning_length = len(reasoning) if isinstance(reasoning, str) else 0
            usage = response_obj.get("usage") if isinstance(response_obj.get("usage"), dict) else {}
            token_count = usage.get("completion_tokens")
            completion_tokens = token_count if isinstance(token_count, (int, float)) else ""
            if not content.strip():
                transport_outcome = "empty_content"
                contract_issue = "empty_content"
            else:
                try:
                    classification = json.loads(content)
                except json.JSONDecodeError:
                    contract_issue = "malformed_json"
                else:
                    contract_valid, contract_issue = valid_production_contract(classification)
        else:
            transport_outcome = transport_outcome or "invalid_response_envelope"
            contract_issue = "missing_choice"
    elif not transport_outcome:
        transport_outcome = "invalid_response_envelope"

    category = subtype = condition = object_name = ""
    confidence_pct = ""
    predicted_bin = ""
    mapping_reason = ""
    override_applied = False
    category_correct = bin_correct = ""
    incorrect_reason = ""
    if contract_valid and isinstance(classification, dict):
        object_name = classification["object_name_th"]
        category = normalize_waste_category(classification["waste_category"])
        subtype = normalize_material_subtype(classification["material_subtype"])
        condition = normalize_visible_condition(classification["visible_condition"])
        confidence_pct = round(float(classification["confidence"]) * 100, 2)
        override = configured_object_name_override(object_name, config.object_name_overrides)
        if override:
            category = normalize_waste_category(override["waste_category"])
            subtype = normalize_material_subtype(override["material_subtype"])
        decision = mapper.resolve(
            location_id=config.location_id,
            waste_category=classification["waste_category"],
            confidence=classification["confidence"],
            object_name_th=object_name,
            material_subtype=classification["material_subtype"],
            visible_condition=condition,
            has_food_scraps=classification["has_food_scraps"],
            has_liquid=classification["has_liquid"],
        )
        predicted_bin = decision.bin_number if decision.bin_number is not None else ""
        mapping_reason = decision.mapping_reason or ""
        override_applied = decision.object_name_override_applied
        category_correct = category == sample["expected_category"]
        bin_correct = predicted_bin == sample["expected_bin"]
        if not category_correct:
            incorrect_reason = f"expected category {sample['expected_category']}, got {category}"
        elif not bin_correct:
            incorrect_reason = f"expected Bin {sample['expected_bin']}, got {predicted_bin or 'no bin'}"
    elif not contract_valid:
        incorrect_reason = f"No mapping: {transport_outcome or contract_issue} ({contract_issue})"

    row.update({
        "model_object_name": object_name,
        "model_category": category,
        "model_subtype": subtype,
        "model_condition": condition,
        "confidence_percent": confidence_pct,
        "predicted_bin": predicted_bin,
        "mapping_reason": mapping_reason,
        "object_name_override_applied": override_applied,
        "json_valid": contract_valid,
        "contract_issue": contract_issue,
        "http_status": status if status is not None else "",
        "outcome": transport_outcome or ("valid" if contract_valid else "invalid_json_or_contract"),
        "latency_seconds": round(latency, 3),
        "finish_reason": finish_reason,
        "content_length": content_length,
        "reasoning_content_length": reasoning_length,
        "completion_tokens": completion_tokens,
        "category_correct": category_correct,
        "bin_correct": bin_correct,
        "incorrect_reason": incorrect_reason,
    })
    return row


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_outputs(manifest: dict, rows: list[dict], token_limit: int) -> None:
    fields = [
        "request_id", "image_path", "expected_object", "expected_category", "expected_subtype",
        "expected_condition", "expected_bin", "expected_bin_reason", "model_object_name", "model_category",
        "model_subtype", "model_condition", "confidence_percent", "predicted_bin", "mapping_reason",
        "object_name_override_applied", "json_valid", "contract_issue", "http_status", "outcome",
        "latency_seconds", "finish_reason", "content_length", "reasoning_content_length", "completion_tokens",
        "category_correct", "bin_correct", "incorrect_reason",
    ]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    total = len(rows)
    valid_count = sum(row["json_valid"] is True for row in rows)
    timeout_error_count = sum(row["outcome"] in {"timeout", "transport_error"} or str(row["outcome"]).startswith("http_") for row in rows)
    empty_count = sum(row["outcome"] == "empty_content" for row in rows)
    category_labeled = [row for row in rows if row.get("expected_category")]
    bin_labeled = [row for row in rows if row.get("expected_bin") is not None]
    category_compared = [row for row in category_labeled if isinstance(row.get("category_correct"), bool)]
    bin_compared = [row for row in bin_labeled if isinstance(row.get("bin_correct"), bool)]
    latencies = sorted(float(row["latency_seconds"]) for row in rows)
    median_latency = statistics.median(latencies) if latencies else 0.0
    p95_latency = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else 0.0
    category_acc = (sum(r["category_correct"] for r in category_compared) / len(category_compared)) if category_compared else None
    bin_acc = (sum(r["bin_correct"] for r in bin_compared) / len(bin_compared)) if bin_compared else None

    lines = [
        "# qwen3.8:27b offline diagnostic sample",
        "",
        f"Run time (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "This is a small, offline diagnostic sample, not a production evaluation. Requests were sent sequentially with no automatic retries. No `/predict` route was called. The stored scans were read only; expected labels were assigned by direct image review and ICT-001 mapping v4, not by historical model predictions.",
        "",
        f"Model: `{MODEL}`; prompt: `qwen-waste-classification-v10`; mapping: `{manifest['mapping_version']}`; timeout: {TIMEOUT_SECONDS}s; bounded completion tokens: {token_limit}.",
        "",
        "## Per-image results",
        "",
        "| Request ID | Expected object → Bin | Model object / category / subtype / condition | Confidence | Predicted Bin | Mapping reason | JSON | HTTP / outcome | Latency | Category correct | Bin correct | If incorrect |",
        "|---|---|---|---:|---:|---|---|---|---:|---|---|---|",
    ]
    for r in rows:
        expected = f"{r['expected_object']} → {r['expected_bin']} ({r['expected_bin_reason']})"
        model_result = " / ".join([str(r.get("model_object_name") or "—"), str(r.get("model_category") or "—"), str(r.get("model_subtype") or "—"), str(r.get("model_condition") or "—")])
        lines.append("| " + " | ".join(markdown_cell(x) for x in [
            r["request_id"], expected, model_result,
            f"{r['confidence_percent']}%" if r["confidence_percent"] != "" else "—",
            r["predicted_bin"] or "—", r["mapping_reason"] or "—",
            "valid" if r["json_valid"] else f"invalid ({r['contract_issue']})",
            f"{r['http_status'] or '—'} / {r['outcome']}", f"{r['latency_seconds']:.3f}s",
            r["category_correct"] if r["category_correct"] != "" else "—",
            r["bin_correct"] if r["bin_correct"] != "" else "—", r["incorrect_reason"] or "—",
        ]) + " |")
    lines += [
        "",
        "## Aggregate metrics",
        "",
        f"- Valid JSON plus current three-candidate contract: {valid_count}/{total} ({valid_count / total * 100:.1f}%).",
        f"- Timeout/transport/HTTP error rate: {timeout_error_count}/{total} ({timeout_error_count / total * 100:.1f}%). Empty-content responses: {empty_count}/{total}.",
        f"- Category accuracy over human-reviewed expected labels with a valid model result: {sum(r['category_correct'] for r in category_compared)}/{len(category_compared)} ({category_acc * 100:.1f}%). Invalid/unavailable rows are excluded from this accuracy denominator; coverage is {len(category_compared)}/{len(category_labeled)}.",
        f"- Bin accuracy over human-reviewed expected labels with a valid mapped result: {sum(r['bin_correct'] for r in bin_compared)}/{len(bin_compared)} ({bin_acc * 100:.1f}%). Invalid/unavailable rows are excluded from this accuracy denominator; coverage is {len(bin_compared)}/{len(bin_labeled)}.",
        f"- Median latency: {median_latency:.3f}s. P95 latency: {p95_latency:.3f}s (nearest-rank method; all attempted rows).",
        "- Confidence is the model’s reported score and is not an accuracy measure.",
        "",
        "## Sample coverage notes",
        "",
    ]
    lines.extend(f"- {note}" for note in manifest.get("coverage_gaps", []))
    lines += [
        "",
        "## Safe diagnostics",
        "",
        "Only response status, timing, finish reason, lengths, token usage, parsed classification fields, and mapping decisions are recorded. API credentials, full response content/reasoning, and image payloads are not written to the report.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    env = load_dotenv_values(ROOT / "internal_api" / ".env")
    api_url = env.get("QWEN_API_URL")
    if not api_url:
        raise RuntimeError("QWEN_API_URL is not configured")
    api_key = env.get("QWEN_API_KEY", "")
    try:
        token_limit = max(1, min(int(env.get("QWEN_MAX_COMPLETION_TOKENS", "1000")), 2048))
    except ValueError:
        token_limit = 1000
    prompt = production_prompt()
    config = load_location_config(manifest["location_id"])
    mapper = MappingService(config)
    rows = []
    for sample in manifest["samples"]:
        rows.append(run_one(sample, prompt, chat_completions_url(api_url), api_key, token_limit, mapper, config))
        write_outputs(manifest, rows, token_limit)
        print(json.dumps({
            "request_id": sample["request_id"],
            "http_status": rows[-1]["http_status"],
            "outcome": rows[-1]["outcome"],
            "json_valid": rows[-1]["json_valid"],
            "latency_seconds": rows[-1]["latency_seconds"],
        }, ensure_ascii=False))
    print(json.dumps({"csv": str(CSV_PATH), "markdown": str(REPORT_PATH), "rows": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
