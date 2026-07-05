"""CORPUS reference data (CRS <-> STANOX resolution) for the Network Rail Open Data feed."""
from __future__ import annotations

import gzip
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CORPUS_REFRESH_INTERVAL_DAYS,
    CORPUS_STORAGE_VERSION,
    DOMAIN,
    NROD_CORPUS_URL,
)

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30


class CorpusError(Exception):
    """Base exception for CORPUS reference data errors."""


class CorpusAuthenticationError(CorpusError):
    """NROD credentials were rejected."""


class CorpusUnavailableError(CorpusError):
    """CORPUS data could not be fetched and no cached copy is available."""


class CorpusReferenceStore:
    """Resolves CRS codes to STANOX codes using Network Rail's CORPUS reference data.

    CORPUS data is fetched once via HTTP (Basic Auth, using the same NROD
    account as the STOMP feed) and cached locally, since it changes rarely.
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        """Initialize the store.

        Args:
            hass: Home Assistant instance
            session: aiohttp client session
        """
        self._hass = hass
        self._session = session
        self._store = Store(hass, CORPUS_STORAGE_VERSION, f"{DOMAIN}_corpus")
        self._crs_to_stanox: dict[str, list[str]] = {}
        self._stanox_to_crs: dict[str, str] = {}
        self._fetched_at: str | None = None

    async def async_load(self) -> None:
        """Load any cached CORPUS data from storage."""
        raw = await self._store.async_load()
        if raw is None:
            return
        self._crs_to_stanox = raw.get("crs_to_stanox", {})
        self._stanox_to_crs = raw.get("stanox_to_crs", {})
        self._fetched_at = raw.get("fetched_at")
        _LOGGER.debug(
            "Loaded cached CORPUS data (%d stations, fetched %s)",
            len(self._crs_to_stanox),
            self._fetched_at,
        )

    def _is_stale(self) -> bool:
        """Return True if the cached data is missing or too old to trust."""
        if not self._crs_to_stanox or not self._fetched_at:
            return True
        fetched_at = dt_util.parse_datetime(self._fetched_at)
        if fetched_at is None:
            return True
        age = dt_util.utcnow() - fetched_at
        return age.days >= CORPUS_REFRESH_INTERVAL_DAYS

    async def async_ensure_fresh(self, username: str, password: str) -> None:
        """Refresh CORPUS data if the cache is missing or stale.

        On fetch failure: keep serving a stale cache if one exists (with a
        warning); raise CorpusUnavailableError only if there is no cache at
        all, so callers can disable the feature for that entry gracefully.

        Args:
            username: NROD account username
            password: NROD account password

        Raises:
            CorpusUnavailableError: If refresh fails and no cache exists
        """
        if not self._is_stale():
            return

        try:
            await self._async_fetch_and_store(username, password)
        except CorpusError:
            if self._crs_to_stanox:
                _LOGGER.warning(
                    "Failed to refresh CORPUS reference data; continuing with stale cache"
                )
                return
            raise

    async def _async_fetch_and_store(self, username: str, password: str) -> None:
        """Fetch CORPUS data from NROD and persist it to storage."""
        crs_to_stanox, stanox_to_crs = await self._async_fetch(username, password)

        self._crs_to_stanox = crs_to_stanox
        self._stanox_to_crs = stanox_to_crs
        self._fetched_at = dt_util.utcnow().isoformat()

        await self._store.async_save(
            {
                "crs_to_stanox": self._crs_to_stanox,
                "stanox_to_crs": self._stanox_to_crs,
                "fetched_at": self._fetched_at,
            }
        )
        _LOGGER.debug("Refreshed CORPUS data (%d stations)", len(self._crs_to_stanox))

    async def _async_fetch(
        self, username: str, password: str
    ) -> tuple[dict[str, list[str]], dict[str, str]]:
        """Fetch and parse the raw CORPUS JSON file from NROD.

        Returns:
            Tuple of (crs -> list of stanox codes, stanox -> crs)

        Raises:
            CorpusAuthenticationError: If NROD rejects the credentials
            CorpusUnavailableError: For any other fetch/parse failure
        """
        try:
            async with self._session.get(
                NROD_CORPUS_URL,
                auth=aiohttp.BasicAuth(username, password),
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise CorpusAuthenticationError("NROD authentication failed")
                if response.status != 200:
                    raise CorpusUnavailableError(
                        f"CORPUS download failed with status {response.status}"
                    )
                raw = await response.read()
        except CorpusError:
            raise
        except aiohttp.ClientError as err:
            raise CorpusUnavailableError(f"Network error fetching CORPUS data: {err}") from err

        try:
            data = json.loads(self._maybe_gunzip(raw))
        except (gzip.BadGzipFile, UnicodeDecodeError, ValueError) as err:
            raise CorpusUnavailableError(f"Invalid CORPUS response: {err}") from err

        return self._parse_corpus(data)

    @staticmethod
    def _maybe_gunzip(raw: bytes) -> bytes:
        """Decompress the response body if it's gzip, regardless of headers.

        NROD's CORPUS endpoint serves gzip-compressed content but doesn't
        always set a Content-Encoding header, so aiohttp's automatic
        decompression can't be relied on.
        """
        if raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        return raw

    @staticmethod
    def _parse_corpus(data: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, str]]:
        """Parse the CORPUS TIPLOCDATA array into CRS/STANOX lookup tables."""
        crs_to_stanox: dict[str, list[str]] = {}
        stanox_to_crs: dict[str, str] = {}

        for entry in data.get("TIPLOCDATA", []):
            crs = (entry.get("3ALPHA") or "").strip().upper()
            stanox = (entry.get("STANOX") or "").strip()

            if not crs or not stanox:
                continue

            crs_to_stanox.setdefault(crs, [])
            if stanox not in crs_to_stanox[crs]:
                crs_to_stanox[crs].append(stanox)
            stanox_to_crs.setdefault(stanox, crs)

        return crs_to_stanox, stanox_to_crs

    def get_stanox_codes_for_crs(self, crs: str) -> set[str]:
        """Return the set of STANOX codes associated with a CRS code."""
        return set(self._crs_to_stanox.get(crs.upper(), []))

    def get_crs_for_stanox(self, stanox: str) -> str | None:
        """Return the CRS code for a STANOX code, if known."""
        return self._stanox_to_crs.get(stanox)

    async def async_test_credentials(self, username: str, password: str) -> None:
        """Validate NROD credentials without establishing a STOMP connection.

        Raises:
            CorpusAuthenticationError: If the credentials are rejected
            CorpusUnavailableError: For any other connectivity failure
        """
        try:
            async with self._session.get(
                NROD_CORPUS_URL,
                auth=aiohttp.BasicAuth(username, password),
                timeout=aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise CorpusAuthenticationError("NROD authentication failed")
                if response.status != 200:
                    raise CorpusUnavailableError(
                        f"NROD credential check failed with status {response.status}"
                    )
        except CorpusError:
            raise
        except aiohttp.ClientError as err:
            raise CorpusUnavailableError(f"Network error validating NROD credentials: {err}") from err
