from focal_ai_export.core import focal_bin, folder_date, lens_type
from pathlib import Path


def test_focal_bins():
    assert focal_bin(24) == "24-34"
    assert focal_bin(200) == "200+"


def test_lens_type():
    assert lens_type(25, 25) == "prime"
    assert lens_type(12, 40) == "zoom"


def test_folder_date():
    root = Path("/photos")
    assert folder_date(Path("/photos/2026/2026 8월/a.jpg"), root) == (2026, 8)
