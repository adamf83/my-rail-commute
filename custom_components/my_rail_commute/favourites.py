"""Favourite trains: parsing, live state and persistence."""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    FAVOURITE_STATE_CANCELLED,
    FAVOURITE_STATE_DEPARTED,
    FAVOURITE_STATE_NOT_RUNNING,
    FAVOURITE_STATE_SCHEDULED,
    FAVOURITE_STATE_UPCOMING,
    FAVOURITES_STORAGE_VERSION,
    MAX_FAVOURITE_TRAINS,
)

_LOGGER = logging.getLogger(__name__)

_SPLIT_RE: Final = re.compile(r"[,\s;]+")
_TIME_RE: Final = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_favourite_trains(text: str | None) -> list[str]:
    """Parse "08:15, 17:42" into a sorted, de-duplicated list of HH:MM times.

    Separators may be commas, spaces or semicolons. An empty value is valid
    and means no favourites.

    Raises:
        ValueError: if an entry is not a valid 24-hour HH:MM time, or there
            are more than MAX_FAVOURITE_TRAINS.
    """
    times: set[str] = set()
    for part in _SPLIT_RE.split((text or "").strip()):
        if not part:
            continue
        match = _TIME_RE.match(part)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            raise ValueError(f"'{part}' is not a valid HH:MM time")
        times.add(f"{int(match.group(1)):02d}:{match.group(2)}")
    if len(times) > MAX_FAVOURITE_TRAINS:
        raise ValueError(f"At most {MAX_FAVOURITE_TRAINS} favourite trains")
    return sorted(times)


def favourite_slug(departure: str) -> str:
    """Return a unique-id friendly form of an HH:MM time ("0815")."""
    return departure.replace(":", "")


class FavouriteTrainsStore:
    """Persists each favourite's last seen result for the current day.

    The departure board drops a train as it leaves, so "departed" can only be
    known from what was seen earlier. Results reset on the first update of a
    new day.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass, FAVOURITES_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_favourites"
        )
        self._day: str | None = None
        self._results: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load persisted results, discarding any from a previous day."""
        raw = await self._store.async_load()
        if raw and raw.get("day") == dt_util.now().date().isoformat():
            self._day = raw["day"]
            self._results = raw.get("results", {})
        else:
            self._day = None
            self._results = {}

    def _roll_day(self) -> bool:
        today = dt_util.now().date().isoformat()
        if self._day != today:
            self._day = today
            self._results = {}
            return True
        return False

    async def async_update(
        self, results: dict[str, dict[str, Any]], seen: set[str]
    ) -> None:
        """Remember the latest result of each favourite seen on the board."""
        changed = self._roll_day()
        for departure in seen:
            if self._results.get(departure) != results[departure]:
                self._results[departure] = results[departure]
                changed = True
        if changed:
            await self._store.async_save({"day": self._day, "results": self._results})

    def get(self, departure: str) -> dict[str, Any] | None:
        """Return today's last seen result for a favourite, if any."""
        if self._day != dt_util.now().date().isoformat():
            return None
        return self._results.get(departure)


def build_favourite_states(
    favourites: list[str],
    services: list[dict[str, Any]],
    store: FavouriteTrainsStore | None,
    now_minutes: int,
    time_window: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    """Work out each favourite's live state from the unfiltered board.

    Args:
        favourites: HH:MM scheduled departure times
        services: unfiltered first-leg board services
        store: persisted last seen results (may be None)
        now_minutes: minutes since midnight, now
        time_window: board window in minutes

    Returns:
        (ordered favourite dicts, results to persist keyed by time, times seen)
    """
    by_time = {s.get("scheduled_departure"): s for s in services}
    out: list[dict[str, Any]] = []
    persist: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()

    for departure in favourites:
        hours, minutes = departure.split(":")
        fav_minutes = int(hours) * 60 + int(minutes)
        service = by_time.get(departure)
        entry: dict[str, Any] = {"scheduled_departure": departure}

        if service is not None:
            entry.update(
                {
                    "expected_departure": service.get("expected_departure"),
                    "platform": service.get("platform"),
                    "operator": service.get("operator"),
                    "delay_minutes": service.get("delay_minutes", 0),
                    "is_cancelled": service.get("is_cancelled", False),
                    "destination": service.get("destination"),
                    "service_id": service.get("service_id"),
                    "status": service.get("status"),
                    "service_type": service.get("service_type", "train"),
                    "scheduled_arrival": service.get("scheduled_arrival"),
                    "estimated_arrival": service.get("estimated_arrival"),
                    "calling_points": service.get("calling_points", []),
                    "delay_reason": service.get("delay_reason"),
                    "cancellation_reason": service.get("cancellation_reason"),
                }
            )
            entry["state"] = (
                FAVOURITE_STATE_CANCELLED
                if service.get("is_cancelled")
                else FAVOURITE_STATE_UPCOMING
            )
            persist[departure] = dict(entry)
            seen.add(departure)
        else:
            previous = store.get(departure) if store is not None else None
            delta = fav_minutes - now_minutes
            if previous is not None and delta <= 0:
                # Cancelled trains never left, so they stay cancelled
                entry.update(previous)
                entry["state"] = (
                    FAVOURITE_STATE_CANCELLED
                    if previous.get("is_cancelled")
                    else FAVOURITE_STATE_DEPARTED
                )
            elif delta > time_window:
                entry["state"] = FAVOURITE_STATE_SCHEDULED
            else:
                entry["state"] = FAVOURITE_STATE_NOT_RUNNING
        out.append(entry)

    return out, persist, seen
