import time
from datetime import datetime, timezone

import streamlit as st

from study import config
from study.model import top_guesses
from study.resources import get_model, get_store, plan_item_image, plan_item_pixels
from study.storage import StorageError

if st.session_state.stage != "task" or not st.session_state.session_id:
    if st.session_state.stage == "survey":
        st.switch_page("app_pages/survey.py")
    if st.session_state.stage in ("completed", "terminated"):
        st.switch_page("app_pages/finished.py")
    st.warning("Please read the instructions and enter a participant ID first.", icon=":material/info:")
    if st.button("Go to instructions", icon=":material/arrow_back:"):
        st.switch_page("app_pages/instructions.py")
    st.stop()

plan = st.session_state.plan
position = st.session_state.position
total = len(plan)
item = plan[position]
with_ai = st.session_state.condition == "ai"

# First time this image is shown (not on every rerun):
# 1. AI condition: run the model live on the image and log exactly what will be shown.
# 2. Start the response timer, so it measures the participant's decision only.
if st.session_state.shown_position != position:
    shown_at = datetime.now(timezone.utc).isoformat()
    ai_output = None
    if with_ai:
        try:
            ai_output = get_model().predict(plan_item_pixels(item))
        except Exception:  # noqa: BLE001 - the AI version cannot continue without the model
            st.error(
                "The AI model is unavailable, so the study can't continue right now. "
                "Please tell the researcher.",
                icon=":material/error:",
            )
            st.stop()
        try:
            get_store().log_ai_output(st.session_state.session_id, item["position"], ai_output, shown_at)
        except StorageError as error:
            st.error(f"{error} The image will appear once this works.", icon=":material/cloud_off:")
            st.button("Try again", icon=":material/refresh:")
            st.stop()
    st.session_state.update(
        shown_position=position,
        trial_started=time.monotonic(),
        shown_at=shown_at,
        digit=None,
        first_choice_at=None,
        digit_changes=0,
        ai_output=ai_output,
        ai_top_guesses_open=False,
    )


def choose_digit(digit: int) -> None:
    current = st.session_state.digit
    if current is None and st.session_state.first_choice_at is None:
        st.session_state.first_choice_at = time.monotonic()
    elif current is not None and current != digit:
        st.session_state.digit_changes += 1
    st.session_state.digit = digit


def open_top_guesses(trial_position: int) -> None:
    st.session_state.ai_top_guesses_open = True
    ms_since_shown = int(round((time.monotonic() - st.session_state.trial_started) * 1000))
    # Logging this optional interaction is retried once but never blocks the participant.
    for _ in range(2):
        try:
            get_store().log_interaction(
                st.session_state.session_id, trial_position, "top_guesses_opened", ms_since_shown
            )
            return
        except StorageError:
            continue


with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    st.badge(f"Image {position + 1} of {total}", icon=":material/grid_on:", color="gray")
    st.caption(f"Participant ID: {st.session_state.user_id}")
st.progress(position / total)

st.subheader("Which digit is this?")
# Top-aligned so the image stays put when the AI card grows (top guesses opened).
with st.container(horizontal=True, horizontal_alignment="center", vertical_alignment="top", gap="medium"):
    st.image(plan_item_image(item), width=config.IMAGE_DISPLAY_PX)
    if with_ai:
        ai_output = st.session_state.ai_output
        with st.container(border=True, width=240, gap="small", key="ai_card"):
            st.metric(":material/smart_toy: AI suggestion", ai_output["prediction"])
            st.progress(ai_output["confidence"], text=f"AI confidence: {ai_output['confidence']:.0%}")
            if st.session_state.ai_top_guesses_open:
                st.caption(f"The AI's top {config.AI_TOP_GUESSES} guesses")
                for guess, probability in top_guesses(ai_output, config.AI_TOP_GUESSES):
                    st.progress(probability, text=f"{guess}: {probability:.0%}")
            else:
                st.button(
                    f"Show the AI's top {config.AI_TOP_GUESSES} guesses",
                    key=f"top_guesses_{position}",
                    type="tertiary",
                    icon=":material/bar_chart:",
                    on_click=open_top_guesses,
                    args=(item["position"],),
                )

# Fixed-width buttons: one row of ten on a laptop, two even rows of five on a phone.
with st.container(horizontal=True, horizontal_alignment="distribute", gap="small", key="digit_buttons"):
    for digit in range(10):
        st.button(
            str(digit),
            key=f"digit_{digit}",
            type="primary" if st.session_state.digit == digit else "secondary",
            on_click=choose_digit,
            args=(digit,),
            width=56,
        )

confidence = st.segmented_control(
    "How confident are you?",
    options=list(config.CONFIDENCE_LABELS),
    format_func=config.CONFIDENCE_LABELS.get,
    required=True,
    key=f"confidence_{position}",
    width="stretch",
)

with st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
    st.caption("Answers can't be changed after you submit.")
    submitted = st.button(
        "Submit answer",
        key="submit_answer",
        type="primary",
        icon=":material/arrow_forward:",
        disabled=st.session_state.digit is None or confidence is None,
    )

if submitted:
    response_time_ms = int(round((time.monotonic() - st.session_state.trial_started) * 1000))
    first_choice_ms = None
    if st.session_state.first_choice_at is not None:
        first_choice_ms = min(
            response_time_ms,
            int(round((st.session_state.first_choice_at - st.session_state.trial_started) * 1000)),
        )
    try:
        failures = get_store().record_response(
            st.session_state.session_id,
            item["position"],
            st.session_state.digit,
            int(confidence),
            response_time_ms,
            first_choice_ms,
            st.session_state.digit_changes,
            st.session_state.shown_at,
        )
    except StorageError as error:
        st.error(
            f"{error} Your answer has not been saved yet, so please submit it again.",
            icon=":material/cloud_off:",
        )
        st.stop()

    st.session_state.attention_failures = failures
    if failures >= config.MAX_ATTENTION_FAILURES:
        try:
            get_store().end_session(st.session_state.session_id, "terminated")
        except StorageError:
            pass  # The answers are already saved; marking the session is best-effort.
        st.session_state.stage = "terminated"
        st.switch_page("app_pages/finished.py")

    st.session_state.position += 1
    if st.session_state.position >= total:
        st.session_state.stage = "survey"
        st.switch_page("app_pages/survey.py")
    st.rerun()
