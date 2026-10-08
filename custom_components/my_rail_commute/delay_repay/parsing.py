"""Readers for the actual-arrival fields in LDBWS service-details responses.

Two sources can confirm an arrival (see issue #170):

* the destination board's own details: top-level ``ata`` / ``isCancelled``
* a downstream board's details: the destination's entry in
  ``previousCallingPoints`` (``at`` / ``isCancelled``)

Both report an actual as an ``HH:MM`` time or the text "On time"; that value
is returned unchanged for :func:`models.arrival_delay_minutes` to interpret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ArrivalObservation:
    """What a details response says about arrival at one location."""

    actual: str | None = None
    is_cancelled: bool = False

    @property
    def has_outcome(self) -> bool:
        """True once the arrival happened or the stop was cancelled."""
        return self.is_cancelled or self.actual is not None


def _text(value: Any) -> str | None:
    """Return a stripped non-empty string, else None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def observation_from_board_details(details: Any) -> ArrivalObservation:
    """Read the board station's own arrival from a service-details response.

    ``ata`` is only present once the train has arrived; before that the
    response carries ``eta`` instead and ``actual`` stays None.
    """
    if not isinstance(details, dict):
        return ArrivalObservation()
    return ArrivalObservation(
        actual=_text(details.get("ata")),
        is_cancelled=details.get("isCancelled") is True,
    )


def _first_calling_point_list(details: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the "through" train's previous calling points.

    The API gives a list of lists: the first is the through train and any
    others are joining or splitting portions, which are ignored here.
    """
    groups = details.get("previousCallingPoints")
    if isinstance(groups, dict):
        groups = [groups]
    if not isinstance(groups, list) or not groups:
        return []
    first = groups[0]
    points = first.get("callingPoint") if isinstance(first, dict) else None
    if isinstance(points, dict):
        points = [points]
    if not isinstance(points, list):
        return []
    return [p for p in points if isinstance(p, dict)]


def observation_from_calling_points(
    details: Any, destination_crs: str
) -> ArrivalObservation | None:
    """Read the destination's arrival from a downstream board's details.

    Returns None when the destination cannot be identified: not in the first
    calling-point list, or listed more than once (a circular route, where we
    cannot tell which visit is meant). A stop that is listed but not yet
    reached returns an observation with ``actual`` None.
    """
    if not isinstance(details, dict):
        return None
    crs = destination_crs.strip().upper()
    matches = [
        p
        for p in _first_calling_point_list(details)
        if str(p.get("crs", "")).strip().upper() == crs
    ]
    if len(matches) != 1:
        return None
    point = matches[0]
    return ArrivalObservation(
        actual=_text(point.get("at")),
        is_cancelled=point.get("isCancelled") is True,
    )
