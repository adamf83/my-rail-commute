"""Persistent daily statistics storage for My Rail Commute integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STATS_RETENTION_DAYS, STORAGE_VERSION, STATUS_DELAYED

_LOGGER = logging.getLogger(__name__)


class CommuteStatisticsStore:
    """Manages persistent daily commute statistics using HA's Store."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_stats")
        self._data: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Load persisted data from storage."""
        raw = await self._store.async_load()
        if raw is None:
            self._data = {}
        else:
            self._data = raw.get("days", {})
        self._prune_old_entries()
        _LOGGER.debug("Loaded %d days of historical stats", len(self._data))

    async def async_record_observation(self, parsed_data: dict[str, Any]) -> None:
        """Accumulate today's observation from a coordinator update and persist."""
        if parsed_data.get("services_tracked", 0) == 0:
            _LOGGER.debug("Skipping stats recording: no services tracked in this update")
            return

        today_key = dt_util.now().date().isoformat()
        day = self._data.get(today_key, {
            "on_time_count": 0,
            "delayed_count": 0,
            "cancelled_count": 0,
            "total_observations": 0,
            "total_delay_minutes": 0,
        })

        on_time = parsed_data.get("on_time_count", 0)
        delayed = parsed_data.get("delayed_count", 0)
        cancelled = parsed_data.get("cancelled_count", 0)
        obs = on_time + delayed + cancelled

        total_delay = sum(
            s.get("delay_minutes", 0)
            for s in parsed_data.get("services", [])
            if s.get("status") == STATUS_DELAYED and not s.get("is_cancelled", False)
        )

        day["on_time_count"] += on_time
        day["delayed_count"] += delayed
        day["cancelled_count"] += cancelled
        day["total_observations"] += obs
        day["total_delay_minutes"] += total_delay

        total_obs = day["total_observations"]
        day["on_time_pct"] = round(day["on_time_count"] / total_obs * 100, 2) if total_obs > 0 else 0.0
        day["avg_delay_minutes"] = (
            round(day["total_delay_minutes"] / day["delayed_count"], 2)
            if day["delayed_count"] > 0
            else 0.0
        )

        self._data[today_key] = day
        self._prune_old_entries()
        await self._store.async_save({"version": STORAGE_VERSION, "days": self._data})
        _LOGGER.debug(
            "Recorded stats for %s: on_time=%d delayed=%d cancelled=%d",
            today_key, on_time, delayed, cancelled,
        )

    def get_today_stats(self) -> dict[str, Any]:
        """Return today's accumulated stats, or an empty dict if no data yet."""
        return self._data.get(dt_util.now().date().isoformat(), {})

    def get_rolling_stats(self, days: int) -> dict[str, Any]:
        """Return aggregated stats across the last `days` calendar days (today included)."""
        today = dt_util.now().date()
        window = [(today - timedelta(days=i)).isoformat() for i in range(days)]
        days_with_data = [d for d in window if d in self._data]

        if not days_with_data:
            return {"on_time_pct": None, "avg_delay_minutes": None, "days_with_data": 0}

        total_on_time = sum(self._data[d]["on_time_count"] for d in days_with_data)
        total_obs = sum(self._data[d]["total_observations"] for d in days_with_data)
        total_delayed = sum(self._data[d]["delayed_count"] for d in days_with_data)
        total_delay_min = sum(self._data[d]["total_delay_minutes"] for d in days_with_data)

        return {
            "on_time_pct": round(total_on_time / total_obs * 100, 1) if total_obs > 0 else None,
            "avg_delay_minutes": round(total_delay_min / total_delayed, 1) if total_delayed > 0 else None,
            "days_with_data": len(days_with_data),
        }

    def get_best_and_worst_days(self, days: int = 30) -> dict[str, Any]:
        """Return worst/best day (by on-time %) across the last `days` calendar days."""
        today = dt_util.now().date()
        window = [(today - timedelta(days=i)).isoformat() for i in range(days)]
        candidates = {d: self._data[d] for d in window if d in self._data and self._data[d].get("total_observations", 0) > 0}

        if not candidates:
            return {"worst_day": None, "best_day": None}

        worst = min(candidates, key=lambda d: candidates[d].get("on_time_pct", 100))
        best = max(candidates, key=lambda d: candidates[d].get("on_time_pct", 0))

        return {
            "worst_day": {
                "date": worst,
                "on_time_pct": candidates[worst].get("on_time_pct"),
                "avg_delay_minutes": candidates[worst].get("avg_delay_minutes"),
            },
            "best_day": {
                "date": best,
                "on_time_pct": candidates[best].get("on_time_pct"),
                "avg_delay_minutes": candidates[best].get("avg_delay_minutes"),
            },
        }

    def get_daily_breakdown(self, days: int = 30) -> list[dict[str, Any]]:
        """Return per-day stats for last N calendar days, oldest first."""
        today = dt_util.now().date()
        result = []
        for i in range(days - 1, -1, -1):
            date_str = (today - timedelta(days=i)).isoformat()
            day = self._data.get(date_str)
            result.append({
                "date": date_str,
                "on_time_pct": day.get("on_time_pct") if day else None,
                "avg_delay_minutes": day.get("avg_delay_minutes") if day else None,
                "total_observations": day.get("total_observations", 0) if day else 0,
            })
        return result

    def get_raw_data(self) -> dict[str, Any]:
        """Return a copy of all stored daily stats records."""
        return dict(self._data)

    def _prune_old_entries(self) -> None:
        """Remove entries older than STATS_RETENTION_DAYS."""
        cutoff = (dt_util.now().date() - timedelta(days=STATS_RETENTION_DAYS)).isoformat()
        stale = [key for key in self._data if key < cutoff]
        for key in stale:
            del self._data[key]
        if stale:
            _LOGGER.debug("Pruned %d stale stats entries (older than %s)", len(stale), cutoff)
