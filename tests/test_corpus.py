"""Tests for the CORPUS reference data store."""
from __future__ import annotations

import gzip
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.util import dt as dt_util
import pytest

from custom_components.my_rail_commute.corpus import (
    CorpusAuthenticationError,
    CorpusReferenceStore,
    CorpusUnavailableError,
)

SAMPLE_CORPUS = {
    "TIPLOCDATA": [
        {"STANOX": "87701", "3ALPHA": "PAD", "TIPLOC": "PADTON"},
        {"STANOX": "87702", "3ALPHA": "PAD", "TIPLOC": "PADTONL"},
        {"STANOX": "88616", "3ALPHA": "RDG", "TIPLOC": "READING"},
        {"STANOX": "", "3ALPHA": "", "TIPLOC": ""},
        {"STANOX": "12345", "3ALPHA": " ", "TIPLOC": "NOCRS"},
    ]
}


class _FakeResponse:
    def __init__(self, status: int, json_data=None, gzip_body: bool = False):
        self.status = status
        if json_data is None:
            self._body = b""
        else:
            body = json.dumps(json_data).encode("utf-8")
            self._body = gzip.compress(body) if gzip_body else body

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _make_store(load_return=None):
    """Return a CorpusReferenceStore with a mocked HA Store and session."""
    hass = MagicMock()
    session = MagicMock()
    with patch("custom_components.my_rail_commute.corpus.Store") as MockStore:
        instance = MockStore.return_value
        instance.async_load = AsyncMock(return_value=load_return)
        instance.async_save = AsyncMock(return_value=None)
        store = CorpusReferenceStore(hass, session)
        store._store = instance
    return store, session


@pytest.mark.asyncio
async def test_async_load_no_cache():
    """async_load with no persisted data leaves the store empty."""
    store, _ = _make_store(load_return=None)
    await store.async_load()
    assert store.get_stanox_codes_for_crs("PAD") == set()


@pytest.mark.asyncio
async def test_async_load_restores_cache():
    """async_load restores previously persisted CRS/STANOX mappings."""
    existing = {
        "crs_to_stanox": {"PAD": ["87701", "87702"]},
        "stanox_to_crs": {"87701": "PAD", "87702": "PAD"},
        "fetched_at": dt_util.utcnow().isoformat(),
    }
    store, _ = _make_store(load_return=existing)
    await store.async_load()
    assert store.get_stanox_codes_for_crs("PAD") == {"87701", "87702"}
    assert store.get_crs_for_stanox("87701") == "PAD"


def test_parse_corpus_skips_blank_entries():
    """Blank CRS/STANOX entries are skipped; multi-STANOX CRS codes are merged."""
    crs_to_stanox, stanox_to_crs = CorpusReferenceStore._parse_corpus(SAMPLE_CORPUS)
    assert crs_to_stanox["PAD"] == ["87701", "87702"]
    assert crs_to_stanox["RDG"] == ["88616"]
    assert "NOCRS" not in stanox_to_crs.values()
    assert "12345" not in stanox_to_crs


@pytest.mark.asyncio
async def test_async_ensure_fresh_fetches_when_no_cache():
    """A missing cache triggers a fetch and populates the lookup tables."""
    store, session = _make_store(load_return=None)
    await store.async_load()
    session.get = MagicMock(return_value=_FakeResponse(200, SAMPLE_CORPUS))

    await store.async_ensure_fresh("user", "pass")

    assert store.get_stanox_codes_for_crs("PAD") == {"87701", "87702"}
    store._store.async_save.assert_called_once()


@pytest.mark.asyncio
async def test_async_ensure_fresh_fetches_gzipped_response_without_content_encoding_header():
    """NROD sometimes serves gzip bytes without a Content-Encoding header; we must gunzip it."""
    store, session = _make_store(load_return=None)
    await store.async_load()
    session.get = MagicMock(return_value=_FakeResponse(200, SAMPLE_CORPUS, gzip_body=True))

    await store.async_ensure_fresh("user", "pass")

    assert store.get_stanox_codes_for_crs("PAD") == {"87701", "87702"}


@pytest.mark.asyncio
async def test_async_ensure_fresh_invalid_body_raises_unavailable():
    """A response body that is neither valid JSON nor gzip raises CorpusUnavailableError."""
    store, session = _make_store(load_return=None)
    await store.async_load()
    response = _FakeResponse(200)
    response._body = b"\x8b not json or gzip"
    session.get = MagicMock(return_value=response)

    with pytest.raises(CorpusUnavailableError):
        await store.async_ensure_fresh("user", "pass")


@pytest.mark.asyncio
async def test_async_ensure_fresh_skips_when_fresh():
    """A recently-fetched cache is not refetched."""
    existing = {
        "crs_to_stanox": {"PAD": ["87701"]},
        "stanox_to_crs": {"87701": "PAD"},
        "fetched_at": dt_util.utcnow().isoformat(),
    }
    store, session = _make_store(load_return=existing)
    await store.async_load()
    session.get = MagicMock(side_effect=AssertionError("should not fetch when fresh"))

    await store.async_ensure_fresh("user", "pass")

    assert store.get_stanox_codes_for_crs("PAD") == {"87701"}


@pytest.mark.asyncio
async def test_async_ensure_fresh_auth_failure_no_cache_raises():
    """Auth failure with no existing cache raises CorpusAuthenticationError."""
    store, session = _make_store(load_return=None)
    await store.async_load()
    session.get = MagicMock(return_value=_FakeResponse(401))

    with pytest.raises(CorpusAuthenticationError):
        await store.async_ensure_fresh("user", "bad_pass")


@pytest.mark.asyncio
async def test_async_ensure_fresh_serves_stale_cache_on_failure():
    """A fetch failure with an existing stale cache keeps serving the stale data."""
    stale_fetched_at = (dt_util.utcnow() - timedelta(days=30)).isoformat()
    existing = {
        "crs_to_stanox": {"PAD": ["87701"]},
        "stanox_to_crs": {"87701": "PAD"},
        "fetched_at": stale_fetched_at,
    }
    store, session = _make_store(load_return=existing)
    await store.async_load()
    session.get = MagicMock(return_value=_FakeResponse(500))

    await store.async_ensure_fresh("user", "pass")

    # Stale cache is preserved rather than wiped out by the failed refresh
    assert store.get_stanox_codes_for_crs("PAD") == {"87701"}


@pytest.mark.asyncio
async def test_async_test_credentials_success():
    """A 200 response means credentials are valid."""
    store, session = _make_store()
    session.get = MagicMock(return_value=_FakeResponse(200))
    await store.async_test_credentials("user", "pass")


@pytest.mark.asyncio
async def test_async_test_credentials_auth_failure():
    """A 401 response raises CorpusAuthenticationError."""
    store, session = _make_store()
    session.get = MagicMock(return_value=_FakeResponse(401))
    with pytest.raises(CorpusAuthenticationError):
        await store.async_test_credentials("user", "bad_pass")


@pytest.mark.asyncio
async def test_async_test_credentials_other_failure():
    """A non-200/401 response raises CorpusUnavailableError."""
    store, session = _make_store()
    session.get = MagicMock(return_value=_FakeResponse(503))
    with pytest.raises(CorpusUnavailableError):
        await store.async_test_credentials("user", "pass")
