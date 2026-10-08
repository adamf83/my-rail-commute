"""Tests for Delay Repay operator schemes."""

from __future__ import annotations

import pytest

from custom_components.my_rail_commute.delay_repay.schemes import (
    DEFAULT_THRESHOLDS,
    OperatorScheme,
    SchemeSet,
    build_scheme_set,
    normalise_operator,
    parse_operator_schemes,
    parse_thresholds,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Southern", "southern"),
        ("Great Western Railway", "great western railway"),
        ("great-western  RAILWAY", "great western railway"),
        ("  c2c ", "c2c"),
        ("London North Eastern Railway.", "london north eastern railway"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalise_operator(name, expected):
    assert normalise_operator(name) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15,30,60,120", (15, 30, 60, 120)),
        ("120, 60 30;15", (15, 30, 60, 120)),
        ("30", (30,)),
        ("30,30,15", (15, 30)),
        (" 15 , 30 ", (15, 30)),
    ],
)
def test_parse_thresholds(text, expected):
    assert parse_thresholds(text) == expected


@pytest.mark.parametrize("text", ["", "  ", "abc", "15,x", "0", "1441", "-5", "1.5"])
def test_parse_thresholds_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_thresholds(text)


def test_parse_operator_schemes():
    text = """
    # comment
    Southern = 30,60,120 | https://example.com/claim
    Great Western Railway = 15, 30, 60
    """
    schemes = parse_operator_schemes(text)
    assert schemes["southern"] == OperatorScheme(
        thresholds=(30, 60, 120), claim_url="https://example.com/claim"
    )
    assert schemes["great western railway"] == OperatorScheme(
        thresholds=(15, 30, 60), claim_url=None
    )


def test_parse_operator_schemes_empty():
    assert parse_operator_schemes("") == {}
    assert parse_operator_schemes("\n  \n# only a comment\n") == {}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("Southern", "line 1"),
        ("= 15,30", "line 1"),
        ("Southern = abc", "line 1"),
        ("\nSouthern = 15 | ftp://x", "line 2"),
        ("Southern = 15 | not a url", "claim link"),
        ("Southern = 15\nsouthern = 30", "duplicate"),
    ],
)
def test_parse_operator_schemes_errors_name_the_line(text, fragment):
    with pytest.raises(ValueError, match=fragment):
        parse_operator_schemes(text)


def test_scheme_set_lookup_falls_back_to_default():
    schemes = build_scheme_set("15,30", "Southern = 30,60 | https://example.com/c")
    assert schemes.for_operator("Southern").thresholds == (30, 60)
    assert schemes.for_operator("SOUTHERN ").claim_url == "https://example.com/c"
    assert schemes.for_operator("Thameslink").thresholds == (15, 30)
    assert schemes.for_operator(None).thresholds == (15, 30)


def test_scheme_set_defaults():
    assert SchemeSet().default.thresholds == DEFAULT_THRESHOLDS


def test_build_scheme_set_propagates_errors():
    with pytest.raises(ValueError):
        build_scheme_set("nope", "")
    with pytest.raises(ValueError):
        build_scheme_set("15", "bad line")
