"""Shared app shell. Each entry point (one per condition) calls run() with its condition."""
import streamlit as st

from study.config import CONDITION_NAMES
from study.resources import init_state, storage_backend


def run(condition: str) -> None:
    st.set_page_config(
        page_title="Digit recognition study" + (" (AI)" if condition == "ai" else ""),
        page_icon=":material/pin:",
        layout="centered",
    )

    init_state(condition)

    if storage_backend() == "local":
        st.info(
            f"**Local test mode ({CONDITION_NAMES[condition]}).** Responses are saved to a "
            "SQLite file on this computer, not to Supabase. Do not share this instance "
            "with participants.",
            icon=":material/science:",
        )

    pages = [
        st.Page("app_pages/instructions.py", title="Instructions", icon=":material/menu_book:", default=True),
        st.Page("app_pages/task.py", title="Task", icon=":material/grid_on:"),
        st.Page("app_pages/survey.py", title="Questionnaire", icon=":material/checklist:"),
        st.Page("app_pages/finished.py", title="Finished", icon=":material/check_circle:"),
    ]
    page = st.navigation(pages, position="hidden")
    page.run()
