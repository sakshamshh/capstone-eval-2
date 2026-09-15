from datetime import datetime, timedelta, timezone

import pytest

from logic import compute_light_state, haversine_distance_m

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def transition(state, changed_at, distance, seconds):
    return compute_light_state(state, changed_at, distance, 50, 4.5, START + timedelta(seconds=seconds))


def test_approaching_from_far_away():
    assert transition("idle", START, 100, 1) == ("idle", START)
    assert transition("idle", START, 50, 2) == ("yellow_flash", START + timedelta(seconds=2))


def test_staying_within_radius_past_yellow_duration():
    state, changed_at = transition("idle", START, 25, 1)
    assert transition(state, changed_at, 20, 5.49) == ("yellow_flash", changed_at)
    assert transition(state, changed_at, 20, 5.5) == ("green", START + timedelta(seconds=5.5))
    assert transition(state, changed_at, 20, 6) == ("green", START + timedelta(seconds=6))


def test_moving_away_during_yellow():
    assert transition("yellow_flash", START, 50.01, 2) == ("idle", START + timedelta(seconds=2))


def test_moving_away_during_green():
    assert transition("green", START, 100, 10) == ("idle", START + timedelta(seconds=10))


def test_green_stays_green_without_resetting_timestamp():
    assert transition("green", START, 0, 100) == ("green", START)


def test_returning_after_departure_starts_full_yellow_period():
    state, changed_at = transition("yellow_flash", START, 100, 4)
    state, changed_at = transition(state, changed_at, 0, 5)
    assert transition(state, changed_at, 0, 6) == ("yellow_flash", START + timedelta(seconds=5))


def test_clock_moving_back_does_not_skip_yellow():
    assert transition("yellow_flash", START, 0, -1) == ("yellow_flash", START)


def test_haversine_known_distance_and_edge_cases():
    assert haversine_distance_m(0, 0, 0, 0) == 0
    assert haversine_distance_m(0, 0, 0, 1) == pytest.approx(111_194.93, abs=0.01)
    assert haversine_distance_m(0, 180, 0, -180) == pytest.approx(0, abs=1e-6)
    assert haversine_distance_m(90, 0, -90, 0) == pytest.approx(20_015_086.8, abs=0.1)
