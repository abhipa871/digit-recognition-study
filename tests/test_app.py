"""End-to-end tests of both study versions with AppTest and the local SQLite backend."""
import json
import sqlite3

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from study import config
from study.resources import get_trials

BASELINE_APP = "../streamlit_app.py"
AI_APP = "../streamlit_app_ai.py"

# AppTest reruns the default page after the app calls st.switch_page, whereas a
# browser stays on the new page. follow() mirrors the browser by pointing AppTest
# at the page for the participant's current stage before the next interaction.
STAGE_PAGES = {
    "instructions": "app_pages/instructions.py",
    "task": "app_pages/task.py",
    "survey": "app_pages/survey.py",
    "completed": "app_pages/finished.py",
    "terminated": "app_pages/finished.py",
}


def follow(at: AppTest) -> AppTest:
    at.switch_page(STAGE_PAGES[at.session_state["stage"]])
    return at


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "study.db"
    monkeypatch.setenv("DIGIT_STUDY_BACKEND", "local")
    monkeypatch.setenv("DIGIT_STUDY_LOCAL_DB", str(path))
    st.cache_resource.clear()  # get_store() is cached per process
    yield path
    st.cache_resource.clear()


def open_app(app=BASELINE_APP) -> AppTest:
    return AppTest.from_file(app, default_timeout=60).run()


def start(at: AppTest, user_id="Tester-01", answer=None) -> AppTest:
    condition = at.session_state["condition"]
    at.text_input[0].set_value(user_id)
    at.radio[0].set_value(answer or config.COMPREHENSION[condition]["answer"])
    at.button[0].click()
    return follow(at.run())


def answer_current(at: AppTest, digit: int, confidence: int = 4) -> AppTest:
    at.button(key=f"digit_{digit}").click().run()
    position = at.session_state["position"]
    at.button_group(key=f"confidence_{position}").set_value(confidence).run()
    return follow(at.button(key="submit_answer").click().run())


def finish_survey(at: AppTest, value=5) -> AppTest:
    for item in config.survey_items(at.session_state["condition"]):
        at.button_group(key=f"survey_{item['key']}").set_value(value)
    at.text_area[0].set_value("All clear.")
    return follow(at.button[0].click().run())


def query(path, sql, *params):
    with sqlite3.connect(path) as db:
        return db.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Baseline (no AI)
# ---------------------------------------------------------------------------

def test_full_participant_flow(db_path):
    at = open_app()
    assert not at.exception
    assert "Handwritten digit recognition" in at.title[0].value
    assert at.info  # local-mode banner is visible
    assert not at.metric  # no AI anywhere in the baseline

    at = start(at)
    assert not at.exception
    assert at.session_state["stage"] == "task"
    plan = at.session_state["plan"]
    assert len(plan) == 22
    assert not at.metric  # no AI card on the task page either

    # Submit stays disabled until a digit and a confidence level are chosen.
    assert at.button(key="submit_answer").disabled

    for index, item in enumerate(plan):
        assert at.session_state["position"] == index
        # Change your mind once on the first image to exercise the change counter.
        if index == 0:
            at.button(key=f"digit_{(item['expected_digit'] + 1) % 10}").click().run()
        at = answer_current(at, item["expected_digit"])
        assert not at.exception

    assert at.session_state["stage"] == "survey"
    assert len(at.button_group) == len(config.SURVEY_ITEMS)  # no AI questions
    at = finish_survey(at)
    assert not at.exception
    assert at.session_state["stage"] == "completed"
    assert any("Thank you" in t.value for t in at.title)

    (session,) = query(
        db_path,
        "select s.status, s.condition, s.comprehension_attempts, s.attention_failures, "
        "s.n_trials, p.user_id from digit_sessions s join digit_participants p using (participant_id)",
    )
    assert session == ("completed", "no_ai", 1, 0, 22, "tester-01")
    responses = query(
        db_path,
        "select position, is_correct, confidence, response_time_ms, first_choice_ms, digit_changes "
        "from digit_responses order by position",
    )
    assert [r[0] for r in responses] == list(range(1, 23))
    assert all(r[1] == 1 and r[2] == 4 and r[3] >= 0 for r in responses)
    assert responses[0][5] == 1 and all(r[5] == 0 for r in responses[1:])
    assert all(r[4] is not None and r[4] <= r[3] for r in responses)
    (survey,) = query(db_path, "select mental_demand, frustration, ai_trust, comments from digit_surveys")
    assert survey == (5, 5, None, "All clear.")
    assert query(db_path, "select count(*) from digit_ai_outputs") == [(0,)]


def test_wrong_comprehension_answer_can_be_retried(db_path):
    at = open_app()
    at = start(at, answer=config.COMPREHENSION["no_ai"]["options"][0])
    assert at.error and at.session_state["stage"] == "instructions"
    at.radio[0].set_value(config.COMPREHENSION["no_ai"]["answer"])
    follow(at.button[0].click().run())
    assert at.session_state["stage"] == "task"
    (attempts,) = query(db_path, "select comprehension_attempts from digit_sessions")[0]
    assert attempts == 2


def test_invalid_participant_id_is_rejected(db_path):
    at = start(open_app(), user_id="no spaces allowed")
    assert at.error and at.session_state["stage"] == "instructions"
    assert not db_path.exists()  # nothing was written


def test_failing_both_attention_checks_ends_the_attempt(db_path):
    at = start(open_app())
    for item in at.session_state["plan"]:
        digit = item["expected_digit"]
        if item["is_attention_check"]:
            digit = (digit + 5) % 10
        at = answer_current(at, digit)
        if at.session_state["stage"] != "task":
            break
    assert at.session_state["stage"] == "terminated"
    assert query(db_path, "select status, attention_failures from digit_sessions") == [("terminated", 2)]

    follow(at.button[0].click().run())  # "Start over"
    assert at.session_state["stage"] == "instructions"
    assert at.session_state["user_id"] == "tester-01"


def test_second_session_with_same_id_gets_new_images(db_path):
    first = start(open_app(), user_id="repeat-01")
    second = start(open_app(), user_id="REPEAT-01")
    ids = lambda at: {p["trial_id"] for p in at.session_state["plan"] if p["trial_id"] is not None}
    assert ids(first).isdisjoint(ids(second))


# ---------------------------------------------------------------------------
# AI condition (synchronous model output)
# ---------------------------------------------------------------------------

def test_ai_instructions_show_live_model_examples(db_path):
    at = open_app(AI_APP)
    assert not at.exception
    assert at.session_state["condition"] == "ai"
    text = " ".join(c.value for c in at.caption)
    # Live model outputs on the three examples (see tools/ and tests/test_model.py).
    assert "AI suggested **0**" in text and "AI suggested **1**" in text
    assert "7 out of 10 times" in " ".join(m.value for m in at.markdown)


def test_ai_full_participant_flow(db_path):
    trials = get_trials()
    at = start(open_app(AI_APP), user_id="AI-Tester")
    assert not at.exception and at.session_state["stage"] == "task"
    plan = at.session_state["plan"]

    followed = 0
    for index, item in enumerate(plan):
        output = at.session_state["ai_output"]
        # The AI card shows the live prediction, which matches Assignment 2 exactly.
        assert at.metric[0].value == str(output["prediction"])
        if item["is_attention_check"]:
            assert output["prediction"] == item["expected_digit"]
        else:
            assert output["prediction"] == trials.loc[item["trial_id"], "model_prediction"]
        if index == 0:
            at.button(key="top_guesses_0").click().run()
            assert at.session_state["ai_top_guesses_open"]
            assert "top 3 guesses" in " ".join(c.value for c in at.caption)
        # Follow the AI on study images, answer checks correctly.
        digit = item["expected_digit"] if item["is_attention_check"] else output["prediction"]
        followed += not item["is_attention_check"]
        at = answer_current(at, digit)
        assert not at.exception

    assert at.session_state["stage"] == "survey"
    assert len(at.button_group) == len(config.SURVEY_ITEMS) + len(config.AI_SURVEY_ITEMS)
    at = finish_survey(at, value=6)
    assert at.session_state["stage"] == "completed" and not at.exception

    assert query(db_path, "select condition, status from digit_sessions") == [("ai", "completed")]
    outputs = query(
        db_path,
        "select st.position, st.trial_id, a.prediction, a.confidence, a.probabilities, "
        "a.model_version, r.selected_digit from digit_ai_outputs a "
        "join digit_session_trials st using (session_id, position) "
        "join digit_responses r using (session_id, position) order by st.position",
    )
    assert len(outputs) == 22
    for position, trial_id, prediction, confidence, probabilities, version, selected in outputs:
        probabilities = json.loads(probabilities)
        assert len(probabilities) == 10 and abs(sum(probabilities) - 1) < 1e-3
        assert probabilities[prediction] == pytest.approx(confidence)
        assert version.startswith("small_resnet_assignment2.pth#sha256:027a1ffbeff7")
        if trial_id is not None:
            assert confidence == pytest.approx(trials.loc[trial_id, "model_confidence"], abs=1e-5)
            assert selected == prediction  # we followed the AI
    interactions = query(db_path, "select position, action, ms_since_shown from digit_ai_interactions")
    assert len(interactions) == 1 and interactions[0][:2] == (1, "top_guesses_opened")
    assert interactions[0][2] >= 0
    (survey,) = query(db_path, "select ai_trust, ai_helpfulness from digit_surveys")
    assert survey == (6, 6)


def test_same_id_across_conditions_never_repeats_images(db_path):
    baseline = start(open_app(BASELINE_APP), user_id="both-01")
    ai = start(open_app(AI_APP), user_id="both-01")
    ids = lambda at: {p["trial_id"] for p in at.session_state["plan"] if p["trial_id"] is not None}
    assert len(ids(baseline)) == len(ids(ai)) == 20
    assert ids(baseline).isdisjoint(ids(ai))
    assert sorted(query(db_path, "select condition from digit_sessions")) == [("ai",), ("no_ai",)]
