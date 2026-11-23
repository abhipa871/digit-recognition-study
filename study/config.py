"""Study settings shared by the pages, the storage layer and the tests."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRIALS_CSV = ROOT / "data" / "study_dataset_200_trials.csv"
INSTRUCTION_EXAMPLES_JSON = ROOT / "assets" / "instruction_examples.json"
MODEL_CHECKPOINT = ROOT / "models" / "small_resnet_assignment2.pth"

# Two deployments of the same code: streamlit_app.py runs "no_ai" (baseline) and
# streamlit_app_ai.py runs "ai" (live model suggestions). Both write to the same
# database, tagged with the condition.
CONDITIONS = ("no_ai", "ai")
CONDITION_NAMES = {"no_ai": "without AI", "ai": "with AI assistance"}
APP_VERSION = "2.0"

# 2 trials per digit x 10 digits = 20 study trials, plus the attention checks.
TRIALS_PER_DIGIT = 2
ATTENTION_CHECK_COUNT = 2
# Participants are only stopped if they fail every attention check (tolerant policy).
MAX_ATTENTION_FAILURES = ATTENTION_CHECK_COUNT
ESTIMATED_MINUTES = 5

IMAGE_DISPLAY_PX = 256

CONFIDENCE_LABELS = {
    1: "Guessing",
    2: "Unsure",
    3: "Somewhat sure",
    4: "Fairly sure",
    5: "Certain",
}

# Short NASA-TLX (raw TLX without physical demand), 7-point scales.
# Note: "performance" is worded so that 7 = best; reverse it (8 - x) when
# computing an overall workload score.
SURVEY_ITEMS = [
    {
        "key": "mental_demand",
        "question": "How mentally demanding was the task?",
        "low": "Very low",
        "high": "Very high",
    },
    {
        "key": "temporal_demand",
        "question": "How hurried or rushed was the pace of the task?",
        "low": "Very low",
        "high": "Very high",
    },
    {
        "key": "performance",
        "question": "How successful were you at identifying the digits?",
        "low": "Not at all",
        "high": "Completely",
    },
    {
        "key": "effort",
        "question": "How hard did you have to work to reach your level of performance?",
        "low": "Very low",
        "high": "Very high",
    },
    {
        "key": "frustration",
        "question": "How insecure, discouraged, irritated, stressed, or annoyed were you?",
        "low": "Very low",
        "high": "Very high",
    },
]
# Asked only in the AI condition, after the workload items.
AI_SURVEY_ITEMS = [
    {
        "key": "ai_trust",
        "question": "How much did you trust the AI's suggestions?",
        "low": "Not at all",
        "high": "Completely",
    },
    {
        "key": "ai_helpfulness",
        "question": "How helpful were the AI's suggestions?",
        "low": "Not at all",
        "high": "Extremely",
    },
]
SURVEY_KEYS = [item["key"] for item in SURVEY_ITEMS]
AI_SURVEY_KEYS = [item["key"] for item in AI_SURVEY_ITEMS]
SURVEY_SCALE = list(range(1, 8))
MAX_COMMENT_CHARS = 1000


def survey_items(condition: str) -> list[dict]:
    return SURVEY_ITEMS + (AI_SURVEY_ITEMS if condition == "ai" else [])


# One comprehension question per condition: [question, options, correct option].
COMPREHENSION = {
    "no_ai": {
        "question": "Quick check: what will you do for each image?",
        "options": [
            "Describe what the image looks like in words",
            "Pick the digit (0-9) it shows and rate how confident you are",
            "Decide whether an AI's answer is right or wrong",
            "Draw the digit yourself",
        ],
        "answer": "Pick the digit (0-9) it shows and rate how confident you are",
    },
    "ai": {
        "question": "Quick check: what does the AI do in this task?",
        "options": [
            "It answers for me automatically",
            "It suggests a digit, and I make the final decision",
            "It checks my answers after I submit them",
            "There is no AI in this task",
        ],
        "answer": "It suggests a digit, and I make the final decision",
    },
}

# Follow-up interactions with the AI that are logged (see digit_ai_interactions).
AI_ACTIONS = ("top_guesses_opened",)
AI_TOP_GUESSES = 3
