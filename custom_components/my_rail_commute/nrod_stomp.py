"""Shared Network Rail Open Data (NROD) STOMP feed manager.

Bridges stomp.py's thread/callback-based STOMP client into Home Assistant's
asyncio event loop. There must only ever be one connection per NROD account
(the feed provider's docs warn against concurrent connections), so this
manager is a single shared, ref-counted resource used across every config
entry that opts into Recent Train Times.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import stomp

from .const import (
    NROD_CONNECT_SOCKET_TIMEOUT,
    NROD_CONNECT_TIMEOUT,
    NROD_RECONNECT_BACKOFF_FACTOR,
    NROD_RECONNECT_INITIAL_DELAY,
    NROD_RECONNECT_MAX_DELAY,
    NROD_STOMP_HOST,
    NROD_STOMP_SSL_PORT,
    NROD_STOMP_TOPIC,
)

_LOGGER = logging.getLogger(__name__)

EVENT_ARRIVAL = "ARRIVAL"
EVENT_DEPARTURE = "DEPARTURE"
EVENT_PASS = "PASS"

_MOVEMENT_MSG_TYPE = "0003"
_CANCELLATION_MSG_TYPE = "0002"


@dataclass
class MovementEvent:
    """A single train movement report (arrival/departure/pass) at a location."""

    train_id: str
    service_date: str  # YYYY-MM-DD, derived from the event's own timestamp
    stanox: str
    event_type: str  # ARRIVAL / DEPARTURE / PASS
    planned_time: str | None
    actual_time: str | None
    platform: str | None
    toc: str | None
    variation_status: str | None
    delay_minutes: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class CancellationEvent:
    """A train cancellation report."""

    train_id: str
    service_date: str
    stanox: str | None
    reason_code: str | None
    cancelled_at_origin: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


Callback = Callable[["MovementEvent | CancellationEvent"], "Awaitable[None] | None"]


def _epoch_ms_to_iso(value: Any) -> str | None:
    """Convert an NROD epoch-milliseconds timestamp string to an ISO datetime string."""
    if not value:
        return None
    try:
        return dt_util.utc_from_timestamp(int(value) / 1000).isoformat()
    except (TypeError, ValueError):
        return None


def _date_from_iso(value: str | None) -> str | None:
    """Extract the YYYY-MM-DD date component from an ISO datetime string."""
    if not value:
        return None
    return value[:10]


def parse_stomp_frame(body: str) -> list[MovementEvent | CancellationEvent]:
    """Parse a TRAIN_MVT_ALL_TOC STOMP frame body into typed events.

    The feed delivers a JSON array of records shaped like
    {"header": {"msg_type": "0003"}, "body": {...}}. Unrecognised message
    types and malformed records are skipped rather than raising, since the
    feed is high-volume and continuous — a single bad record must never take
    down the connection.
    """
    events: list[MovementEvent | CancellationEvent] = []

    try:
        records = json.loads(body)
    except (ValueError, TypeError):
        _LOGGER.debug("Discarding malformed NROD STOMP frame (invalid JSON)")
        return events

    if not isinstance(records, list):
        return events

    for record in records:
        try:
            event = _parse_record(record)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Discarding malformed NROD movement record: %s", err)
            continue
        if event is not None:
            events.append(event)

    return events


def _parse_record(record: dict[str, Any]) -> MovementEvent | CancellationEvent | None:
    """Parse a single record's header/body into a typed event, if recognised."""
    if not isinstance(record, dict):
        return None

    header = record.get("header", {})
    msg_type = header.get("msg_type")
    body = record.get("body", {})

    if not isinstance(body, dict):
        return None

    if msg_type == _MOVEMENT_MSG_TYPE:
        return _parse_movement(body)
    if msg_type == _CANCELLATION_MSG_TYPE:
        return _parse_cancellation(body)
    return None


def _parse_movement(body: dict[str, Any]) -> MovementEvent | None:
    """Parse a TRUST movement message body into a MovementEvent."""
    event_type = str(body.get("event_type") or "").upper()
    if event_type not in (EVENT_ARRIVAL, EVENT_DEPARTURE, EVENT_PASS):
        return None

    train_id = body.get("train_id")
    stanox = body.get("loc_stanox")
    if not train_id or not stanox:
        return None

    planned_time = _epoch_ms_to_iso(body.get("planned_timestamp") or body.get("gbtt_timestamp"))
    actual_time = _epoch_ms_to_iso(body.get("actual_timestamp"))
    service_date = _date_from_iso(planned_time or actual_time)
    if not service_date:
        return None

    variation_status = body.get("variation_status")
    delay_minutes = 0
    try:
        raw_variation = int(body.get("timetable_variation") or 0)
        delay_minutes = -raw_variation if variation_status == "EARLY" else raw_variation
    except (TypeError, ValueError):
        delay_minutes = 0

    return MovementEvent(
        train_id=str(train_id),
        service_date=service_date,
        stanox=str(stanox),
        event_type=event_type,
        planned_time=planned_time,
        actual_time=actual_time,
        platform=body.get("platform") or None,
        toc=body.get("toc_id") or None,
        variation_status=variation_status,
        delay_minutes=delay_minutes,
        raw=body,
    )


def _parse_cancellation(body: dict[str, Any]) -> CancellationEvent | None:
    """Parse a TRUST cancellation message body into a CancellationEvent."""
    train_info = body.get("train_info", body)
    train_id = train_info.get("train_id")
    if not train_id:
        return None

    dep_time = _epoch_ms_to_iso(train_info.get("dep_timestamp"))
    canx_time = _epoch_ms_to_iso(train_info.get("canx_timestamp"))
    service_date = _date_from_iso(dep_time or canx_time)
    if not service_date:
        return None

    return CancellationEvent(
        train_id=str(train_id),
        service_date=service_date,
        stanox=train_info.get("loc_stanox") or None,
        reason_code=train_info.get("canx_reason_code") or None,
        cancelled_at_origin=train_info.get("canx_type") == "AT ORIGIN",
        raw=train_info,
    )


class _FeedListener(stomp.ConnectionListener):
    """Bridges stomp.py's synchronous callbacks to the feed manager."""

    def __init__(self, manager: NrodFeedManager) -> None:
        self._manager = manager

    def on_connected(self, frame: Any) -> None:
        self._manager._on_connected()

    def on_message(self, frame: Any) -> None:
        self._manager._on_message(frame)

    def on_error(self, frame: Any) -> None:
        self._manager._on_error(frame)

    def on_disconnected(self) -> None:
        self._manager._on_disconnected()


class NrodFeedManager:
    """Manages a single shared STOMP connection to the NROD movement feed."""

    def __init__(self, hass: HomeAssistant, username: str, password: str) -> None:
        """Initialize the feed manager.

        Args:
            hass: Home Assistant instance
            username: NROD account username
            password: NROD account password
        """
        self._hass = hass
        self._username = username
        self._password = password
        self._connection: stomp.Connection | None = None
        self._subscribers: dict[str, list[Callback]] = {}
        self._entry_stanox: dict[str, set[str]] = {}
        self._entry_callback: dict[str, Callback] = {}
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay: float = NROD_RECONNECT_INITIAL_DELAY
        self._stopped = True
        self._connect_lock = asyncio.Lock()
        self._connect_generation = 0

        self.connected = False
        self.last_message_at: datetime | None = None

    @property
    def has_subscribers(self) -> bool:
        """Return True if any config entry is still using this feed."""
        return bool(self._entry_callback)

    async def async_acquire(
        self, entry_id: str, subscriber: Callback, stanox_codes: set[str]
    ) -> None:
        """Register a config entry's interest in a set of STANOX codes.

        Establishes the shared STOMP connection if this is the first
        subscriber overall. Serialized by _connect_lock so that config
        entries set up concurrently during HA bootstrap can't each race
        into opening their own connection — NROD only allows a single
        connection per account, so two simultaneous logins just knock
        each other off the feed.

        A failed initial connect does not raise: it falls back to the same
        backoff reconnect loop used for post-connect drops, so a transient
        NROD outage during HA bootstrap doesn't permanently strand this
        entry's subscription (unlogged and unregistered from the shared
        manager, since it registers itself above before ever attempting to
        connect).
        """
        self._entry_callback[entry_id] = subscriber
        self._entry_stanox[entry_id] = set(stanox_codes)
        for stanox in stanox_codes:
            callbacks = self._subscribers.setdefault(stanox, [])
            if subscriber not in callbacks:
                callbacks.append(subscriber)

        async with self._connect_lock:
            if self._connection is None and not self.connected:
                try:
                    await self._async_connect()
                except Exception:  # noqa: BLE001 - third-party client raises assorted errors
                    _LOGGER.warning(
                        "Initial connect to Network Rail Open Data feed failed; "
                        "scheduling reconnect",
                        exc_info=True,
                    )
                    self._schedule_reconnect()

    async def async_release(self, entry_id: str) -> None:
        """Remove a config entry's subscription.

        Tears down the shared STOMP connection once no entries remain.
        """
        subscriber = self._entry_callback.pop(entry_id, None)
        stanox_codes = self._entry_stanox.pop(entry_id, set())

        for stanox in stanox_codes:
            callbacks = self._subscribers.get(stanox, [])
            if subscriber in callbacks:
                callbacks.remove(subscriber)
            if not callbacks:
                self._subscribers.pop(stanox, None)

        if not self.has_subscribers:
            await self._async_disconnect()

    async def _async_connect(self) -> None:
        """Establish the shared STOMP connection (off the event loop).

        Bounded by NROD_CONNECT_TIMEOUT so an unreachable or slow-to-respond
        NROD endpoint can never block Home Assistant's bootstrap. A timeout
        is raised like any other connect failure, so callers (including the
        reconnect backoff loop) handle it the same way.

        Cancelling the asyncio.wait_for on timeout does not stop the blocking
        call running in the executor thread — Python threads can't be forced
        to stop. That leftover thread can still finish the STOMP login after
        we've already given up, and since NROD allows only one connection per
        account, a stray late login is enough to knock a subsequent, genuine
        attempt off the feed. _connect_generation lets an abandoned attempt
        recognise itself when it finally finishes and disconnect instead of
        being treated as live.
        """
        self._stopped = False
        self._connect_generation += 1
        generation = self._connect_generation
        try:
            await asyncio.wait_for(
                self._hass.async_add_executor_job(self._connect_sync, generation),
                timeout=NROD_CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            _LOGGER.warning(
                "Timed out connecting to Network Rail Open Data feed after %s seconds",
                NROD_CONNECT_TIMEOUT,
            )
            raise ConnectionError("Timed out connecting to NROD feed") from err

    def _connect_sync(self, generation: int) -> None:
        """Blocking STOMP connect/subscribe, run in an executor thread."""
        connection = stomp.Connection(
            host_and_ports=[(NROD_STOMP_HOST, NROD_STOMP_SSL_PORT)],
            keepalive=True,
            timeout=NROD_CONNECT_SOCKET_TIMEOUT,
            # stomp.py retries failed TCP connects internally (default: 3
            # attempts). Left at its default, that inner loop can take longer
            # than NROD_CONNECT_TIMEOUT to give up, so our own asyncio.wait_for
            # abandons the executor thread while stomp.py is still retrying in
            # the background - leaving a stale thread that keeps hammering
            # NROD alongside the fresh attempt our outer reconnect loop then
            # starts. We already own retry/backoff at the manager level (with
            # generation tracking to disown stale attempts), so make stomp.py
            # try exactly once and bail out to us instead of retrying itself.
            reconnect_attempts_max=1,
        )
        connection.set_ssl(for_hosts=[(NROD_STOMP_HOST, NROD_STOMP_SSL_PORT)])
        connection.set_listener("nrod", _FeedListener(self))
        connection.connect(
            username=self._username,
            passcode=self._password,
            wait=True,
            headers={"client-id": self._username},
        )
        # NROD recommends a durable subscription, which keeps messages published
        # during a disconnect queued for 5 minutes rather than dropping them. On
        # the ActiveMQ broker NROD runs, durability needs client-id (set above on
        # CONNECT) paired with activemq.subscriptionName here - the "id" header
        # alone only scopes acking/unsubscribing within this session and isn't
        # enough on its own. The subscription name must stay stable across
        # reconnects for the broker to recognise it as the same durable sub.
        connection.subscribe(
            destination=NROD_STOMP_TOPIC,
            id="my-rail-commute",
            ack="auto",
            headers={"activemq.subscriptionName": self._username},
        )

        if generation != self._connect_generation:
            _LOGGER.warning(
                "NROD login completed after the caller had already given up on it; "
                "disconnecting the stray session instead of leaving it open"
            )
            try:
                connection.disconnect()
            except Exception:  # noqa: BLE001 - best-effort cleanup of a third-party client
                _LOGGER.debug("Error disconnecting abandoned NROD connection", exc_info=True)
            return

        self._connection = connection

    async def _async_disconnect(self) -> None:
        """Tear down the shared STOMP connection."""
        self._stopped = True
        self._connect_generation += 1
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        connection = self._connection
        self._connection = None
        self.connected = False

        if connection is not None:
            await self._hass.async_add_executor_job(self._disconnect_sync, connection)

    @staticmethod
    def _disconnect_sync(connection: stomp.Connection) -> None:
        """Blocking STOMP disconnect, run in an executor thread."""
        try:
            connection.disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup of a third-party client
            _LOGGER.debug("Error disconnecting NROD STOMP connection", exc_info=True)

    # --- callbacks invoked from stomp.py's background thread ---

    def _on_connected(self) -> None:
        self._hass.loop.call_soon_threadsafe(self._handle_connected)

    def _handle_connected(self) -> None:
        self.connected = True
        self._reconnect_delay = NROD_RECONNECT_INITIAL_DELAY
        _LOGGER.info("Connected to Network Rail Open Data feed")

    def _on_disconnected(self) -> None:
        self._hass.loop.call_soon_threadsafe(self._handle_disconnected)

    def _handle_disconnected(self) -> None:
        self.connected = False
        if self._stopped:
            return
        _LOGGER.warning("Disconnected from Network Rail Open Data feed; scheduling reconnect")
        self._schedule_reconnect()

    def _on_error(self, frame: Any) -> None:
        _LOGGER.debug("NROD STOMP error frame: %s", getattr(frame, "body", frame))

    def _on_message(self, frame: Any) -> None:
        body = getattr(frame, "body", frame)
        self._hass.loop.call_soon_threadsafe(self._handle_frame, body)

    def _handle_frame(self, body: str) -> None:
        self.last_message_at = dt_util.utcnow()
        for event in parse_stomp_frame(body):
            for callback in list(self._subscribers.get(event.stanox, [])):
                result = callback(event)
                if asyncio.iscoroutine(result):
                    self._hass.async_create_task(result)

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self._hass.async_create_background_task(
            self._async_reconnect_loop(), "nrod_feed_reconnect"
        )

    async def _async_reconnect_loop(self) -> None:
        """Retry connecting with exponential backoff until it succeeds or we're stopped."""
        while not self._stopped and self.has_subscribers and not self.connected:
            _LOGGER.debug("Reconnecting to NROD feed in %s seconds", self._reconnect_delay)
            await asyncio.sleep(self._reconnect_delay)
            if self._stopped or not self.has_subscribers:
                return
            try:
                async with self._connect_lock:
                    if self.connected:
                        return
                    await self._async_connect()
                return
            except Exception:  # noqa: BLE001 - third-party client raises assorted errors
                _LOGGER.warning("NROD feed reconnect attempt failed", exc_info=True)
                self._reconnect_delay = min(
                    self._reconnect_delay * NROD_RECONNECT_BACKOFF_FACTOR,
                    NROD_RECONNECT_MAX_DELAY,
                )
