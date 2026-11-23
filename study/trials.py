"""Trial data, per-participant trial assignment, and image rendering.

Everything here is plain Python (no Streamlit), so it can be unit-tested directly.
"""
import io
import json
import random
import re
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageOps

PIXEL_COLUMNS = [f"pixel_{i}" for i in range(64)]
PARTICIPANT_ID_PATTERN = re.compile(r"^[a-z0-9_-]{4,40}$")

# Crisp, noise-free 8x8 digits used as attention checks. Any attentive participant
# can read these, unlike the degraded study images.
# A one-pixel black margin keeps the strokes off the image edge, so the white
# strokes never blend into the page background.
_ATTENTION_CHECK_ROWS = {
    0: [
        "........",
        "..####..",
        ".#....#.",
        ".#....#.",
        ".#....#.",
        ".#....#.",
        "..####..",
        "........",
    ],
    4: [
        "........",
        ".#...#..",
        ".#...#..",
        ".######.",
        ".....#..",
        ".....#..",
        ".....#..",
        "........",
    ],
    7: [
        "........",
        ".######.",
        "......#.",
        ".....#..",
        "....#...",
        "...#....",
        "...#....",
        "........",
    ],
}
ATTENTION_CHECK_IMAGES = {
    digit: np.array([[16.0 if c == "#" else 0.0 for c in row] for row in rows])
    for digit, rows in _ATTENTION_CHECK_ROWS.items()
}


def normalize_participant_id(raw: str) -> str:
    """Lower-case and validate a self-chosen participant ID.

    Lower-casing means "AP-27" and "ap-27" are the same participant in both
    versions of the study.
    """
    value = (raw or "").strip().lower()
    if not PARTICIPANT_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "Use 4-40 characters: letters, numbers, hyphens (-) or underscores (_), "
            "with no spaces."
        )
    return value


def load_trials(csv_path: Path) -> pd.DataFrame:
    """Load the 200 study trials (degraded pixels + ground truth) from Assignment 2."""
    frame = pd.read_csv(csv_path)
    missing = {"trial_id", "ground_truth", *PIXEL_COLUMNS} - set(frame.columns)
    if missing:
        raise ValueError(f"Trial file is missing columns: {sorted(missing)}")
    if frame["trial_id"].duplicated().any():
        raise ValueError("Trial file has duplicate trial_id values.")
    return frame.set_index("trial_id", drop=False)


def trial_pixels(trials: pd.DataFrame, trial_id: int) -> np.ndarray:
    return trials.loc[trial_id, PIXEL_COLUMNS].to_numpy(dtype=float).reshape(8, 8)


def render_digit_png(pixels, size: int = 256, frame: int = 16) -> bytes:
    """Render an 8x8 image on the 0-16 scale as a crisp, enlarged PNG.

    White strokes on black match how the images appear in the Assignment 2 report.
    Nearest-neighbour scaling keeps the pixels sharp instead of smoothing them,
    so participants see exactly what the model sees. A black frame of `frame`
    pixels separates strokes at the image edge from the white page.
    """
    inner = size - 2 * frame
    if inner <= 0 or inner % 8:
        raise ValueError("size - 2 * frame must be a positive multiple of 8")
    array = np.clip(np.asarray(pixels, dtype=float).reshape(8, 8), 0, 16)
    image = Image.fromarray(np.round(array / 16 * 255).astype(np.uint8))  # 2-D uint8 -> grayscale
    image = image.resize((inner, inner), Image.Resampling.NEAREST)
    image = ImageOps.expand(image, border=frame, fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def load_instruction_examples(json_path: Path) -> list[dict]:
    return json.loads(Path(json_path).read_text())


def build_trial_plan(
    trials: pd.DataFrame,
    exposure: dict[int, int],
    seen: set[int],
    rng: random.Random,
    trials_per_digit: int = 2,
    attention_checks: int = 2,
) -> list[dict]:
    """Choose this participant's trials and their order.

    - Stratified: `trials_per_digit` trials of every digit, so each participant sees
      the same mix of digits.
    - Never repeats a trial this participant ID has already seen (for example in
      the other condition), as long as unseen trials remain.
    - Among unseen trials, prefers those shown to the fewest participants so far, so
      the 200 trials are covered evenly; ties are broken at random.
    - Attention checks are inserted at random positions away from the start and end.
    """
    chosen: list[dict] = []
    for digit in range(10):
        pool = trials.loc[trials["ground_truth"] == digit, "trial_id"].astype(int).tolist()
        if len(pool) < trials_per_digit:
            raise ValueError(f"Not enough trials of digit {digit}.")
        ranked = sorted(
            pool,
            key=lambda trial_id: (trial_id in seen, exposure.get(trial_id, 0), rng.random()),
        )
        chosen.extend(
            {"trial_id": trial_id, "is_attention_check": False, "expected_digit": digit}
            for trial_id in ranked[:trials_per_digit]
        )
    rng.shuffle(chosen)

    check_digits = rng.sample(sorted(ATTENTION_CHECK_IMAGES), k=attention_checks)
    total = len(chosen) + attention_checks
    # Spread the checks out: one per equal-sized segment of the task, never in the
    # first three or last positions.
    segment = (total - 4) / attention_checks
    for k, digit in enumerate(check_digits):
        low = 3 + int(round(k * segment))
        high = 3 + int(round((k + 1) * segment)) - 1
        index = rng.randint(low, max(low, high))
        chosen.insert(
            index,
            {"trial_id": None, "is_attention_check": True, "expected_digit": digit},
        )

    return [{"position": position, **item} for position, item in enumerate(chosen, start=1)]
