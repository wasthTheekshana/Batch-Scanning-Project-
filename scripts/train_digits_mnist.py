"""Train the handwritten-digit model on MNIST (70,000 real handwritten digits).

    pip install scikit-learn scipy         # training only; the application needs numpy only
    python scripts/train_digits_mnist.py

The digits are augmented the way scanned form boxes differ from MNIST: heavier and
lighter pens, tilt, slant, and size. Writes ocr/digit_model28.npz (a few hundred KB of
weights run with plain numpy by ocr/digits.py). The 8x8 model it replaces was trained
on 1,797 digits.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
OUT = REPO / "ocr" / "digit_model28.npz"


def augment(images: np.ndarray, rng) -> np.ndarray:
    import cv2
    out = np.empty_like(images)
    for i, flat in enumerate(images):
        a = flat.reshape(28, 28).astype(np.uint8)
        angle = rng.uniform(-14, 14)
        shear = rng.uniform(-0.25, 0.25)
        scale = rng.uniform(0.85, 1.12)
        m = cv2.getRotationMatrix2D((14, 14), angle, scale)
        m[0, 1] += shear
        a = cv2.warpAffine(a, m, (28, 28), flags=cv2.INTER_LINEAR)
        mode = rng.integers(0, 4)
        if mode == 1:
            a = cv2.dilate(a, np.ones((2, 2), np.uint8))
        elif mode == 2:
            a = cv2.erode(a, np.ones((2, 2), np.uint8))
        elif mode == 3:
            a = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
        out[i] = a.reshape(784)
    return out


def main() -> int:
    from sklearn.datasets import fetch_openml
    from sklearn.neural_network import MLPClassifier

    mnist = fetch_openml("mnist_784", version=1, as_frame=False, parser="liac-arff")
    x = mnist.data.astype(np.uint8)
    y = mnist.target.astype(int)
    x_train, y_train, x_test, y_test = x[:60000], y[:60000], x[60000:], y[60000:]
    rng = np.random.default_rng(11)
    ax = np.vstack([x_train, augment(x_train, rng), augment(x_train, rng)])
    ay = np.concatenate([y_train, y_train, y_train])
    model = MLPClassifier(hidden_layer_sizes=(256, 128), activation="relu", alpha=1e-4,
                          max_iter=40, early_stopping=True, n_iter_no_change=4,
                          random_state=11, verbose=True)
    model.fit(ax / 255.0, ay)
    print(f"MNIST test accuracy: {model.score(x_test / 255.0, y_test):.4f}")
    aug_test = augment(x_test, rng)
    print(f"augmented test accuracy: {model.score(aug_test / 255.0, y_test):.4f}")
    np.savez(OUT, w1=model.coefs_[0].astype(np.float32), b1=model.intercepts_[0].astype(np.float32),
             w2=model.coefs_[1].astype(np.float32), b2=model.intercepts_[1].astype(np.float32),
             w3=model.coefs_[2].astype(np.float32), b3=model.intercepts_[2].astype(np.float32))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
