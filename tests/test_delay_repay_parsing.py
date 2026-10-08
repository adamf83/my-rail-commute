"""Tests for reading actual arrivals from service-details responses."""

from __future__ import annotations

import pytest

from custom_components.my_rail_commute.delay_repay.parsing import (
    ArrivalObservation,
    observation_from_board_details,
    observation_from_calling_points,
)

# --- Destination board details (top-level ata) -------------------------------


def test_board_details_late_actual():
    # London Bridge example: sta 22:53, ata 23:05
    details = {
        "locationName": "London Bridge",
        "crs": "LBG",
        "sta": "22:53",
        "ata": "23:05",
    }
    assert observation_from_board_details(details) == ArrivalObservation(actual="23:05")


def test_board_details_on_time_actual():
    # Caterham example: ata is the text "On time"
    details = {"crs": "CAT", "sta": "22:57", "ata": "On time"}
    assert observation_from_board_details(details).actual == "On time"


def test_board_details_not_arrived_yet():
    details = {"crs": "LBG", "sta": "22:53", "eta": "23:05"}
    obs = observation_from_board_details(details)
    assert obs.actual is None
    assert obs.has_outcome is False


def test_board_details_cancelled():
    details = {"crs": "LBG", "sta": "22:53", "isCancelled": True}
    obs = observation_from_board_details(details)
    assert obs.is_cancelled is True
    assert obs.has_outcome is True


@pytest.mark.parametrize("bad", [None, [], "text", 5])
def test_board_details_invalid_payload(bad):
    assert observation_from_board_details(bad) == ArrivalObservation()


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_board_details_blank_ata_is_not_an_actual(empty):
    assert observation_from_board_details({"ata": empty}).actual is None


# --- Downstream board details (calling points) -------------------------------


def _details(points, extra_groups=()):
    groups = [{"callingPoint": points}, *extra_groups]
    return {"crs": "LBG", "previousCallingPoints": groups}


def test_calling_points_late_actual():
    details = _details(
        [
            {"crs": "OXT", "st": "22:20", "at": "22:31", "isCancelled": False},
            {"crs": "ECR", "st": "22:38", "at": "22:50", "isCancelled": False},
        ]
    )
    assert observation_from_calling_points(details, "ECR") == ArrivalObservation(
        actual="22:50"
    )


def test_calling_points_on_time_actual():
    details = _details([{"crs": "RAI", "st": "21:30", "at": "On time"}])
    assert observation_from_calling_points(details, "RAI").actual == "On time"


def test_calling_points_crs_match_is_case_insensitive():
    details = _details([{"crs": "ECR", "st": "22:38", "at": "22:50"}])
    assert observation_from_calling_points(details, " ecr ").actual == "22:50"


def test_calling_points_stop_not_reached_yet():
    # A future stop carries et, not at
    details = _details([{"crs": "SEV", "st": "23:20", "et": "On time"}])
    obs = observation_from_calling_points(details, "SEV")
    assert obs == ArrivalObservation(actual=None)
    assert obs.has_outcome is False


def test_calling_points_cancelled_stop():
    details = _details([{"crs": "ECR", "st": "22:38", "isCancelled": True}])
    obs = observation_from_calling_points(details, "ECR")
    assert obs.is_cancelled is True
    assert obs.has_outcome is True


def test_calling_points_destination_not_listed():
    details = _details([{"crs": "OXT", "st": "22:20", "at": "22:31"}])
    assert observation_from_calling_points(details, "ECR") is None


def test_calling_points_circular_route_is_ambiguous():
    details = _details(
        [
            {"crs": "ECR", "st": "22:38", "at": "22:50"},
            {"crs": "ECR", "st": "23:10", "et": "On time"},
        ]
    )
    assert observation_from_calling_points(details, "ECR") is None


def test_calling_points_only_first_list_is_used():
    # A splitting/joining portion must not be read as the through train
    details = _details(
        [{"crs": "OXT", "st": "22:20", "at": "22:31"}],
        extra_groups=[{"callingPoint": [{"crs": "ECR", "st": "22:38", "at": "22:50"}]}],
    )
    assert observation_from_calling_points(details, "ECR") is None


def test_calling_points_tolerates_single_dict_shapes():
    details = {
        "previousCallingPoints": {
            "callingPoint": {"crs": "ECR", "st": "22:38", "at": "22:50"}
        }
    }
    assert observation_from_calling_points(details, "ECR").actual == "22:50"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"previousCallingPoints": []},
        {"previousCallingPoints": [None]},
        {"previousCallingPoints": [{"callingPoint": "nope"}]},
    ],
)
def test_calling_points_invalid_payload(bad):
    assert observation_from_calling_points(bad, "ECR") is None
