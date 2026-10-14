import json
import stat

import pytest

from gamescoach.engine import create_session
from gamescoach.models import AthleteProfile
from gamescoach.storage import Store, StoreError


def test_profile_and_full_session_roundtrip_with_private_permissions(tmp_path):
    store = Store(tmp_path / "private")
    profile = AthleteProfile(name="Athlete", experience="Competing", training_days_per_week=4)
    store.save_profile(profile)
    assert store.load_profile() == profile
    session = create_session("competition", "CrossFit pacing", profile, demo=True)
    store.save_session(session)
    assert store.get_session(session.id).to_dict() == session.to_dict()
    assert store.list_sessions() == [session]
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "profile.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((store.root / "sessions" / f"{session.id}.json").stat().st_mode) == 0o600


def test_duplicate_session_does_not_overwrite_existing_plan(tmp_path):
    store = Store(tmp_path)
    session = create_session("workout", "CrossFit workout", AthleteProfile(), demo=True)
    store.save_session(session)
    before = (tmp_path / "sessions" / f"{session.id}.json").read_bytes()
    session.final_plan = "Unwanted replacement"
    with pytest.raises(StoreError, match="not overwritten"):
        store.save_session(session)
    assert (tmp_path / "sessions" / f"{session.id}.json").read_bytes() == before


def test_missing_and_traversal_ids_are_rejected(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(StoreError, match="Invalid plan ID"):
        store.get_session("../../profile")
    with pytest.raises(StoreError, match="not found"):
        store.get_session("abcdef123456")


def test_corrupt_history_has_actionable_error(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "sessions").mkdir()
    (tmp_path / "sessions" / "abcdef123456.json").write_text("not json")
    with pytest.raises(StoreError, match="JSON contents"):
        store.list_sessions()


def test_mismatched_session_id_is_detected(tmp_path):
    store = Store(tmp_path)
    session = create_session("workout", "CrossFit workout", AthleteProfile(), demo=True)
    store.save_session(session)
    path = tmp_path / "sessions" / f"{session.id}.json"
    data = json.loads(path.read_text())
    data["id"] = "abcdef123456"
    path.write_text(json.dumps(data))
    with pytest.raises(StoreError, match="does not match"):
        store.get_session(session.id)


@pytest.mark.parametrize("values", [
    {"age": True}, {"age": -1}, {"training_days_per_week": 8},
    {"bodyweight_kg": float("nan")}, {"bodyweight_kg": float("inf")},
    {"competition_date": "2026-02-30"}, {"competition_date": "20261014"},
    {"equipment": ["rower"]}, {"unknown_field": 10},
])
def test_invalid_profile_values(values):
    with pytest.raises(ValueError):
        AthleteProfile.from_dict(values)
