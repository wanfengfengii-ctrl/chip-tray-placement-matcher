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


class TestExcludedSockets:
    """Temporarily deactivated sockets (line changeover / socket maintenance)."""

    def four_socket_payload(self):
        return {
            "batch_id": "BATCH-EXCL",
            "tolerance": 10,
            "sockets": [
                {"id": "S1", "x": 0, "y": 0},
                {"id": "S2", "x": 10, "y": 0},
                {"id": "S3", "x": 20, "y": 0},
                {"id": "S4", "x": 30, "y": 0},
            ],
            "detections": [
                {"id": "D1", "x": 0, "y": 0},
                {"id": "D2", "x": 10, "y": 0},
            ],
        }

    def test_pass_after_excluding_sockets_echoes_sorted_ids(self):
        payload = self.four_socket_payload()
        # Declared out of order on purpose: the echo must be sorted.
        payload["excluded_socket_ids"] = ["S4", "S3"]
        response = post(payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASS"
        assert body["excluded_socket_ids"] == ["S3", "S4"]
        assert body["pairs"] == [
            {"socket_id": "S1", "detection_id": "D1"},
            {"socket_id": "S2", "detection_id": "D2"},
        ]
        # Cost and pairs are based on effective sockets only.
        assert body["min_total_cost"] == 0
        assert {p["socket_id"] for p in body["pairs"]}.isdisjoint(
            {"S3", "S4"}
        )

    def test_exclusion_order_rearrangement_is_byte_identical(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = ["S4", "S3"]
        baseline = post(payload)
        assert baseline.json()["status"] == "PASS"

        rng = random.Random(7)
        for _ in range(10):
            shuffled = copy.deepcopy(payload)
            rng.shuffle(shuffled["excluded_socket_ids"])
            rng.shuffle(shuffled["sockets"])
            response = post(shuffled)
            assert response.status_code == 200
            assert response.content == baseline.content

    def test_count_mismatch_uses_effective_socket_count(self):
        payload = self.four_socket_payload()
        # 4 sockets - 1 excluded = 3 vs 2 detections -> COUNT_MISMATCH.
        payload["excluded_socket_ids"] = ["S3"]
        response = post(payload)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "COUNT_MISMATCH"
        assert body["socket_count"] == 3
        assert body["detection_count"] == 2
        # Business failures keep their legacy shape: no echo, no pairs.
        assert "excluded_socket_ids" not in body
        assert "pairs" not in body
        assert "min_total_cost" not in body

    def test_matching_runs_on_effective_set(self):
        # S1 has no nearby detection; it must be paired (not silently ignored)
        # unless it is excluded.
        payload = {
            "batch_id": "BATCH-EXCL2",
            "tolerance": 2,
            "sockets": [
                {"id": "S1", "x": 500, "y": 500},
                {"id": "S2", "x": 10, "y": 0},
            ],
            "detections": [{"id": "D2", "x": 10, "y": 0}],
        }
        # Without exclusion: counts differ -> COUNT_MISMATCH.
        body = post(payload).json()
        assert body["status"] == "COUNT_MISMATCH"
        assert body["socket_count"] == 2

        # With S1 declared inactive: 1 vs 1, matching on the effective set.
        payload["excluded_socket_ids"] = ["S1"]
        body = post(payload).json()
        assert body["status"] == "PASS"
        assert body["min_total_cost"] == 0
        assert body["pairs"] == [{"socket_id": "S2", "detection_id": "D2"}]
        assert body["excluded_socket_ids"] == ["S1"]

    def test_position_mismatch_after_exclusion_has_no_partial_result(self):
        # Excluding S3 leaves S1/S2; D2 is far from S2, so no perfect
        # matching exists on the effective set.
        payload = {
            "batch_id": "BATCH-EXCL3",
            "tolerance": 2,
            "sockets": [
                {"id": "S1", "x": 0, "y": 0},
                {"id": "S2", "x": 10, "y": 0},
                {"id": "S3", "x": 20, "y": 0},
            ],
            "detections": [
                {"id": "D1", "x": 0, "y": 0},
                {"id": "D2", "x": 100, "y": 0},
            ],
            "excluded_socket_ids": ["S3"],
        }
        response = post(payload)
        assert response.status_code == 200
        body = response.json()
        assert body == {"status": "POSITION_MISMATCH", "batch_id": "BATCH-EXCL3"}

    def test_unknown_excluded_id_rejected(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = ["S4", "NOPE"]
        response = post(payload)
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert len(body["error"]["details"]) == 1
        detail = body["error"]["details"][0]
        assert detail["loc"] == ["excluded_socket_ids"]
        assert detail["type"] == "excluded_socket_ids_unknown"
        assert detail["msg"]
        assert "pairs" not in body

    def test_detection_id_is_not_a_valid_excluded_socket_id(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = ["D1"]
        response = post(payload)
        assert response.status_code == 400
        details = response.json()["error"]["details"]
        assert [d["loc"] for d in details] == [["excluded_socket_ids"]]
        assert details[0]["type"] == "excluded_socket_ids_unknown"

    def test_duplicate_excluded_ids_rejected(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = ["S3", "S3"]
        response = post(payload)
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        details = body["error"]["details"]
        assert [d["loc"] for d in details] == [["excluded_socket_ids"]]
        assert details[0]["type"] == "excluded_socket_ids_duplicate"
        assert "pairs" not in body

    def test_duplicate_takes_precedence_over_unknown(self):
        payload = self.four_socket_payload()
        # Both malformed: the duplicate is reported (checked first).
        payload["excluded_socket_ids"] = ["NOPE", "NOPE"]
        response = post(payload)
        details = response.json()["error"]["details"]
        assert len(details) == 1
        assert details[0]["loc"] == ["excluded_socket_ids"]
        assert details[0]["type"] == "excluded_socket_ids_duplicate"

    def test_excluded_ids_must_be_strings(self):
        payload = self.four_socket_payload()
        for bad in (["S3", 4], [True], [None], [1.5]):
            payload_bad = copy.deepcopy(payload)
            payload_bad["excluded_socket_ids"] = bad
            response = post(payload_bad)
            assert response.status_code == 400
            details = response.json()["error"]["details"]
            assert details[0]["loc"][0] == "excluded_socket_ids"

    def test_excluded_field_must_be_array(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = "S3"
        response = post(payload)
        assert response.status_code == 400
        details = response.json()["error"]["details"]
        assert details[0]["loc"] == ["excluded_socket_ids"]

    def test_explicit_null_rejected_but_absent_field_keeps_legacy(self):
        # A declared-but-null exclusion is ambiguous and must be rejected as a
        # field error (it is NOT equivalent to omitting the field).
        payload = self.four_socket_payload()
        payload["detections"].append({"id": "D3", "x": 20, "y": 0})
        payload["detections"].append({"id": "D4", "x": 30, "y": 0})
        payload["excluded_socket_ids"] = None
        response = post(payload)
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert len(body["error"]["details"]) == 1
        detail = body["error"]["details"][0]
        assert detail["loc"] == ["excluded_socket_ids"]
        assert detail["type"] == "excluded_socket_ids_type"
        assert "pairs" not in body

        # The same batch with the field removed processes normally.
        del payload["excluded_socket_ids"]
        legacy = post(payload)
        assert legacy.status_code == 200
        assert legacy.json()["status"] == "PASS"
        assert b"excluded_socket_ids" not in legacy.content

    def test_empty_exclusion_array_is_declared_but_excludes_nothing(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = []
        # 4 vs 2 -> COUNT_MISMATCH legacy body, and a declared-but-empty
        # success response elsewhere would echo [].
        body = post(payload).json()
        assert body["status"] == "COUNT_MISMATCH"
        assert body["socket_count"] == 4
        assert "excluded_socket_ids" not in body

        payload["detections"].append({"id": "D3", "x": 20, "y": 0})
        payload["detections"].append({"id": "D4", "x": 30, "y": 0})
        body = post(payload).json()
        assert body["status"] == "PASS"
        assert body["excluded_socket_ids"] == []

    def test_too_many_excluded_ids_rejected(self):
        payload = self.four_socket_payload()
        payload["excluded_socket_ids"] = ["S3"] * 81
        response = post(payload)
        assert response.status_code == 400
        details = response.json()["error"]["details"]
        assert details[0]["loc"] == ["excluded_socket_ids"]

    def test_legacy_request_without_field_is_byte_identical(self):
        # Requests that never carry excluded_socket_ids must keep producing
        # exactly the legacy bytes for both success and business failures.
        pass_payload = {
            "batch_id": "BATCH-LEGACY",
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
        pass_bytes = post(pass_payload).content
        assert b"excluded_socket_ids" not in pass_bytes
        # Repeated uploads (and array reorders) stay byte-identical.
        shuffled = copy.deepcopy(pass_payload)
        shuffled["sockets"] = list(reversed(shuffled["sockets"]))
        assert post(shuffled).content == pass_bytes

        mismatch_payload = {**pass_payload, "detections": pass_payload["detections"][:1]}
        mismatch_bytes = post(mismatch_payload).content
        assert b"excluded_socket_ids" not in mismatch_bytes
        assert b'"socket_count":2' in mismatch_bytes
        assert b'"detection_count":1' in mismatch_bytes


class TestHealth:
    def test_healthz(self):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
