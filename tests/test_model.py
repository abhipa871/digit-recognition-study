"""The live model in the app is the Assignment 2 model: same weights, same outputs."""
import hashlib

import numpy as np
import pytest

from study import config
from study.model import DigitModel, top_guesses
from study.storage import validate_ai_output
from study.trials import ATTENTION_CHECK_IMAGES, load_trials, trial_pixels


@pytest.fixture(scope="module")
def model():
    return DigitModel(config.MODEL_CHECKPOINT)


@pytest.fixture(scope="module")
def trials():
    return load_trials(config.TRIALS_CSV)


def test_checkpoint_is_the_assignment_2_file(model):
    digest = hashlib.sha256(config.MODEL_CHECKPOINT.read_bytes()).hexdigest()
    assert digest.startswith("027a1ffbeff7")
    assert model.version.endswith(digest[:12])
    assert model.best_val_accuracy == pytest.approx(0.665625)  # 66.6% reported in A2


def test_live_predictions_reproduce_assignment_2(model, trials):
    outputs = [model.predict(trial_pixels(trials, t)) for t in trials["trial_id"]]
    predictions = np.array([o["prediction"] for o in outputs])
    confidences = np.array([o["confidence"] for o in outputs])
    assert (predictions == trials["model_prediction"].to_numpy()).all()
    assert np.abs(confidences - trials["model_confidence"].to_numpy()).max() < 1e-5
    assert (predictions == trials["ground_truth"].to_numpy()).mean() == pytest.approx(0.705)


def test_outputs_are_valid_for_logging(model, trials):
    output = model.predict(trial_pixels(trials, 1))
    validate_ai_output(output)  # raises if malformed
    assert output["latency_ms"] >= 0
    guesses = top_guesses(output, 3)
    assert guesses[0][0] == output["prediction"]
    assert guesses[0][1] >= guesses[1][1] >= guesses[2][1]


@pytest.mark.parametrize("digit", sorted(ATTENTION_CHECK_IMAGES))
def test_ai_reads_attention_checks_correctly(model, digit):
    # Otherwise a participant who simply followed the AI could be stopped as
    # "inattentive", which would wrongly remove over-reliant participants.
    assert model.predict(ATTENTION_CHECK_IMAGES[digit])["prediction"] == digit
