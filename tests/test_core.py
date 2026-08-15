from pathlib import Path

import pytest

from focal_ai_export.core import (
    PhotoRecord,
    _assign_decision_units,
    _preference_score_rows,
    focal_bin,
    focal_log_cluster,
    folder_date,
    lens_type,
    zoom_position_bin,
)


def test_focal_bins():
    assert focal_bin(24) == "24-34"
    assert focal_bin(200) == "200+"


def test_lens_type():
    assert lens_type(25, 25) == "prime"
    assert lens_type(12, 40) == "zoom"


def test_folder_date():
    root = Path("/photos")
    assert folder_date(Path("/photos/2026/2026 8월/a.jpg"), root) == (2026, 8)


def _record(name: str, captured_at: str, focal: float) -> PhotoRecord:
    return PhotoRecord(
        source_file=name, source_group="2026", year=2026, month=1, day=1,
        date_source="DateTimeOriginal", captured_at=captured_at,
        camera_model="Camera", lens_model="Lens", lens_type="prime",
        focal_actual_mm=focal, focal_35mm_mm=focal, focal_35mm_source="exif",
        crop_factor=None, lens_min_actual_mm=focal, lens_max_actual_mm=focal,
        zoom_position_log=None, zoom_position_bin=None,
        is_exact_zoom_wide_end=None, is_exact_zoom_tele_end=None,
        focal_bin_35mm=focal_bin(focal), focal_log_cluster_mm=focal_log_cluster(focal),
        photo_weight=1.0,
    )


def test_session_and_burst_weights_compress_continuous_shots():
    records = [
        _record("a.jpg", "2026-01-01T10:00:00", 50),
        _record("b.jpg", "2026-01-01T10:00:03", 50),
        _record("c.jpg", "2026-01-01T10:10:00", 85),
        _record("d.jpg", "2026-01-01T12:00:01", 85),
    ]
    metadata = _assign_decision_units(records, session_gap_minutes=90, burst_gap_seconds=5)

    assert metadata == {"session_count": 2, "burst_count": 3, "records_without_timestamp": 0}
    assert records[0].burst_id == records[1].burst_id
    assert records[1].burst_id != records[2].burst_id
    assert sum(record.session_weight for record in records[:3]) == pytest.approx(1)
    assert records[0].burst_weight + records[1].burst_weight == pytest.approx(1)
    assert records[3].session_weight == 1


def test_log_cluster_and_zoom_position_boundaries():
    assert abs(focal_log_cluster(50) / 50 - 1) < .06
    assert zoom_position_bin(.1) == "wide_side"
    assert zoom_position_bin(.9) == "tele_end"


def test_final_edit_preference_score_uses_normalized_50_30_20_components():
    records = [
        _record("a.jpg", "2026-01-01T10:00:00", 50),
        _record("b.jpg", "2026-01-01T10:00:03", 50),
        _record("c.jpg", "2026-01-02T10:00:00", 85),
    ]
    records[0].shooting_day_weight = records[1].shooting_day_weight = .5
    records[2].shooting_day_weight = 1
    for record in records:
        record.month_weight = 1 / 3

    rows = _preference_score_rows(records)
    focal_50 = next(row for row in rows if row["lens_group"] == "all" and row["representation"] == "exact" and row["focal_35mm_mm"] == 50)

    assert focal_50["photo_share"] == pytest.approx(2 / 3)
    assert focal_50["shooting_day_share"] == pytest.approx(.5)
    assert focal_50["month_share"] == pytest.approx(2 / 3)
    assert focal_50["composite_score_50_30_20"] == pytest.approx(.5 * (2 / 3) + .3 * .5 + .2 * (2 / 3))


def test_export_dataset_outputs_and_handoff(tmp_path):
    from focal_ai_export.core import export_dataset

    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()
    out_dir = tmp_path / "output"

    export_dataset(photo_dir, out_dir)

    handoff = out_dir / "AI_HANDOFF.md"
    assert handoff.exists()
    content = handoff.read_text(encoding="utf-8")
    assert "AI 분석 의뢰 가이드" in content
    assert "preference_score.csv" in content
    assert "단렌즈 영입 고민" in content

