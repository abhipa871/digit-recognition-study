"""Cached resources and session-state helpers used by the pages."""
import os

import streamlit as st

from study.config import (
    CONDITIONS,
    IMAGE_DISPLAY_PX,
    INSTRUCTION_EXAMPLES_JSON,
    MODEL_CHECKPOINT,
    TRIALS_CSV,
)
from study.storage import LocalStore, StorageError, SupabaseStore
from study.trials import (
    ATTENTION_CHECK_IMAGES,
    load_instruction_examples,
    load_trials,
    render_digit_png,
    trial_pixels,
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def get_trials():
    return load_trials(TRIALS_CSV)


@st.cache_data(show_spinner=False, max_entries=300)
def trial_image(trial_id: int) -> bytes:
    return render_digit_png(trial_pixels(get_trials(), trial_id), size=IMAGE_DISPLAY_PX)


@st.cache_data(show_spinner=False)
def attention_check_image(digit: int) -> bytes:
    return render_digit_png(ATTENTION_CHECK_IMAGES[digit], size=IMAGE_DISPLAY_PX)


@st.cache_data(show_spinner=False)
def instruction_examples() -> list[dict]:
    return [
        {
            "digit": example["digit"],
            "pixels": example["degraded"],
            "clean": render_digit_png(example["clean"], size=128, frame=8),
            "degraded": render_digit_png(example["degraded"], size=128, frame=8),
        }
        for example in load_instruction_examples(INSTRUCTION_EXAMPLES_JSON)
    ]


def plan_item_pixels(item: dict):
    """The exact 8x8 values shown to the participant (and given to the model)."""
    if item["is_attention_check"]:
        return ATTENTION_CHECK_IMAGES[item["expected_digit"]]
    return trial_pixels(get_trials(), item["trial_id"])


def plan_item_image(item: dict) -> bytes:
    if item["is_attention_check"]:
        return attention_check_image(item["expected_digit"])
    return trial_image(item["trial_id"])


@st.cache_data(show_spinner=False)
def ai_study_accuracy() -> float:
    """The model's accuracy on the 200 study trials (70.5% in Assignment 2)."""
    return float(get_trials()["model_correct"].mean())


# ---------------------------------------------------------------------------
# The live AI model (AI condition only; torch is imported only when needed)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def get_model():
    import torch

    from study.model import DigitModel

    torch.set_num_threads(1)  # tiny model; avoid oversubscribing a shared CPU
    return DigitModel(MODEL_CHECKPOINT)


# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------

def _setting(section: str, key: str, env: str, default=None):
    try:
        value = st.secrets.get(section, {}).get(key)
    except (FileNotFoundError, KeyError, AttributeError):
        value = None
    return value or os.getenv(env) or default


def storage_backend() -> str:
    """"supabase" (default, used when deployed) or "local" (explicit opt-in)."""
    return str(_setting("study", "backend", "DIGIT_STUDY_BACKEND", "supabase")).lower()


@st.cache_resource(show_spinner=False)
def get_store():
    backend = storage_backend()
    if backend == "local":
        path = _setting("study", "local_db_path", "DIGIT_STUDY_LOCAL_DB", "local_study.db")
        trials = get_trials()
        return LocalStore(path, dict(zip(trials["trial_id"], trials["ground_truth"])))
    if backend != "supabase":
        raise StorageError(f'Unknown storage backend "{backend}".')

    url = _setting("supabase", "url", "SUPABASE_URL")
    key = _setting("supabase", "publishable_key", "SUPABASE_PUBLISHABLE_KEY") or _setting(
        "supabase", "anon_key", "SUPABASE_ANON_KEY"
    )
    if not url or not key:
        raise StorageError(
            "The study database is not configured. Add the Supabase URL and "
            "publishable key to the app's secrets."
        )
    return SupabaseStore(url, key)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

STATE_DEFAULTS = {
    # Flow: "instructions" -> "task" -> "survey" -> "completed" (or "terminated")
    "stage": "instructions",
    "user_id": None,
    "session_id": None,
    "plan": None,
    "position": 0,
    "comprehension_attempts": 0,
    "attention_failures": 0,
    # Per-trial bookkeeping, reset whenever a new image is shown
    "shown_position": None,
    "trial_started": None,
    "shown_at": None,
    "digit": None,
    "first_choice_at": None,
    "digit_changes": 0,
    # AI condition: the live model output shown for the current image
    "ai_output": None,
    "ai_top_guesses_open": False,
}


def init_state(condition: str) -> None:
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition {condition!r}")
    for key, value in STATE_DEFAULTS.items():
        st.session_state.setdefault(key, value)
    # Set by the entry point on every run; each deployment serves one condition.
    st.session_state.condition = condition


def reset_attempt() -> None:
    """Start over from the instructions (keeps the participant ID for convenience)."""
    user_id = st.session_state.get("user_id")
    for key, value in STATE_DEFAULTS.items():
        st.session_state[key] = value
    st.session_state.user_id = user_id
