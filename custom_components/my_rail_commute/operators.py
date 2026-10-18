"""Train operator filtering: known operators, option lists and matching."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

# National Rail train operating company codes (as in Darwin's operatorCode).
# Used for the filter checkboxes before any live data has been seen; operators
# met on live boards but missing here are added to the list automatically.
KNOWN_OPERATORS: Final[dict[str, str]] = {
    "AW": "Transport for Wales",
    "CC": "c2c",
    "CH": "Chiltern Railways",
    "CS": "Caledonian Sleeper",
    "EM": "East Midlands Railway",
    "ES": "Eurostar",
    "GC": "Grand Central",
    "GN": "Great Northern",
    "GR": "LNER",
    "GW": "Great Western Railway",
    "GX": "Gatwick Express",
    "HC": "Heathrow Connect",
    "HT": "Hull Trains",
    "HX": "Heathrow Express",
    "IL": "Island Line",
    "LD": "Lumo",
    "LE": "Greater Anglia",
    "LM": "West Midlands Trains",
    "LO": "London Overground",
    "LT": "Elizabeth line",
    "ME": "Merseyrail",
    "NT": "Northern",
    "SE": "Southeastern",
    "SN": "Southern",
    "SR": "ScotRail",
    "SW": "South Western Railway",
    "TL": "Thameslink",
    "TP": "TransPennine Express",
    "VT": "Avanti West Coast",
    "XC": "CrossCountry",
}


def normalise_operator_code(code: str | None) -> str:
    """Return an operator code in the canonical (upper-case) form."""
    return (code or "").strip().upper()


def operator_options(
    observed: dict[str, str] | None = None,
    selected: Iterable[str] = (),
) -> dict[str, str]:
    """Build the code -> label mapping offered as filter checkboxes.

    Merges the bundled operators, those seen on live boards, and any codes
    already selected (so a stored choice never vanishes from the form).
    Sorted by label.
    """
    options: dict[str, str] = dict(KNOWN_OPERATORS)
    for code, name in (observed or {}).items():
        code = normalise_operator_code(code)
        if code and code not in options:
            options[code] = name or code
    for code in selected:
        code = normalise_operator_code(code)
        if code and code not in options:
            options[code] = code
    return dict(sorted(options.items(), key=lambda item: item[1].lower()))


def clean_operator_filter(codes: Iterable[str] | None) -> list[str]:
    """Normalise a stored filter to a sorted, de-duplicated list of codes."""
    return sorted({c for c in map(normalise_operator_code, codes or []) if c})


def service_matches_operators(
    service: dict[str, Any], operator_codes: Iterable[str]
) -> bool:
    """Return True if the service is run by one of the given operators.

    An empty filter matches everything. Services without an operator code
    (for example rail-replacement buses that Darwin does not attribute) are
    excluded once a filter is set.
    """
    codes = set(operator_codes)
    if not codes:
        return True
    return normalise_operator_code(service.get("operator_code")) in codes
