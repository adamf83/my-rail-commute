"""Confirming a forecast against the actual arrival at the destination.

A service ID is specific to the board that issued it and only works while the
train is on that board, so the departure-board ID is useless once the train
has left. Instead the destination's arrivals board is queried for the same
train, and the details of that arrival-side ID report the actual arrival
(``ata``, see :mod:`parsing`).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from ..api import NationalRailAPI, NationalRailAPIError
from .models import ClaimRecord
from .parsing import ArrivalObservation, observation_from_board_details

_LOGGER = logging.getLogger(__name__)

_ID_PREFIX_RE = re.compile(r"^\d+")


class ConfirmationSource(Protocol):
    """Something that can report a journey's actual arrival."""

    async def async_fetch(self, record: ClaimRecord) -> ArrivalObservation | None:
        """Return what is known about the arrival, or None if nothing yet.

        Raises:
            NationalRailAPIError: if the underlying API call fails.
        """


def id_prefix(service_id: str | None) -> str:
    """Return the leading digits of a service ID, else an empty string.

    The same train carries the same numeric prefix on every board (for example
    9494208WHYTELF at Whyteleafe and 9494208LNDNBDC at London Bridge); the
    rest names the board's station. This is observed, not documented, so it is
    a matching aid with a fallback rather than a guarantee.
    """
    match = _ID_PREFIX_RE.match(service_id or "")
    return match[0] if match else ""


def _first_previous_point(service: dict[str, Any]) -> dict[str, Any]:
    groups = service.get("previousCallingPoints")
    if isinstance(groups, dict):
        groups = [groups]
    if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
        return {}
    points = groups[0].get("callingPoint")
    if isinstance(points, dict):
        points = [points]
    if isinstance(points, list) and points and isinstance(points[0], dict):
        return points[0]
    return {}


def match_arrival_service(
    services: list[dict[str, Any]], record: ClaimRecord
) -> dict[str, Any] | None:
    """Find the arrivals-board entry for the train a record is about.

    Matches on the shared numeric service ID prefix first, then falls back to
    the train's origin and scheduled departure from its first calling point.
    """
    prefix = id_prefix(record.service_id)
    if prefix:
        for service in services:
            if id_prefix(service.get("serviceID")) == prefix:
                return service
    for service in services:
        point = _first_previous_point(service)
        if (
            str(point.get("crs", "")).strip().upper() == record.origin.upper()
            and point.get("st") == record.scheduled_departure
        ):
            return service
    return None


def details_ids(service: dict[str, Any]) -> list[str]:
    """Return the IDs worth trying for a details lookup, best first.

    The URL-safe form is preferred because the raw ID can contain characters
    that break a URL path.
    """
    candidates = [service.get("serviceIdUrlSafe"), service.get("serviceID")]
    ids: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str) and candidate and candidate not in ids:
            ids.append(candidate)
    return ids


class DestinationBoardSource:
    """Reads the actual arrival from the destination's arrivals board."""

    def __init__(self, api: NationalRailAPI) -> None:
        """Create a source using an API client."""
        self._api = api

    async def async_fetch(self, record: ClaimRecord) -> ArrivalObservation | None:
        """Look the train up at its destination and read its actual arrival."""
        services = await self._api.get_arrival_board(record.destination, record.origin)
        service = match_arrival_service(services, record)
        if service is None:
            _LOGGER.debug(
                "Journey %s not on the %s arrivals board",
                record.key,
                record.destination,
            )
            return None
        if service.get("isCancelled") is True:
            return ArrivalObservation(is_cancelled=True)

        for service_id in details_ids(service):
            try:
                details = await self._api.get_service_details(service_id)
            except NationalRailAPIError as err:
                _LOGGER.debug("Service details unavailable for %s: %s", record.key, err)
                continue
            return observation_from_board_details(details)
        return None
