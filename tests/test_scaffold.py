"""Sanity check: every top-level package imports."""
import importlib

import pytest

PACKAGES = ["core", "data", "scanning", "imaging", "separation",
            "qc", "ocr", "export", "delivery", "ui"]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name):
    assert importlib.import_module(name) is not None
