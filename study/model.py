"""The Assignment 2 model, run live inside the study app (synchronous AI output).

`ResidualBlock` and `SmallResNet` are copied unchanged from Assignment2.ipynb, and the
weights are loaded from the same checkpoint the notebook saved
(models/small_resnet_assignment2.pth). Running it on the 200 study images reproduces
the Assignment 2 predictions exactly (see tests/test_model.py).
"""
import hashlib
import io
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Architecture (verbatim from Assignment2.ipynb)
# ---------------------------------------------------------------------------

class ResidualBlock(nn.Module):
    def __init__(self, in_channels, out_channels, stride=1):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_channels)
        self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(out_channels)
        if stride != 1 or in_channels != out_channels:
            self.shortcut = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(out_channels)
            )
        else:
            self.shortcut = nn.Identity()

    def forward(self, x):
        residual = self.shortcut(x)

        x = F.relu(self.bn1(self.conv1(x)))
        x = self.bn2(self.conv2(x))

        x = x + residual
        x = F.relu(x)

        return x


class SmallResNet(nn.Module):
    def __init__(self, num_classes=10):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(32)
        self.layer1 = ResidualBlock(32, 32)
        self.layer2 = ResidualBlock(32, 64, stride=2)
        self.layer3 = ResidualBlock(64, 128, stride=2)
        self.pool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(128, num_classes)

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.pool(x)
        x = torch.flatten(x, 1)
        x = self.fc(x)

        return x


# ---------------------------------------------------------------------------
# Loading and live prediction
# ---------------------------------------------------------------------------

# Besides tensors, the notebook's checkpoint stores the study indices as a NumPy
# array. Allowing exactly these NumPy types lets PyTorch's safe loader
# (weights_only=True) read the original file without unpickling arbitrary code.
_SAFE_NUMPY_GLOBALS = [np.dtype, np.ndarray, np._core.multiarray._reconstruct]
_SAFE_NUMPY_GLOBALS += [
    getattr(np.dtypes, name) for name in ("Int64DType", "Int32DType") if hasattr(np.dtypes, name)
]


class DigitModel:
    """The trained SmallResNet plus a `predict` call for one 8x8 image."""

    def __init__(self, checkpoint_path: Path):
        raw = Path(checkpoint_path).read_bytes()
        # Logged with every AI output, so the data shows exactly which model was used.
        self.version = f"{Path(checkpoint_path).name}#sha256:{hashlib.sha256(raw).hexdigest()[:12]}"
        with torch.serialization.safe_globals(_SAFE_NUMPY_GLOBALS):
            checkpoint = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        self.best_val_accuracy = float(checkpoint.get("best_val_accuracy", float("nan")))
        self.net = SmallResNet(num_classes=10)
        self.net.load_state_dict(checkpoint["model_state_dict"])
        self.net.eval()

    def predict(self, pixels) -> dict:
        """Run the model on one degraded image (8x8, 0-16 scale, as stored in the CSV)."""
        x = torch.as_tensor(np.asarray(pixels, dtype=np.float32).reshape(1, 1, 8, 8))
        start = time.perf_counter()
        with torch.inference_mode():
            probabilities = torch.softmax(self.net(x), dim=1)[0].tolist()
        latency_ms = (time.perf_counter() - start) * 1000
        prediction = int(np.argmax(probabilities))
        return {
            "model_version": self.version,
            "prediction": prediction,
            "confidence": round(probabilities[prediction], 6),
            "probabilities": [round(p, 6) for p in probabilities],
            "latency_ms": round(latency_ms, 3),
        }


def top_guesses(output: dict, k: int = 3) -> list[tuple[int, float]]:
    """The model's k most likely digits with their probabilities, most likely first."""
    ranked = sorted(enumerate(output["probabilities"]), key=lambda pair: pair[1], reverse=True)
    return ranked[:k]
