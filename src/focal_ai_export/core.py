from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ExifTags

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif"}
FOCAL_BINS = (("<24", 0, 24), ("24-34", 24, 35), ("35-49", 35, 50),
              ("50-84", 50, 85), ("85-134", 85, 135), ("135-199", 135, 200),
              ("200+", 200, math.inf))


@dataclass
class PhotoRecord:
    source_file: str
    source_group: str
    year: int | None
    month: int | None
    day: int | None
    date_source: str
    camera_model: str
    lens_model: str
    lens_type: str
    focal_actual_mm: float | None
    focal_35mm_mm: float | None
    focal_35mm_source: str
    crop_factor: float | None
    lens_min_actual_mm: float | None
    lens_max_actual_mm: float | None
    zoom_position_log: float | None
    focal_bin_35mm: str | None
    photo_weight: float
    shooting_day_weight: float | None = None
    month_weight: float | None = None


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return float(value[0]) / float(value[1])
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def _text(value) -> str:
    return "" if value is None else str(value).replace("\x00", "").strip()


def _read_exif(path: Path) -> dict[str, object]:
    try:
        with Image.open(path) as image:
            root = image.getexif()
            tags = {ExifTags.TAGS.get(key, key): value for key, value in root.items()}
            tags.update({ExifTags.TAGS.get(key, key): value for key, value in root.get_ifd(ExifTags.IFD.Exif).items()})
            return tags
    except Exception:
        return {}


def _original_date(tags: dict[str, object]) -> datetime | None:
    try:
        return datetime.strptime(_text(tags.get("DateTimeOriginal"))[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def folder_date(path: Path, root: Path) -> tuple[int | None, int | None]:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None, None
    for part in parts:
        match = re.search(r"(?<!\d)(20\d{2})\s*(?:년)?\s*(\d{1,2})\s*월", part)
        if match:
            month = int(match.group(2))
            return int(match.group(1)), month if 1 <= month <= 12 else None
    for part in parts:
        match = re.search(r"(?<!\d)(20\d{2})(?!\d)", part)
        if match:
            return int(match.group(1)), None
    return None, None


def lens_range(tags: dict[str, object]) -> tuple[float | None, float | None]:
    value = tags.get("LensSpecification")
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        low, high = _number(value[0]), _number(value[1])
        if low and high:
            return low, high
    lens = _text(tags.get("LensModel"))
    match = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*mm", lens, re.I)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = re.search(r"(\d+(?:\.\d+)?)\s*mm", lens, re.I)
    if match:
        value = float(match.group(1))
        return value, value
    return None, None


def lens_type(low: float | None, high: float | None) -> str:
    if not low or not high:
        return "unknown"
    return "prime" if math.isclose(low, high, rel_tol=0.001) else "zoom"


def focal_bin(value: float | None) -> str | None:
    if value is None:
        return None
    return next(label for label, low, high in FOCAL_BINS if low <= value < high)


def find_images(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not any(part.startswith(".") for part in path.parts))


def snapshot(paths: Iterable[Path]) -> list[tuple[str, int, int]]:
    return [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]


def scan(root: Path) -> tuple[list[PhotoRecord], dict[str, object]]:
    root = root.resolve()
    preliminary: list[PhotoRecord] = []
    quality: Counter[str] = Counter()
    for path in find_images(root):
        quality["scanned"] += 1
        tags = _read_exif(path)
        if not tags:
            quality["unreadable_or_no_exif"] += 1
        actual = _number(tags.get("FocalLength"))
        equivalent = _number(tags.get("FocalLengthIn35mmFilm"))
        taken = _original_date(tags)
        fallback_year, fallback_month = folder_date(path, root)
        if taken:
            year, month, day, date_source = taken.year, taken.month, taken.day, "DateTimeOriginal"
            if fallback_year and fallback_year != taken.year:
                quality["folder_year_mismatch"] += 1
        else:
            year, month, day, date_source = fallback_year, fallback_month, None, "folder"
            quality["date_fallback"] += 1
        low, high = lens_range(tags)
        category = lens_type(low, high)
        zoom_position = None
        if category == "zoom" and actual and low and high and high > low:
            zoom_position = min(1.0, max(0.0, math.log(actual / low) / math.log(high / low)))
        preliminary.append(PhotoRecord(
            source_file=str(path.relative_to(root)),
            source_group=(path.relative_to(root).parts[0] if len(path.relative_to(root).parts) > 1 else "."),
            year=year, month=month, day=day, date_source=date_source,
            camera_model=_text(tags.get("Model")) or "Unknown camera",
            lens_model=_text(tags.get("LensModel")) or "Unknown lens", lens_type=category,
            focal_actual_mm=actual, focal_35mm_mm=equivalent,
            focal_35mm_source="exif" if equivalent else "missing", crop_factor=None,
            lens_min_actual_mm=low, lens_max_actual_mm=high, zoom_position_log=zoom_position,
            focal_bin_35mm=focal_bin(equivalent), photo_weight=1.0,
        ))
    factors: dict[str, list[float]] = defaultdict(list)
    for record in preliminary:
        if record.focal_actual_mm and record.focal_35mm_mm:
            ratio = record.focal_35mm_mm / record.focal_actual_mm
            if .5 <= ratio <= 10:
                factors[record.camera_model].append(ratio)
    medians = {camera: statistics.median(values) for camera, values in factors.items() if len(values) >= 3}
    for record in preliminary:
        if record.focal_35mm_mm is None and record.focal_actual_mm and record.camera_model in medians:
            record.crop_factor = medians[record.camera_model]
            record.focal_35mm_mm = record.focal_actual_mm * record.crop_factor
            record.focal_35mm_source = "derived_camera_median"
            record.focal_bin_35mm = focal_bin(record.focal_35mm_mm)
            quality["derived_equivalent"] += 1
        if record.focal_35mm_mm is None:
            quality["missing_equivalent"] += 1
        else:
            quality["usable_equivalent"] += 1
    day_groups = Counter((r.year, r.month, r.day) for r in preliminary if r.year and r.month and r.day and r.date_source == "DateTimeOriginal")
    month_groups = Counter((r.year, r.month) for r in preliminary if r.year and r.month)
    for record in preliminary:
        if record.date_source == "DateTimeOriginal" and record.year and record.month and record.day:
            record.shooting_day_weight = 1 / day_groups[(record.year, record.month, record.day)]
        if record.year and record.month:
            record.month_weight = 1 / month_groups[(record.year, record.month)]
    metadata = {
        "quality": dict(quality),
        "camera_crop_factors": {
            camera: {"median_crop_factor": factor, "sample_count": len(factors[camera])}
            for camera, factor in medians.items()
        },
        "focal_bins_35mm": [label for label, _, _ in FOCAL_BINS],
    }
    return preliminary, metadata


def _weighted_quantile(values: list[tuple[float, float]], q: float) -> float | None:
    """Weighted nearest-rank quantile, preserving exact focal-length values."""
    usable = sorted((value, weight) for value, weight in values if weight is not None and weight > 0)
    if not usable:
        return None
    threshold, accumulated = sum(weight for _, weight in usable) * q, 0.0
    for value, weight in usable:
        accumulated += weight
        if accumulated >= threshold:
            return value
    return usable[-1][0]


def _summary_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    """Machine-readable descriptive statistics; it makes no preference claim."""
    rows: list[dict[str, object]] = []
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"))
    for year in sorted({record.year for record in records if record.year is not None}):
        annual = [record for record in records if record.year == year]
        for lens_group in ("all", "prime", "zoom"):
            group = annual if lens_group == "all" else [record for record in annual if record.lens_type == lens_group]
            for weight_name, field in weight_specs:
                values = [(record.focal_35mm_mm, getattr(record, field)) for record in group if record.focal_35mm_mm is not None and getattr(record, field) is not None]
                weighted_n = sum(weight for _, weight in values)
                mean = sum(value * weight for value, weight in values) / weighted_n if weighted_n else None
                rows.append({"year": year, "lens_group": lens_group, "weighting": weight_name,
                             "record_count": len(group), "usable_equivalent_count": len(values), "weighted_sample_size": weighted_n,
                             "mean_35mm_mm": mean, "median_35mm_mm": _weighted_quantile(values, .5),
                             "q1_35mm_mm": _weighted_quantile(values, .25), "q3_35mm_mm": _weighted_quantile(values, .75)})
    return rows


def _exact_focal_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"))
    for year in sorted({record.year for record in records if record.year is not None}):
        for lens_group in ("all", "prime", "zoom"):
            group = [record for record in records if record.year == year and (lens_group == "all" or record.lens_type == lens_group) and record.focal_35mm_mm is not None]
            for weight_name, field in weight_specs:
                counts: defaultdict[float, float] = defaultdict(float)
                for record in group:
                    weight = getattr(record, field)
                    if weight is not None:
                        counts[record.focal_35mm_mm] += weight
                total = sum(counts.values())
                rows.extend({"year": year, "lens_group": lens_group, "weighting": weight_name,
                             "focal_35mm_mm": focal, "weighted_count": count,
                             "weighted_share": count / total if total else None}
                            for focal, count in sorted(counts.items()))
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_dataset(root: Path, output: Path) -> Path:
    root = root.resolve()
    paths = find_images(root)
    before = snapshot(paths)
    records, metadata = scan(root)
    if before != snapshot(paths):
        raise RuntimeError("원본 파일 변화가 감지되어 결과를 저장하지 않았습니다.")
    output.mkdir(parents=True, exist_ok=False)
    fields = list(asdict(records[0]).keys()) if records else list(PhotoRecord.__annotations__.keys())
    _write_csv(output / "photos.csv", [asdict(record) for record in records], fields)
    with (output / "photos.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    dataset = {
        "format": "focal-ai-export/v1", "created_at": datetime.now().isoformat(timespec="seconds"),
        "record_count": len(records), "schema": {name: str(type_) for name, type_ in PhotoRecord.__annotations__.items()},
        "metadata": metadata,
        "privacy": "source_file is relative to the selected folder; absolute paths, GPS, serial numbers, and image pixels are not exported.",
    }
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    summary, exact_focals = _summary_rows(records), _exact_focal_rows(records)
    _write_csv(output / "yearly_summary.csv", summary, list(summary[0]) if summary else ["year", "lens_group", "weighting", "record_count", "usable_equivalent_count", "weighted_sample_size", "mean_35mm_mm", "median_35mm_mm", "q1_35mm_mm", "q3_35mm_mm"])
    _write_csv(output / "exact_focal_usage.csv", exact_focals, list(exact_focals[0]) if exact_focals else ["year", "lens_group", "weighting", "focal_35mm_mm", "weighted_count", "weighted_share"])
    (output / "AI_HANDOFF.md").write_text("""# AI 분석 의뢰용 데이터\n\n`photos.jsonl` 또는 `photos.csv`와 `yearly_summary.csv`, `exact_focal_usage.csv`를 AI에 첨부하세요. 이 데이터는 인사이트를 포함하지 않는 원시·파생 EXIF 데이터셋입니다.\n\n## 권장 요청문\n\n이 데이터에서 연도별 35mm 환산 초점거리 사용 변화, 사진/촬영일/월 가중치의 차이, 단렌즈와 줌렌즈의 차이, 줌 위치를 분석해줘. `focal_35mm_source`가 `missing`인 레코드는 환산 초점거리 통계에서 제외하고, `derived_camera_median`은 별도 표시해줘. 불완전 연도는 `date_source`, `year`, `month`을 확인해 보수적으로 해석해줘.\n\n## 필드\n\n- `focal_35mm_mm`: 35mm 환산 초점거리.\n- `focal_35mm_source`: `exif`, `derived_camera_median`, `missing`.\n- `photo_weight`, `shooting_day_weight`, `month_weight`: 세 관점의 가중치.\n- `lens_type`: `prime`, `zoom`, `unknown`.\n- `zoom_position_log`: 줌 범위에서 로그 기준 위치(0=광각단, 1=망원단).\n- `yearly_summary.csv`: 연도·렌즈군·가중치별 평균, 중앙값, 사분위수.\n- `exact_focal_usage.csv`: 초점거리 구간으로 뭉개지지 않은 정확한 35mm 환산 초점거리별 가중 사용량.\n""", encoding="utf-8")
    return output
