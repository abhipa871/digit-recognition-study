import streamlit as st

from study.resources import reset_attempt

stage = st.session_state.stage

if stage == "completed":
    st.title("Thank you!", icon=":material/task_alt:")
    st.success("Your answers were saved. You're all done.", icon=":material/cloud_done:")
    other_version = "version without AI" if st.session_state.condition == "ai" else "AI-assisted version"
    st.write(
        f"Your participant ID is **{st.session_state.user_id}**. If you're also doing the "
        f"{other_version} of this study, please enter the same ID there."
    )
    st.caption(f"Reference code: {st.session_state.session_id[:8]}")
    st.write("You can close this tab now.")

elif stage == "terminated":
    st.title("This attempt has ended", icon=":material/warning:")
    st.warning(
        "Both attention-check images were answered incorrectly, so this attempt was "
        "stopped and won't be included in the results.",
        icon=":material/warning:",
    )
    st.write(
        "If that was a mistake, you're welcome to start over. Please look at each "
        "image carefully before answering."
    )
    if st.button("Start over", type="primary", icon=":material/restart_alt:"):
        reset_attempt()
        st.switch_page("app_pages/instructions.py")

else:
    if stage in ("task", "survey"):
        st.switch_page(f"app_pages/{stage}.py")
    st.switch_page("app_pages/instructions.py")
