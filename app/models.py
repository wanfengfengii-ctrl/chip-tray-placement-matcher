"""Pydantic schemas for the uploaded inspection payload.

Validation rules (any violation rejects the whole request):
* coordinates are strict integers in [0, 10000] — floats (even ``1.0``),
  numeric strings and booleans are rejected;
* ``tolerance`` is a strict integer in [0, 500];
* each point id is a non-empty string, unique across the whole batch
  (both lists combined);
* at most 80 points per category;
* ``excluded_socket_ids`` is an optional list of socket ids temporarily
  taken out of service (line changeover / socket maintenance): it must not
  contain duplicates and every id must reference a socket of this batch;
* unknown fields are forbidden everywhere.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

Coordinate = Annotated[int, Field(strict=True, ge=0, le=10000)]
Tolerance = Annotated[int, Field(strict=True, ge=0, le=500)]
PointId = Annotated[str, Field(min_length=1, max_length=64)]
# Strict: numeric (int/float/bool) elements are rejected instead of being
# coerced to strings, because excluded_socket_ids is a string array.
ExcludedSocketId = Annotated[str, Field(strict=True, min_length=1, max_length=64)]

MAX_POINTS_PER_CATEGORY = 80


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PointId
    x: Coordinate
    y: Coordinate


class InspectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: Annotated[str, Field(min_length=1, max_length=128)]
    tolerance: Tolerance
    sockets: Annotated[list[Point], Field(max_length=MAX_POINTS_PER_CATEGORY)]
    detections: Annotated[list[Point], Field(max_length=MAX_POINTS_PER_CATEGORY)]
    # Optional declaration of sockets temporarily excluded from pairing
    # (line changeover / socket maintenance). ``None`` (field absent) keeps
    # the legacy request/response behaviour byte-for-byte.
    excluded_socket_ids: Optional[
        Annotated[list[ExcludedSocketId], Field(max_length=MAX_POINTS_PER_CATEGORY)]
    ] = None

    @model_validator(mode="after")
    def _ids_unique_within_batch(self) -> "InspectionPayload":
        seen: set[str] = set()
        for point in (*self.sockets, *self.detections):
            if point.id in seen:
                raise ValueError(f"duplicate point id in batch: {point.id!r}")
            seen.add(point.id)
        return self

    @field_validator("excluded_socket_ids")
    @classmethod
    def _validate_excluded_socket_ids(
        cls, value: Optional[list[str]], info: ValidationInfo
    ) -> Optional[list[str]]:
        if value is None:
            return None

        # Duplicates are checked first, then membership in this batch's
        # socket set. Either failure rejects the whole request and the error
        # is located at the excluded_socket_ids field itself.
        seen: set[str] = set()
        for socket_id in value:
            if socket_id in seen:
                raise PydanticCustomError(
                    "excluded_socket_ids_duplicate",
                    "excluded_socket_ids contains duplicate socket id "
                    "{socket_id!r}",
                    {"socket_id": socket_id},
                )
            seen.add(socket_id)

        # ``sockets`` is validated before this field (declaration order);
        # skip the membership check when it failed validation itself.
        sockets = info.data.get("sockets")
        if sockets is not None:
            socket_ids = {point.id for point in sockets}
            for socket_id in value:
                if socket_id not in socket_ids:
                    raise PydanticCustomError(
                        "excluded_socket_ids_unknown",
                        "excluded_socket_ids entry {socket_id!r} does not "
                        "reference a socket of this batch",
                        {"socket_id": socket_id},
                    )
        return value
