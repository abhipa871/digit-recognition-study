import random

import streamlit as st

from study import config
from study.resources import (
    ai_study_accuracy,
    get_model,
    get_store,
    get_trials,
    instruction_examples,
)
from study.storage import StorageError
from study.trials import build_trial_plan, normalize_participant_id

# Someone who is already part-way through is sent back to where they were.
_STAGE_PAGES = {
    "task": "app_pages/task.py",
    "survey": "app_pages/survey.py",
    "completed": "app_pages/finished.py",
    "terminated": "app_pages/finished.py",
}
if st.session_state.stage in _STAGE_PAGES:
    st.switch_page(_STAGE_PAGES[st.session_state.stage])

condition = st.session_state.condition
with_ai = condition == "ai"
n_images = config.TRIALS_PER_DIGIT * 10 + config.ATTENTION_CHECK_COUNT
n_questions = len(config.survey_items(condition))
other_version = "version without AI" if with_ai else "AI-assisted version"

if with_ai:
    try:
        with st.spinner("Loading the AI model..."):
            model = get_model()
    except Exception:  # noqa: BLE001 - any failure here means the AI version can't run
        st.error(
            "The AI model could not be loaded, so this version of the study can't run "
            "right now. Please tell the researcher.",
            icon=":material/error:",
        )
        st.stop()

st.title("Handwritten digit recognition", icon=":material/pin:")
if with_ai:
    st.badge("Version with AI assistance", icon=":material/smart_toy:", color="violet")
else:
    st.badge("Version without AI", icon=":material/person:", color="gray")

st.write(
    f"Thanks for taking part! You'll see **{n_images} small, noisy images of "
    "handwritten digits** and decide which digit (0-9) each one shows. "
    + (
        "In this version of the study, **an AI model suggests an answer for every "
        "image**, and you make the final decision. "
        if with_ai
        else "In this version of the study you work **on your own: there is no AI "
        "assistance**. "
    )
    + f"It takes about **{config.ESTIMATED_MINUTES} minutes**."
)

st.subheader("What the images look like")
st.write(
    "Each image is a tiny 8 × 8 pixel scan of a handwritten digit that has been "
    "degraded on purpose. It may be shifted or rotated, covered in random noise, or "
    "have a small patch erased. Some images are easy and some are genuinely hard. "
    "In each example below, the right-hand image is what you'll see in the task. "
    "These examples are not part of the task itself:"
)
with st.container(horizontal=True, gap="small"):
    for difficulty, example in zip(["Easy", "Medium", "Hard"], instruction_examples()):
        with st.container(border=True, gap="small", width="stretch"):
            st.markdown(f"**{difficulty}**: the answer is **{example['digit']}**")
            with st.container(horizontal=True, gap="small"):
                st.image(example["clean"], caption="Original", width=88)
                st.image(example["degraded"], caption="Degraded", width=88)
            if with_ai:
                # Computed live, exactly as in the task.
                output = model.predict(example["pixels"])
                st.caption(
                    f":material/smart_toy: AI suggested **{output['prediction']}** "
                    f"({output['confidence']:.0%} confident)"
                )

if with_ai:
    accuracy = ai_study_accuracy()
    st.subheader("About the AI")
    st.write(
        "The AI is a small neural network trained on handwritten digits. As soon as "
        "an image appears, it analyzes it and suggests a digit, along with how "
        "confident it is (0-100%). You can also open its top "
        f"{config.AI_TOP_GUESSES} guesses. On images like these it is right about "
        f"**{round(accuracy * 10)} out of 10 times** ({accuracy:.1%}), so it can be "
        "wrong, as in the hard example above. Use its suggestion however you like: "
        "the final answer is always yours."
    )

st.subheader("How to answer")
if with_ai:
    st.markdown(
        "1. Look at the image and the AI's suggestion next to it.\n"
        "2. Click the digit (0-9) you think the image shows: the AI's suggestion or a "
        "different one. You can change your mind until you submit.\n"
        "3. Choose how confident *you* are, from *Guessing* to *Certain*.\n"
        "4. Click **Submit answer**. You can't go back, so check your choice first."
    )
else:
    st.markdown(
        "1. Look at the image.\n"
        "2. Click the digit (0-9) it shows. You can change your mind until you submit.\n"
        "3. Choose how confident you are, from *Guessing* to *Certain*.\n"
        "4. Click **Submit answer**. You can't go back, so check your choice first."
    )

st.subheader("Good to know")
st.markdown(
    "- There's no time limit, but we record how long each answer takes, so please "
    "work steadily and don't step away in the middle.\n"
    "- Every image has exactly one correct digit. If you're unsure, give your best guess.\n"
    "- A couple of images are **attention checks** with an obvious answer.\n"
    f"- At the end you'll answer {n_questions} short questions about how the task felt."
)

st.subheader("Your data")
st.write(
    "We record your answers, confidence ratings, response times and questionnaire "
    "answers under the participant ID you choose below. "
    + (
        "We also record the AI's suggestion for each image and whether you opened "
        "its top guesses. "
        if with_ai
        else ""
    )
    + "We don't collect your name or any other personal information. Taking part is "
    "voluntary, and you can stop at any time by closing this tab."
)

st.space("small")

comprehension_check = config.COMPREHENSION[condition]
with st.form("start_form"):
    st.subheader("Before you start", icon=":material/person:")
    user_id = st.text_input(
        "Choose a participant ID",
        placeholder="For example: ap-27",
        max_chars=40,
        autocomplete="off",
        help="4-40 characters: letters, numbers, hyphens (-) or underscores (_).",
    )
    st.caption(
        f"Please don't use your full name. **If you also do the {other_version} of "
        "this study, use the same ID there** so you aren't shown the same images twice."
    )
    comprehension = st.radio(
        comprehension_check["question"],
        comprehension_check["options"],
        index=None,
    )
    submitted = st.form_submit_button(
        "Start the task",
        type="primary",
        icon=":material/arrow_forward:",
        width="stretch",
    )

if submitted:
    try:
        participant_id = normalize_participant_id(user_id)
    except ValueError as error:
        st.error(str(error), icon=":material/error:")
        st.stop()

    if comprehension is None:
        st.error("Please answer the quick check above.", icon=":material/error:")
        st.stop()

    # Every answered attempt is recorded with the session, including the passing one.
    st.session_state.comprehension_attempts += 1
    if comprehension != comprehension_check["answer"]:
        sections = "**About the AI** and **How to answer**" if with_ai else "**How to answer**"
        st.error(
            f"That's not quite right. Please re-read {sections} above, then try again.",
            icon=":material/error:",
        )
        st.stop()

    try:
        with st.spinner("Preparing your images..."):
            store = get_store()
            store.healthcheck()
            exposure, seen = store.assignment_context(participant_id)
            plan = build_trial_plan(
                get_trials(),
                exposure,
                seen,
                random.Random(),
                trials_per_digit=config.TRIALS_PER_DIGIT,
                attention_checks=config.ATTENTION_CHECK_COUNT,
            )
            session_id = store.start_session(
                participant_id,
                condition,
                plan,
                st.session_state.comprehension_attempts,
                config.APP_VERSION,
            )
    except StorageError as error:
        st.error(str(error), icon=":material/cloud_off:")
        st.stop()

    st.session_state.update(
        stage="task",
        user_id=participant_id,
        session_id=session_id,
        plan=plan,
        position=0,
        attention_failures=0,
        shown_position=None,
    )
    st.switch_page("app_pages/task.py")
