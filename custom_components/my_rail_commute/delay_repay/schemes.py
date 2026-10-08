"""Operator Delay Repay schemes: thresholds and claim links.

Schemes are user-configurable. Only a default scheme is bundled; operator
specific schemes (some use different thresholds) are entered in the options
flow as text so they can be corrected without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Final

DEFAULT_THRESHOLDS: Final = (15, 30, 60, 120)
MAX_THRESHOLD_MINUTES: Final = 24 * 60

_SPLIT_RE: Final = re.compile(r"[,\s;]+")
_NON_ALNUM_RE: Final = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class OperatorScheme:
    """Compensation thresholds (minutes) and an optional claim link."""

    thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS
    claim_url: str | None = None


@dataclass(frozen=True, slots=True)
class SchemeSet:
    """A default scheme plus per-operator overrides, keyed by normalised name."""

    default: OperatorScheme = field(default_factory=OperatorScheme)
    operators: dict[str, OperatorScheme] = field(default_factory=dict)

    def for_operator(self, operator: str | None) -> OperatorScheme:
        """Return the scheme for an operator name, else the default."""
        return self.operators.get(normalise_operator(operator), self.default)


def normalise_operator(name: str | None) -> str:
    """Normalise an operator name for matching.

    Case, punctuation and spacing differences are ignored, so
    "Great Western Railway" and "great-western  railway" match.
    """
    if not name:
        return ""
    return _NON_ALNUM_RE.sub(" ", name.casefold()).strip()


def parse_thresholds(text: str) -> tuple[int, ...]:
    """Parse thresholds such as "15,30,60,120" into a sorted tuple.

    Separators may be commas, spaces or semicolons. Duplicates are removed.

    Raises:
        ValueError: if empty, not whole numbers, or outside 1..1440 minutes.
    """
    parts = [p for p in _SPLIT_RE.split(text.strip()) if p]
    if not parts:
        raise ValueError("at least one threshold is required")
    values: set[int] = set()
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"'{part}' is not a whole number of minutes")
        minutes = int(part)
        if not 1 <= minutes <= MAX_THRESHOLD_MINUTES:
            raise ValueError(
                f"{minutes} is outside 1 to {MAX_THRESHOLD_MINUTES} minutes"
            )
        values.add(minutes)
    return tuple(sorted(values))


def parse_operator_schemes(text: str) -> dict[str, OperatorScheme]:
    """Parse operator overrides, one per line.

    Format: ``Operator Name = 15,30,60,120 | https://claim.example``. The claim
    link is optional. Blank lines and lines starting with ``#`` are ignored.

    Raises:
        ValueError: naming the offending line on any problem.
    """
    schemes: dict[str, OperatorScheme] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, rest = line.partition("=")
        key = normalise_operator(name)
        if not sep or not key:
            raise ValueError(f"line {number}: expected 'Operator = 15,30,60'")
        thresholds_text, _, url_text = rest.partition("|")
        try:
            thresholds = parse_thresholds(thresholds_text)
        except ValueError as err:
            raise ValueError(f"line {number}: {err}") from err
        url = url_text.strip() or None
        if url is not None and not re.match(r"^https?://\S+$", url):
            raise ValueError(f"line {number}: claim link must start with http(s)://")
        if key in schemes:
            raise ValueError(f"line {number}: duplicate operator '{name.strip()}'")
        schemes[key] = OperatorScheme(thresholds=thresholds, claim_url=url)
    return schemes


def build_scheme_set(default_thresholds: str, operators: str) -> SchemeSet:
    """Build a SchemeSet from the two option strings.

    Raises:
        ValueError: if either string is invalid.
    """
    return SchemeSet(
        default=OperatorScheme(thresholds=parse_thresholds(default_thresholds)),
        operators=parse_operator_schemes(operators),
    )
