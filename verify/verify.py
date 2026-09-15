"""One-shot acceptance checks against a running Chip Tray Inspection API.

Run inside Docker Compose via the ``verify`` service, or directly:

    API_BASE_URL=http://localhost:8000 python -m verify.verify

Exits with code 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
ENDPOINT = f"{BASE_URL}/api/v1/inspect"
HEALTH_URL = f"{BASE_URL}/healthz"

_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        _failures.append(name)


def post(client: httpx.Client, payload=None, raw: bytes | None = None) -> httpx.Response:
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    return client.post(
        ENDPOINT,
        files={"file": ("payload.json", data, "application/json")},
        timeout=10,
    )


def wait_for_api(client: httpx.Client, timeout_s: int = 60) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if client.get(HEALTH_URL, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def main() -> int:
    with httpx.Client() as client:
        if not wait_for_api(client):
            print(f"API at {BASE_URL} did not become healthy in time")
            return 1

        # 1. Optimal matching beats upload-order greedy pairing.
        payload = {
            "batch_id": "VERIFY-001",
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
        r = post(client, payload)
        body = r.json()
        check("pass.status", r.status_code == 200 and body.get("status") == "PASS", str(body))
        check(
            "pass.optimal_cost",
            body.get("min_total_cost") == 1,
            f"expected min_total_cost=1, got {body.get('min_total_cost')}",
        )
        check(
            "pass.pairs",
            body.get("pairs")
            == [
                {"socket_id": "S1", "detection_id": "D2"},
                {"socket_id": "S2", "detection_id": "D1"},
            ],
            str(body.get("pairs")),
        )

        # 2. Reordering the arrays must produce a byte-identical response.
        shuffled = {
            "batch_id": "VERIFY-001",
            "tolerance": 10,
            "sockets": list(reversed(payload["sockets"])),
            "detections": list(reversed(payload["detections"])),
        }
        r2 = post(client, shuffled)
        check("determinism.byte_identical_on_reorder", r2.content == r.content)

        # 3. Tie-break: equal-cost optima -> lexicographically smallest
        #    detection id sequence in socket-id order.
        tie = {
            "batch_id": "VERIFY-TIE",
            "tolerance": 10,
            "sockets": [
                {"id": "S2", "x": 2, "y": 0},
                {"id": "S1", "x": 0, "y": 0},
            ],
            "detections": [
                {"id": "DB", "x": 1, "y": 0},
                {"id": "DA", "x": 1, "y": 0},
            ],
        }
        body = post(client, tie).json()
        check(
            "tie_break.lexicographic",
            body.get("pairs")
            == [
                {"socket_id": "S1", "detection_id": "DA"},
                {"socket_id": "S2", "detection_id": "DB"},
            ],
            str(body.get("pairs")),
        )

        # 4. Tolerance boundary: distance == t pairs, distance == t + 1 fails.
        boundary_ok = {
            "batch_id": "VERIFY-B1",
            "tolerance": 7,
            "sockets": [{"id": "S1", "x": 0, "y": 0}],
            "detections": [{"id": "D1", "x": 3, "y": 4}],
        }
        body = post(client, boundary_ok).json()
        check(
            "tolerance.distance_equal_to_t_passes",
            body.get("status") == "PASS" and body.get("min_total_cost") == 7,
            str(body),
        )
        boundary_bad = dict(boundary_ok, batch_id="VERIFY-B2", tolerance=6)
        body = post(client, boundary_bad).json()
        check(
            "tolerance.distance_above_t_fails",
            body.get("status") == "POSITION_MISMATCH" and "pairs" not in body,
            str(body),
        )

        # 5. COUNT_MISMATCH.
        mismatch = {
            "batch_id": "VERIFY-C",
            "tolerance": 5,
            "sockets": [{"id": "S1", "x": 0, "y": 0}],
            "detections": [],
        }
        r = post(client, mismatch)
        body = r.json()
        check(
            "count_mismatch",
            r.status_code == 200
            and body.get("status") == "COUNT_MISMATCH"
            and "pairs" not in body,
            str(body),
        )

        # 6. Validation errors reject the whole request with a clear error.
        def expect_error(name, mutated=None, raw=None, status=400, code="VALIDATION_ERROR"):
            r = post(client, mutated, raw=raw)
            try:
                body = r.json()
            except ValueError:
                body = {}
            check(
                name,
                r.status_code == status and body.get("error", {}).get("code") == code,
                f"status={r.status_code} body={body}",
            )

        dup = {
            "batch_id": "VERIFY-E1",
            "tolerance": 5,
            "sockets": [{"id": "S1", "x": 0, "y": 0}, {"id": "S1", "x": 1, "y": 1}],
            "detections": [{"id": "D1", "x": 0, "y": 0}, {"id": "D2", "x": 1, "y": 1}],
        }
        expect_error("error.duplicate_ids", mutated=dup)

        non_int = {
            "batch_id": "VERIFY-E2",
            "tolerance": 5,
            "sockets": [{"id": "S1", "x": 0.5, "y": 0}],
            "detections": [{"id": "D1", "x": 0, "y": 0}],
        }
        expect_error("error.non_integer_coordinate", mutated=non_int)

        out_of_range = {
            "batch_id": "VERIFY-E3",
            "tolerance": 5,
            "sockets": [{"id": "S1", "x": 10001, "y": 0}],
            "detections": [{"id": "D1", "x": 0, "y": 0}],
        }
        expect_error("error.coordinate_out_of_range", mutated=out_of_range)

        unknown = {
            "batch_id": "VERIFY-E4",
            "tolerance": 5,
            "sockets": [],
            "detections": [],
            "unexpected": True,
        }
        expect_error("error.unknown_field", mutated=unknown)

        expect_error("error.invalid_json", raw=b'{"batch_id": ', code="INVALID_JSON")

        oversize = {
            "batch_id": "VERIFY-E5",
            "tolerance": 0,
            "sockets": [],
            "detections": [],
            "padding": "A" * (1024 * 1024),
        }
        expect_error(
            "error.file_too_large",
            mutated=oversize,
            status=413,
            code="FILE_TOO_LARGE",
        )

    if _failures:
        print(f"\n{len(_failures)} acceptance check(s) FAILED: {', '.join(_failures)}")
        return 1
    print("\nAll acceptance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
