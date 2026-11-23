"""Build the example images shown on the instructions page.

The examples are NOT study trials: they come from the training/validation part of
scikit-learn's digits dataset (never from the 200 held-out study images) and are
degraded with exactly the same procedure as Assignment2.ipynb (`degrade_batch`).

Usage (dev-time only; needs torch, torchvision and scikit-learn):

    python tools/make_instruction_examples.py --preview preview.png
    python tools/make_instruction_examples.py --pick 1203 77 1530

`--preview` writes a grid of candidate images (with their source index) so you can
choose easy and hard examples. `--pick` writes assets/instruction_examples.json.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split
from torchvision import transforms

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "assets" / "instruction_examples.json"

STUDY_SPLIT_SEED = 2026      # same split as Assignment2.ipynb
EXAMPLE_DEGRADATION_SEED = 4242  # different from the validation (123) and study (909) seeds

geom_transform = transforms.Compose([
    transforms.RandomRotation(20),
    transforms.RandomAffine(degrees=0, translate=(0.20, 0.20)),
])


def degrade_batch(images, noise_std=2.3, occlusion_prob=0.60, seed=None):
    """Copied from Assignment2.ipynb so the examples match the study images."""
    if seed is not None:
        rng_state = torch.random.get_rng_state()
        torch.manual_seed(seed)
    out = torch.stack([geom_transform(img) for img in images])
    out = out + torch.randn_like(out) * noise_std
    for i in range(len(out)):
        if torch.rand(1).item() < occlusion_prob:
            size = int(torch.randint(1, 3, (1,)).item())
            row = int(torch.randint(0, 9 - size, (1,)).item())
            col = int(torch.randint(0, 9 - size, (1,)).item())
            out[i, :, row:row + size, col:col + size] = 0
    out = torch.clamp(out, 0, 16)
    if seed is not None:
        torch.random.set_rng_state(rng_state)
    return out


def non_study_examples():
    dataset = load_digits()
    data = torch.from_numpy(dataset["data"]).reshape(-1, 1, 8, 8).float()
    targets = dataset["target"]
    all_indices = np.arange(len(data))
    train_val_idx, study_idx = train_test_split(
        all_indices, test_size=200, random_state=STUDY_SPLIT_SEED, stratify=targets
    )
    assert len(set(train_val_idx) & set(study_idx)) == 0
    degraded = degrade_batch(data[train_val_idx], seed=EXAMPLE_DEGRADATION_SEED)
    return {
        int(src): {
            "source_index": int(src),
            "digit": int(targets[src]),
            "clean": np.round(data[src, 0].numpy(), 3).tolist(),
            "degraded": np.round(degraded[k, 0].numpy(), 3).tolist(),
        }
        for k, src in enumerate(train_val_idx)
    }, set(int(i) for i in study_idx)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--pick", type=int, nargs="+")
    args = parser.parse_args()

    examples, study_idx = non_study_examples()

    if args.preview:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        keys = sorted(examples)[:60]
        fig, axes = plt.subplots(6, 10, figsize=(14, 9))
        for ax, key in zip(axes.flat, keys):
            ex = examples[key]
            ax.imshow(np.array(ex["degraded"]), cmap="gray", vmin=0, vmax=16)
            ax.set_title(f"#{key}: {ex['digit']}", fontsize=8)
            ax.axis("off")
        fig.tight_layout()
        fig.savefig(args.preview, dpi=90)
        print("wrote", args.preview)

    if args.pick:
        picked = []
        for key in args.pick:
            if key in study_idx or key not in examples:
                raise SystemExit(f"{key} is a study trial or not a valid index")
            picked.append(examples[key])
        OUT_JSON.write_text(json.dumps(picked, indent=1))
        print("wrote", OUT_JSON, [(p["source_index"], p["digit"]) for p in picked])


if __name__ == "__main__":
    main()
