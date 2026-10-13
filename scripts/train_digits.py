"""Train the handwritten-digit model used for boxed number fields.

    pip install scikit-learn        # training only; not needed to run the app
    python scripts/train_digits.py

Trains a small network on scikit-learn's bundled 8x8 handwritten digits
(augmented with shifts, stroke-weight changes and noise so it copes with
scanned pen strokes) and writes ocr/digit_model.npz, a few KB of weights.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "ocr" / "digit_model.npz"


def augment(images: np.ndarray, labels: np.ndarray, copies: int, rng):
    """Shifted, thickened, thinned and noisy variants of 8x8 digits (0..16)."""
    from scipy.ndimage import grey_dilation, grey_erosion, shift
    out_x, out_y = [images], [labels]
    for _ in range(copies):
        batch = []
        for img in images:
            a = img.reshape(8, 8).astype(float)
            a = shift(a, (rng.uniform(-0.8, 0.8), rng.uniform(-0.8, 0.8)),
                      order=1, mode="constant")
            mode = rng.integers(0, 4)
            if mode == 1:
                a = grey_dilation(a, size=(2, 2))          # a heavier pen
            elif mode == 2:
                a = grey_erosion(a, size=(2, 2)) * 1.2     # a lighter pen
            a = a * rng.uniform(0.8, 1.2) + rng.normal(0, 0.6, a.shape)
            batch.append(np.clip(a, 0, 16).reshape(64))
        out_x.append(np.array(batch))
        out_y.append(labels)
    return np.vstack(out_x), np.concatenate(out_y)


def main() -> int:
    from sklearn.datasets import load_digits
    from sklearn.model_selection import train_test_split
    from sklearn.neural_network import MLPClassifier

    digits = load_digits()
    x_train, x_test, y_train, y_test = train_test_split(
        digits.data, digits.target, test_size=0.25, random_state=7,
        stratify=digits.target)
    rng = np.random.default_rng(7)
    ax, ay = augment(x_train, y_train, copies=6, rng=rng)
    model = MLPClassifier(hidden_layer_sizes=(96,), activation="relu",
                          alpha=1e-3, max_iter=400, random_state=7)
    model.fit(ax / 16.0, ay)
    accuracy = model.score(x_test / 16.0, y_test)
    print(f"held-out accuracy: {accuracy:.3f} on {len(y_test)} digits")
    model.fit(np.vstack([ax, x_test]) / 16.0, np.concatenate([ay, y_test]))
    np.savez(OUT, w1=model.coefs_[0].astype(np.float32),
             b1=model.intercepts_[0].astype(np.float32),
             w2=model.coefs_[1].astype(np.float32),
             b2=model.intercepts_[1].astype(np.float32))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
