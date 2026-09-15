"""FastAPI application exposing the chip tray inspection endpoint."""

from __future__ import annotations

import json
from typing import Any, Union

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .matching import solve_matching
from .models import InspectionPayload

MAX_FILE_BYTES = 1024 * 1024  # 1 MiB hard limit for the uploaded JSON file

app = FastAPI(
    title="Chip Tray Inspection API",
    version="1.0.0",
    description=(
        "Matches expected tray sockets with detected chip centers using "
        "Manhattan-distance optimal assignment."
    ),
)


def _error(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def _validation_details(
    exc: Union[ValidationError, RequestValidationError],
) -> list[dict[str, Any]]:
    return [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "type": str(err.get("type", "")),
            "msg": str(err.get("msg", "")),
        }
        for err in exc.errors()
    ]


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error(
        400,
        "VALIDATION_ERROR",
        "request validation failed",
        _validation_details(exc),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error(500, "INTERNAL_ERROR", "unexpected internal error")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/inspect")
async def inspect(file: UploadFile = File(...)) -> Any:
    """Inspect one uploaded JSON batch file (multipart/form-data, field ``file``)."""
    raw = await file.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        return _error(
            413,
            "FILE_TOO_LARGE",
            "uploaded file exceeds the 1 MiB (1048576 bytes) size limit",
        )

    try:
        payload_obj = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return _error(
            400,
            "INVALID_JSON",
            "uploaded file is not valid UTF-8 encoded JSON",
        )

    try:
        payload = InspectionPayload.model_validate(payload_obj)
    except ValidationError as exc:
        return _error(
            400,
            "VALIDATION_ERROR",
            "payload validation failed",
            _validation_details(exc),
        )

    sockets = payload.sockets
    detections = payload.detections

    # Sockets temporarily taken out of service are removed (by id, sorted)
    # before counting and matching. The field is omitted entirely on every
    # failure so that no partial pairing or context is ever leaked, and when
    # the request did not declare the field the responses stay byte-identical
    # to the legacy contract.
    excluded_ids = payload.excluded_socket_ids
    if excluded_ids is not None:
        excluded_sorted = sorted(excluded_ids)
        excluded_set = set(excluded_sorted)
        sockets = [point for point in sockets if point.id not in excluded_set]

    if len(sockets) != len(detections):
        return {
            "status": "COUNT_MISMATCH",
            "batch_id": payload.batch_id,
            "socket_count": len(sockets),
            "detection_count": len(detections),
        }

    result = solve_matching(sockets, detections, payload.tolerance)
    if result is None:
        return {
            "status": "POSITION_MISMATCH",
            "batch_id": payload.batch_id,
        }

    total_cost, pairs = result
    response = {
        "status": "PASS",
        "batch_id": payload.batch_id,
        "min_total_cost": total_cost,
        "pairs": [
            {"socket_id": socket_id, "detection_id": detection_id}
            for socket_id, detection_id in pairs
        ],
    }
    if excluded_ids is not None:
        # Echoed after pairs, in sorted order (upload order never matters).
        response["excluded_socket_ids"] = excluded_sorted
    return response
