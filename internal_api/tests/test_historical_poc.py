import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from internal_api.historical_poc import (
    GT_HISTORICAL,
    GT_HUMAN,
    build_poc_dataset,
    compute_metrics,
    public_row,
)

JPEG = b"\xff\xd8\xff" + b"0" * 32


def _mapping():
    bins = {
        "ICT-001-B04": SimpleNamespace(bin_id="ICT-001-B04", bin_number=4, bin_name="paper"),
        "ICT-001-B05": SimpleNamespace(bin_id="ICT-001-B05", bin_number=5, bin_name="glass"),
        "ICT-001-B03": SimpleNamespace(bin_id="ICT-001-B03", bin_number=3, bin_name="plastic"),
    }
    return SimpleNamespace(
        category_mapping={"paper": "ICT-001-B04", "glass": "ICT-001-B05", "plastic": "ICT-001-B03"},
        bins=bins,
    )


def _record(request_id, **overrides):
    record = {
        "request_id": request_id,
        "timestamp": "2026-10-21T07:00:00Z",
        "location_id": "ICT-001",
        "image_id": f"images/2026/10/21/{request_id}.jpg",
        "image_path": f"images/2026/10/21/{request_id}.jpg",
        "image_save_status": "saved",
        "object_name_th": "กระดาษ",
        "waste_category": "paper",
        "confidence": 0.9,
        "bin_number": 4,
        "bin_id": "ICT-001-B04",
        "bin_name": "paper",
        "mapping_version": "ICT-10-bin-v2",
        "classification_status": "confirmed",
        "status": "success",
        "error_type": None,
        "model_identifier": "qwen3.8:27b",
    }
    record.update(overrides)
    return record


def _annotation(request_id, action, category, revision=1, **extra):
    row = {
        "request_id": request_id,
        "image_id": f"images/2026/10/21/{request_id}.jpg",
        "revision": revision,
        "reviewer_id": "tester",
        "reviewed_at": "2026-10-28T06:00:00Z",
        "review_action": action,
        "review_status": "reviewed" if action in {"confirm", "correct"} else action,
        "is_final": action in {"confirm", "correct", "unreviewable"},
        "ground_truth_waste_category": category,
        "note": None,
    }
    row.update(extra)
    return row


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _make_root(root, records, annotations):
    for record in records:
        image = root / record["image_id"]
        if record.get("_write_image", True):
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(JPEG)
    clean_records = [{k: v for k, v in record.items() if not k.startswith("_")} for record in records]
    _write_jsonl(root / "logs" / "inference.jsonl", clean_records)
    _write_jsonl(root / "review" / "ground_truth_annotations.jsonl", annotations)


def _digest_tree(root):
    digests = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digests[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


class HistoricalPocDatasetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "storage"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _build(self):
        return build_poc_dataset([("configured_storage", self.root)], _mapping())

    def test_rules_select_human_verified_demo_and_exclusions(self):
        records = [
            _record("confirmed-paper"),                                   # human confirm, correct prediction
            _record("corrected-glass", waste_category="paper", bin_number=4),  # human correct -> glass
            _record("plastic-human", waste_category="plastic", bin_number=3),  # bin not evaluable (plastic)
            _record("demo-only", waste_category="paper"),                  # no annotation at all
            _record("unreviewable", waste_category="paper"),
            _record("timeout", status="api_error", error_type="timeout", waste_category=None, bin_number=None),
            _record("missing-image", waste_category="paper", _write_image=False),
            _record("no-bin", bin_number=None),
            _record("pilot-only", waste_category="paper"),
            _record("skip-only", waste_category="paper"),
        ]
        annotations = [
            _annotation("confirmed-paper", "confirm", "paper"),
            _annotation("corrected-glass", "correct", "glass"),
            _annotation("plastic-human", "confirm", "plastic"),
            _annotation("unreviewable", "unreviewable", None),
            _annotation("pilot-only", "confirm", "glass", evaluation_scope="pilot"),
            _annotation("skip-only", "skip", None),
        ]
        _make_root(self.root, records, annotations)

        dataset = self._build()
        by_id = {row["request_id"]: row for row in dataset["rows"]}
        summary = dataset["summary"]

        self.assertEqual(set(by_id), {"confirmed-paper", "corrected-glass", "plastic-human", "demo-only", "pilot-only"})
        self.assertEqual(by_id["confirmed-paper"]["ground_truth_source"], GT_HUMAN)
        self.assertTrue(by_id["confirmed-paper"]["classification_correct"])
        self.assertTrue(by_id["confirmed-paper"]["bin_correct"])

        corrected = by_id["corrected-glass"]
        self.assertEqual(corrected["ground_truth_waste_category"], "glass")
        self.assertFalse(corrected["classification_correct"])
        self.assertEqual(corrected["expected_bin_number"], 5)
        self.assertFalse(corrected["bin_correct"])

        self.assertEqual(by_id["plastic-human"]["bin_ground_truth_status"], "not_evaluable_plastic")
        self.assertIsNone(by_id["plastic-human"]["bin_correct"])

        demo = by_id["demo-only"]
        self.assertEqual(demo["ground_truth_source"], GT_HISTORICAL)
        self.assertTrue(demo["demo_only"])
        self.assertIsNone(demo["classification_correct"])
        self.assertIsNone(demo["ground_truth_waste_category"])

        # pilot-only annotations are ignored, so the record falls back to demo-only.
        self.assertEqual(by_id["pilot-only"]["ground_truth_source"], GT_HISTORICAL)

        self.assertEqual(summary["excluded"]["reviewer_unreviewable"], 1)
        self.assertEqual(summary["excluded"]["not_success"], 1)
        self.assertEqual(summary["excluded"]["image_missing"], 1)
        self.assertEqual(summary["excluded"]["prediction_incomplete"], 1)
        self.assertEqual(summary["excluded"]["annotation_not_confirmed"], 1)  # skip-only
        self.assertEqual(summary["pool"], {"eligible": 5, GT_HUMAN: 3, GT_HISTORICAL: 2})
        self.assertEqual(summary["review_needed"], 2)
        self.assertEqual(summary["outcome_totals"]["timeout"], 1)

    def test_metrics_use_only_human_verified_rows(self):
        records = [
            _record("h1"),
            _record("h2", waste_category="paper"),
            _record("demo", waste_category="glass", bin_number=5),
        ]
        annotations = [
            _annotation("h1", "confirm", "paper"),
            _annotation("h2", "correct", "glass"),
        ]
        _make_root(self.root, records, annotations)

        metrics = self._build()["summary"]["metrics"]

        self.assertEqual(metrics["classification_sample_size"], 2)
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["bin_sample_size"], 2)
        self.assertEqual(metrics["bin_accuracy"], 0.5)
        self.assertEqual(metrics["per_label"]["paper"]["support"], 1)
        self.assertEqual(metrics["per_label"]["glass"]["recall"], 0.0)

    def test_no_human_labels_yields_none_not_zero(self):
        _make_root(self.root, [_record("demo")], [])
        metrics = self._build()["summary"]["metrics"]
        self.assertIsNone(metrics["accuracy"])
        self.assertIsNone(metrics["macro_f1"])
        self.assertIsNone(metrics["bin_accuracy"])
        self.assertEqual(metrics["classification_sample_size"], 0)

    def test_compute_metrics_macro_f1_matches_hand_calculation(self):
        rows = [
            {"ground_truth_source": GT_HUMAN, "ground_truth_waste_category": "paper",
             "historical_prediction": {"waste_category": "paper"}, "classification_correct": True,
             "expected_bin_number": 4, "bin_correct": True},
            {"ground_truth_source": GT_HUMAN, "ground_truth_waste_category": "paper",
             "historical_prediction": {"waste_category": "glass"}, "classification_correct": False,
             "expected_bin_number": 4, "bin_correct": False},
        ]
        metrics = compute_metrics(rows)
        # paper: P=1, R=0.5, F1=2/3; glass: no true positives, F1=0. Macro = 1/3.
        self.assertAlmostEqual(metrics["macro_f1"], round(1 / 3, 4))
        self.assertEqual(metrics["accuracy"], 0.5)

    def test_sources_are_never_modified(self):
        _make_root(self.root, [_record("h1"), _record("demo", waste_category="glass", bin_number=5)],
                   [_annotation("h1", "confirm", "paper")])
        before = _digest_tree(self.root)
        self._build()
        self.assertEqual(_digest_tree(self.root), before)

    def test_duplicate_request_ids_across_sources_are_counted_once(self):
        other = Path(self._tmp.name) / "legacy"
        other.mkdir()
        _make_root(self.root, [_record("shared")], [])
        _make_root(other, [_record("shared")], [])
        dataset = build_poc_dataset([("configured_storage", self.root), ("legacy_storage", other)], _mapping())
        self.assertEqual(len(dataset["rows"]), 1)
        self.assertEqual(dataset["summary"]["excluded"]["duplicate_request_id"], 1)

    def test_public_rows_hide_internal_paths(self):
        _make_root(self.root, [_record("demo")], [])
        row = public_row(self._build()["rows"][0])
        self.assertFalse(any(key.startswith("_") for key in row))
        self.assertNotIn(str(self.root), json.dumps(row))


if __name__ == "__main__":
    unittest.main()
