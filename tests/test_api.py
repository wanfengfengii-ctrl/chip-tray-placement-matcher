"""API-level tests for the inspection endpoint."""

from __future__ import annotations

import copy
import json
import random

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ENDPOINT = "/api/v1/inspect"


def post(payload=None, raw: bytes | None = None):
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return client.post(
        ENDPOINT, files={"file": ("payload.json", data, "application/json")}
    )


def valid_payload():
    return {
        "batch_id": "BATCH-20260915-001",
        "tolerance": 10,
        "sockets": [
            {"id": "S1", "x": 0, "y": 0},
            {"id": "S2", "x": 10, "y": 0},
        ],
        "detections": [
            {"id": "D1", "x": 9, "y": 0},
            {"id": "D2", "x": 0, "y": 0},
        ],
    }


class TestPass:
    def test_pass_response_shape_and_optimum(self):
        response = post(valid_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASS"
        assert body["batch_id"] == "BATCH-20260915-001"
        assert body["min_total_cost"] == 1
        assert body["pairs"] == [
            {"socket_id": "S1", "detection_id": "D2"},
            {"socket_id": "S2", "detection_id": "D1"},
        ]

    def test_pairs_ordered_by_socket_id_not_input_order(self):
        payload = valid_payload()
        payload["sockets"] = list(reversed(payload["sockets"]))
        payload["detections"] = list(reversed(payload["detections"]))
        body = post(payload).json()
        assert [p["socket_id"] for p in body["pairs"]] == ["S1", "S2"]

    def test_reordering_arrays_yields_byte_identical_response(self):
        payload = valid_payload()
        # More points (with ties) to make the check meaningful.
        payload["sockets"] = [
            {"id": f"S{i}", "x": i * 10, "y": (i % 3) * 5} for i in range(8)
        ]
        payload["detections"] = [
            {"id": f"D{i}", "x": i * 10 + 1, "y": (i % 3) * 5} for i in range(8)
        ]
        payload["tolerance"] = 20
        baseline = post(payload)
        assert baseline.json()["status"] == "PASS"

        rng = random.Random(42)
        for _ in range(10):
            shuffled = copy.deepcopy(payload)
            rng.shuffle(shuffled["sockets"])
            rng.shuffle(shuffled["detections"])
            response = post(shuffled)
            assert response.content == baseline.content


class TestBusinessFailures:
    def test_count_mismatch(self):
        payload = valid_payload()
        payload["detections"] = payload["detections"][:1]
        response = post(payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "COUNT_MISMATCH"
        assert body["socket_count"] == 2
        assert body["detection_count"] == 1
        assert "pairs" not in body
        assert "min_total_cost" not in body

    def test_position_mismatch_when_no_perfect_matching(self):
        payload = valid_payload()
        payload["tolerance"] = 3
        payload["detections"] = [
            {"id": "D1", "x": 9000, "y": 9000},
            {"id": "D2", "x": 8000, "y": 8000},
        ]
        response = post(payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "POSITION_MISMATCH"
        assert "pairs" not in body
        assert "min_total_cost" not in body

    def test_position_mismatch_does_not_leak_partial_matching(self):
        # S2/D2 coincide perfectly, S1/D1 are out of tolerance: the matching
        # as a whole fails and no partial pairing may be exposed.
        payload = {
            "batch_id": "B-LEAK",
            "tolerance": 2,
            "sockets": [{"id": "S1", "x": 0, "y": 0}, {"id": "S2", "x": 50, "y": 50}],
            "detections": [
                {"id": "D1", "x": 100, "y": 100},
                {"id": "D2", "x": 50, "y": 50},
            ],
        }
        body = post(payload).json()
        assert body["status"] == "POSITION_MISMATCH"
        assert "pairs" not in body


class TestValidationErrors:
    def _assert_error(self, response, status_code=400, code="VALIDATION_ERROR"):
        assert response.status_code == status_code
        body = response.json()
        assert body["error"]["code"] == code
        assert body["error"]["message"]
        assert "pairs" not in body

    def test_duplicate_ids_within_one_category(self):
        payload = valid_payload()
        payload["sockets"][1]["id"] = "S1"
        self._assert_error(post(payload))

    def test_duplicate_ids_across_categories(self):
        payload = valid_payload()
        payload["detections"][0]["id"] = "S1"
        self._assert_error(post(payload))

    def test_non_integer_coordinates_rejected(self):
        for bad in (1.5, "5", True, None, [1], {"v": 1}):
            payload = valid_payload()
            payload["sockets"][0]["x"] = bad
            self._assert_error(post(payload), code="VALIDATION_ERROR")

    def test_integral_float_coordinate_rejected(self):
        payload = valid_payload()
        payload["sockets"][0]["x"] = 1.0  # not a JSON integer
        self._assert_error(post(payload))

    def test_coordinate_out_of_range(self):
        for bad in (-1, 10001, 10**9):
            payload = valid_payload()
            payload["sockets"][0]["y"] = bad
            self._assert_error(post(payload))

    def test_coordinate_boundary_values_accepted(self):
        payload = valid_payload()
        payload["sockets"][0]["x"] = 0
        payload["sockets"][0]["y"] = 10000
        payload["detections"][1]["x"] = 0
        payload["detections"][1]["y"] = 10000
        payload["tolerance"] = 500
        assert post(payload).json()["status"] == "PASS"

    def test_tolerance_out_of_range_or_non_integer(self):
        for bad in (-1, 501, 2.5, "10"):
            payload = valid_payload()
            payload["tolerance"] = bad
            self._assert_error(post(payload))

    def test_unknown_top_level_field(self):
        payload = valid_payload()
        payload["operator"] = "alice"
        self._assert_error(post(payload))

    def test_unknown_point_field(self):
        payload = valid_payload()
        payload["sockets"][0]["z"] = 5
        self._assert_error(post(payload))

    def test_missing_required_field(self):
        payload = valid_payload()
        del payload["tolerance"]
        self._assert_error(post(payload))

    def test_too_many_points(self):
        payload = valid_payload()
        payload["sockets"] = [
            {"id": f"S{i}", "x": 0, "y": 0} for i in range(81)
        ]
        payload["detections"] = [
            {"id": f"D{i}", "x": 0, "y": 0} for i in range(81)
        ]
        self._assert_error(post(payload))

    def test_exactly_80_points_accepted(self):
        payload = valid_payload()
        payload["sockets"] = [
            {"id": f"S{i}", "x": i, "y": 0} for i in range(80)
        ]
        payload["detections"] = [
            {"id": f"D{i}", "x": i, "y": 0} for i in range(80)
        ]
        payload["tolerance"] = 0
        assert post(payload).json()["status"] == "PASS"

    def test_empty_id_rejected(self):
        payload = valid_payload()
        payload["sockets"][0]["id"] = ""
        self._assert_error(post(payload))

    def test_invalid_json(self):
        response = post(raw=b'{"batch_id": "B", "tolerance": 1,')
        self._assert_error(response, code="INVALID_JSON")

    def test_top_level_not_an_object(self):
        response = post(raw=b"[1, 2, 3]")
        self._assert_error(response, code="VALIDATION_ERROR")

    def test_missing_file_field(self):
        response = client.post(ENDPOINT)
        self._assert_error(response, code="VALIDATION_ERROR")

    def test_file_over_1mib_rejected(self):
        big = {
            "batch_id": "B",
            "tolerance": 0,
            "sockets": [],
            "detections": [],
            "padding": "A" * (1024 * 1024),
        }
        raw = json.dumps(big).encode("utf-8")
        assert len(raw) > 1024 * 1024
        self._assert_error(post(raw=raw), status_code=413, code="FILE_TOO_LARGE")

    def test_file_exactly_1mib_accepted(self):
        # Exactly 1 MiB must NOT be rejected for its size (it may still fail
        # schema validation because of the padding field — that is fine).
        big = {
            "batch_id": "B",
            "tolerance": 0,
            "sockets": [],
            "detections": [],
            "padding": "",
        }
        raw = json.dumps(big).encode("utf-8")
        pad = 1024 * 1024 - len(raw)
        big["padding"] = "A" * pad
        raw = json.dumps(big).encode("utf-8")
        assert len(raw) == 1024 * 1024
        response = post(raw=raw)
        assert response.status_code == 400  # unknown field, but not 413
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestHealth:
    def test_healthz(self):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
