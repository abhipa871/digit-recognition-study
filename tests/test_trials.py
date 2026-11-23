"""Unit tests for trial assignment, rendering, IDs and the local storage backend."""
import io
import random

import numpy as np
import pytest
from PIL import Image

from study import config
from study.storage import LocalStore, StorageError
from study.trials import (
    ATTENTION_CHECK_IMAGES,
    build_trial_plan,
    load_trials,
    normalize_participant_id,
    render_digit_png,
    trial_pixels,
)


@pytest.fixture(scope="module")
def trials():
    return load_trials(config.TRIALS_CSV)


def make_plan(trials, exposure=None, seen=None, seed=0):
    return build_trial_plan(trials, exposure or {}, seen or set(), random.Random(seed))


def test_trial_file_matches_assignment_2(trials):
    assert len(trials) == 200
    assert trials["model_correct"].sum() == 141  # 70.5% AI accuracy reported in A2


@pytest.mark.parametrize("seed", range(25))
def test_plan_is_stratified_with_two_checks(trials, seed):
    plan = make_plan(trials, seed=seed)
    study = [p for p in plan if not p["is_attention_check"]]
    checks = [p for p in plan if p["is_attention_check"]]

    assert [p["position"] for p in plan] == list(range(1, 23))
    assert len(study) == 20 and len(checks) == 2
    assert len({p["trial_id"] for p in study}) == 20
    for digit in range(10):
        assert sum(p["expected_digit"] == digit for p in study) == 2
    for p in study:  # the answer key in the plan matches the CSV
        assert trials.loc[p["trial_id"], "ground_truth"] == p["expected_digit"]
    # Checks are spread out, never in the first three or the last position.
    assert all(4 <= c["position"] <= 21 for c in checks)
    assert checks[0]["position"] <= 12 < checks[1]["position"]
    assert len({c["expected_digit"] for c in checks}) == 2
    assert all(c["trial_id"] is None for c in checks)


def test_plan_never_repeats_seen_trials(trials):
    first = make_plan(trials, seed=1)
    seen = {p["trial_id"] for p in first if p["trial_id"] is not None}
    second = make_plan(trials, seen=seen, seed=2)
    assert seen.isdisjoint(p["trial_id"] for p in second if p["trial_id"] is not None)


def test_plan_prefers_least_shown_trials(trials):
    # Every trial has been shown 5 times except two per digit.
    fresh = set()
    for digit in range(10):
        fresh.update(trials.loc[trials["ground_truth"] == digit, "trial_id"].head(2))
    exposure = {int(t): (0 if t in fresh else 5) for t in trials["trial_id"]}
    plan = make_plan(trials, exposure=exposure)
    assert {p["trial_id"] for p in plan if p["trial_id"] is not None} == fresh


def test_plan_still_works_when_everything_was_seen(trials):
    plan = make_plan(trials, seen=set(trials["trial_id"].astype(int)))
    assert sum(not p["is_attention_check"] for p in plan) == 20


def test_render_is_crisp_and_white_on_black(trials):
    png = render_digit_png(trial_pixels(trials, 1), size=256)
    image = Image.open(io.BytesIO(png))
    assert image.size == (256, 256)
    check = Image.open(io.BytesIO(render_digit_png(ATTENTION_CHECK_IMAGES[7], size=64)))
    values = set(np.asarray(check).ravel().tolist())
    assert values == {0, 255}  # nearest-neighbour: no blurred in-between values


@pytest.mark.parametrize("digit", sorted(ATTENTION_CHECK_IMAGES))
def test_attention_checks_do_not_touch_the_edge(digit):
    image = ATTENTION_CHECK_IMAGES[digit]
    assert image[0].sum() == image[-1].sum() == image[:, 0].sum() == image[:, -1].sum() == 0


def test_render_rejects_sizes_that_blur_pixels():
    with pytest.raises(ValueError):
        render_digit_png(ATTENTION_CHECK_IMAGES[0], size=250, frame=16)


@pytest.mark.parametrize(
    "raw, expected",
    [("AP-27", "ap-27"), ("  blue_sky9 ", "blue_sky9"), ("abcd", "abcd")],
)
def test_participant_id_is_normalized(raw, expected):
    assert normalize_participant_id(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "has space", "a" * 41, "emoji😀ok", "semi;colon"])
def test_bad_participant_ids_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_participant_id(raw)


# ---------------------------------------------------------------------------
# LocalStore mirrors the Supabase functions; these tests pin down the rules.
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, trials):
    return LocalStore(tmp_path / "study.db", dict(zip(trials["trial_id"], trials["ground_truth"])))


def answer_all(store, session_id, plan, wrong_positions=()):
    failures = 0
    for item in plan:
        digit = item["expected_digit"]
        if item["position"] in wrong_positions:
            digit = (digit + 1) % 10
        failures = store.record_response(session_id, item["position"], digit, 4, 1500, 600, 0, None)
    return failures


SURVEY = {key: 4 for key in config.SURVEY_KEYS} | {"comments": "  fine  "}


def test_local_store_full_session(store, trials):
    exposure, seen = store.assignment_context("tester-01")
    assert len(exposure) == 200 and set(exposure.values()) == {0} and seen == set()

    plan = make_plan(trials, exposure, seen)
    session_id = store.start_session("tester-01", "no_ai", plan, 1, "test")

    with pytest.raises(StorageError):  # cannot complete before answering everything
        store.end_session(session_id, "completed", SURVEY)

    check_positions = [p["position"] for p in plan if p["is_attention_check"]]
    assert answer_all(store, session_id, plan, wrong_positions=check_positions[:1]) == 1

    store.end_session(session_id, "completed", SURVEY)
    store.end_session(session_id, "completed", SURVEY)  # a retried request is harmless
    with pytest.raises(StorageError):
        store.record_response(session_id, 1, 3, 3, 100, None, 0, None)

    exposure, seen = store.assignment_context("tester-01")
    assert seen == {p["trial_id"] for p in plan if p["trial_id"] is not None}
    assert sum(exposure.values()) == 20


def test_local_store_uses_server_side_answer_key(store, trials):
    plan = make_plan(trials)
    tampered = [dict(p) for p in plan]
    first_study = next(p for p in tampered if not p["is_attention_check"])
    first_study["expected_digit"] = (first_study["expected_digit"] + 1) % 10
    session_id = store.start_session("tester-02", "no_ai", tampered, 1, "test")
    # Answering with the true digit still counts as correct.
    true_digit = int(trials.loc[first_study["trial_id"], "ground_truth"])
    store.record_response(session_id, first_study["position"], true_digit, 5, 900, None, 0, None)
    import sqlite3

    with sqlite3.connect(store.path) as db:
        (is_correct,) = db.execute(
            "select is_correct from digit_responses where session_id = ?", (session_id,)
        ).fetchone()
    assert is_correct == 1


@pytest.mark.parametrize(
    "args",
    [
        (10, 3, 100, None, 0),   # digit out of range
        (3, 0, 100, None, 0),    # confidence out of range
        (3, 3, -1, None, 0),     # negative time
        (3, 3, 100, 200, 0),     # first choice after submit
    ],
)
def test_local_store_rejects_bad_responses(store, trials, args):
    session_id = store.start_session("tester-03", "no_ai", make_plan(trials), 1, "test")
    with pytest.raises(StorageError):
        store.record_response(session_id, 1, *args, None)


def test_local_store_rejects_bad_survey(store, trials):
    plan = make_plan(trials)
    session_id = store.start_session("tester-04", "no_ai", plan, 1, "test")
    answer_all(store, session_id, plan)
    with pytest.raises(StorageError):
        store.end_session(session_id, "completed", SURVEY | {"effort": 9})


# ---------------------------------------------------------------------------
# AI condition rules
# ---------------------------------------------------------------------------

def fake_output(prediction=3, confidence=0.6):
    probabilities = [round((1 - confidence) / 9, 6)] * 10
    probabilities[prediction] = confidence
    return {
        "model_version": "test#sha256:000000000000",
        "prediction": prediction,
        "confidence": confidence,
        "probabilities": probabilities,
        "latency_ms": 1.2,
    }


def test_ai_answers_require_the_logged_ai_output(store, trials):
    plan = make_plan(trials)
    session_id = store.start_session("ai-tester-01", "ai", plan, 1, "test")
    with pytest.raises(StorageError, match="AI output"):
        store.record_response(session_id, 1, 3, 4, 900, None, 0, None)
    store.log_ai_output(session_id, 1, fake_output(), None)
    store.log_ai_output(session_id, 1, fake_output(prediction=5), None)  # first output is kept
    store.record_response(session_id, 1, 3, 4, 900, None, 0, None)
    store.log_interaction(session_id, 1, "top_guesses_opened", 450)
    import sqlite3

    with sqlite3.connect(store.path) as db:
        assert db.execute("select prediction from digit_ai_outputs").fetchall() == [(3,)]
        assert db.execute("select count(*) from digit_ai_interactions").fetchone() == (1,)


def test_ai_survey_items_are_required_in_ai_sessions(store, trials):
    plan = make_plan(trials)
    session_id = store.start_session("ai-tester-02", "ai", plan, 1, "test")
    for item in plan:
        store.log_ai_output(session_id, item["position"], fake_output(), None)
        store.record_response(session_id, item["position"], item["expected_digit"], 4, 900, None, 0, None)
    with pytest.raises(StorageError):
        store.end_session(session_id, "completed", SURVEY)
    store.end_session(session_id, "completed", SURVEY | {"ai_trust": 5, "ai_helpfulness": 6})


def test_ai_logging_is_refused_in_the_baseline(store, trials):
    session_id = store.start_session("tester-05", "no_ai", make_plan(trials), 1, "test")
    with pytest.raises(StorageError):
        store.log_ai_output(session_id, 1, fake_output(), None)
    with pytest.raises(StorageError):
        store.log_interaction(session_id, 1, "top_guesses_opened", 10)


@pytest.mark.parametrize(
    "change",
    [
        {"prediction": 10},
        {"confidence": 0.9},                       # doesn't match probabilities[prediction]
        {"probabilities": [0.1] * 9},              # wrong length
        {"probabilities": [0.5] * 10},             # doesn't sum to 1
        {"latency_ms": -1},
        {"model_version": ""},
    ],
)
def test_malformed_ai_outputs_are_rejected(store, trials, change):
    session_id = store.start_session("ai-tester-03", "ai", make_plan(trials), 1, "test")
    with pytest.raises(StorageError):
        store.log_ai_output(session_id, 1, fake_output() | change, None)


def test_prediction_must_be_the_most_likely_digit(store, trials):
    output = fake_output(prediction=3, confidence=0.2)
    output["probabilities"] = [0.0] * 10
    output["probabilities"][3], output["probabilities"][4] = 0.2, 0.8  # 4 is more likely than 3
    session_id = store.start_session("ai-tester-04", "ai", make_plan(trials), 1, "test")
    with pytest.raises(StorageError):
        store.log_ai_output(session_id, 1, output, None)
