import streamlit as st

from study import config
from study.resources import get_store
from study.storage import StorageError

if st.session_state.stage != "survey":
    if st.session_state.stage == "task":
        st.switch_page("app_pages/task.py")
    if st.session_state.stage in ("completed", "terminated"):
        st.switch_page("app_pages/finished.py")
    st.switch_page("app_pages/instructions.py")

items = config.survey_items(st.session_state.condition)

st.title("Almost done", icon=":material/checklist:")
st.write(
    f"You've answered every image. Please answer {len(items)} quick questions about "
    "the task you just did. There are no right or wrong answers."
)

with st.form("survey_form"):
    answers = {}
    for item in items:
        answers[item["key"]] = st.segmented_control(
            item["question"],
            options=config.SURVEY_SCALE,
            key=f"survey_{item['key']}",
            width="stretch",
        )
        st.caption(f"1 = {item['low']}, 7 = {item['high']}")
    comments = st.text_area(
        "Anything else you'd like to tell us about the task? (optional)",
        max_chars=config.MAX_COMMENT_CHARS,
        placeholder="For example, anything that was confusing or didn't work.",
    )
    submitted = st.form_submit_button(
        "Submit and finish",
        type="primary",
        icon=":material/check:",
        width="stretch",
    )

if submitted:
    if any(value is None for value in answers.values()):
        st.error(f"Please answer all {len(items)} questions (1-7) before submitting.", icon=":material/error:")
        st.stop()
    try:
        with st.spinner("Saving..."):
            get_store().end_session(
                st.session_state.session_id,
                "completed",
                {**answers, "comments": comments},
            )
    except StorageError as error:
        st.error(f"{error} Please submit again.", icon=":material/cloud_off:")
        st.stop()
    st.session_state.stage = "completed"
    st.switch_page("app_pages/finished.py")
