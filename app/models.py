"""Pydantic schemas for the uploaded inspection payload.

Validation rules (any violation rejects the whole request):
* coordinates are strict integers in [0, 10000] — floats (even ``1.0``),
  numeric strings and booleans are rejected;
* ``tolerance`` is a strict integer in [0, 500];
* each point id is a non-empty string, unique across the whole batch
  (both lists combined);
* at most 80 points per category;
* unknown fields are forbidden everywhere.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

Coordinate = Annotated[int, Field(strict=True, ge=0, le=10000)]
Tolerance = Annotated[int, Field(strict=True, ge=0, le=500)]
PointId = Annotated[str, Field(min_length=1, max_length=64)]

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

    @model_validator(mode="after")
    def _ids_unique_within_batch(self) -> "InspectionPayload":
        seen: set[str] = set()
        for point in (*self.sockets, *self.detections):
            if point.id in seen:
                raise ValueError(f"duplicate point id in batch: {point.id!r}")
            seen.add(point.id)
        return self
