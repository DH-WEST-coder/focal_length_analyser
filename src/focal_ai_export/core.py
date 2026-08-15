from __future__ import annotations

import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ExifTags

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".heif"}
FOCAL_BINS = (("<24", 0, 24), ("24-34", 24, 35), ("35-49", 35, 50),
              ("50-84", 50, 85), ("85-134", 85, 135), ("135-199", 135, 200),
              ("200+", 200, math.inf))
ZOOM_POSITION_BINS = (("wide_end", 0, .1), ("wide_side", .1, .35),
                      ("middle", .35, .65), ("tele_side", .65, .9),
                      ("tele_end", .9, 1.0000001))
LOG_CLUSTER_STEPS_PER_STOP = 6  # Half-bin width is about ±5.9%.


@dataclass
class PhotoRecord:
    source_file: str
    source_group: str
    year: int | None
    month: int | None
    day: int | None
    date_source: str
    captured_at: str | None
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
    zoom_position_bin: str | None
    is_exact_zoom_wide_end: bool | None
    is_exact_zoom_tele_end: bool | None
    focal_bin_35mm: str | None
    focal_log_cluster_mm: float | None
    photo_weight: float
    shooting_day_weight: float | None = None
    month_weight: float | None = None
    burst_id: str | None = None
    burst_weight: float | None = None
    session_id: str | None = None
    session_weight: float | None = None


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


def focal_log_cluster(value: float | None) -> float | None:
    """Nearest 1/6-stop focal cluster; a bin is approximately ±5.9% wide."""
    if value is None or value <= 0:
        return None
    return round(2 ** (round(math.log2(value) * LOG_CLUSTER_STEPS_PER_STOP) / LOG_CLUSTER_STEPS_PER_STOP), 3)


def zoom_position_bin(value: float | None) -> str | None:
    if value is None:
        return None
    return next(label for label, low, high in ZOOM_POSITION_BINS if low <= value < high)


def _same_burst_choice(previous: PhotoRecord, current: PhotoRecord) -> bool:
    if previous.lens_model != current.lens_model:
        return False
    if previous.focal_actual_mm is not None and current.focal_actual_mm is not None:
        return math.isclose(previous.focal_actual_mm, current.focal_actual_mm, rel_tol=.001)
    if previous.focal_35mm_mm is not None and current.focal_35mm_mm is not None:
        return math.isclose(previous.focal_35mm_mm, current.focal_35mm_mm, rel_tol=.001)
    return False


def _assign_decision_units(records: list[PhotoRecord], session_gap_minutes: int, burst_gap_seconds: int) -> dict[str, int]:
    """Assign sessions and compressed burst decisions using DateTimeOriginal only."""
    dated: list[tuple[datetime, PhotoRecord]] = []
    for record in records:
        if record.captured_at:
            dated.append((datetime.fromisoformat(record.captured_at), record))
    dated.sort(key=lambda item: (item[0], item[1].source_file))
    sessions: list[list[tuple[datetime, PhotoRecord]]] = []
    for item in dated:
        if not sessions or (item[0] - sessions[-1][-1][0]).total_seconds() > session_gap_minutes * 60:
            sessions.append([item])
        else:
            sessions[-1].append(item)
    burst_total = 0
    for session_number, session in enumerate(sessions, start=1):
        session_id = f"session_{session_number:05d}"
        bursts: list[list[tuple[datetime, PhotoRecord]]] = []
        for item in session:
            if (bursts and (item[0] - bursts[-1][-1][0]).total_seconds() <= burst_gap_seconds
                    and _same_burst_choice(bursts[-1][-1][1], item[1])):
                bursts[-1].append(item)
            else:
                bursts.append([item])
        for burst_number, burst in enumerate(bursts, start=1):
            burst_id = f"{session_id}_burst_{burst_number:04d}"
            per_photo_burst = 1 / len(burst)
            per_photo_session = 1 / (len(bursts) * len(burst))
            for _, record in burst:
                record.session_id = session_id
                record.burst_id = burst_id
                record.burst_weight = per_photo_burst
                record.session_weight = per_photo_session
        burst_total += len(bursts)
    return {"session_count": len(sessions), "burst_count": burst_total, "records_without_timestamp": len(records) - len(dated)}


def find_images(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not any(part.startswith(".") for part in path.parts))


def snapshot(paths: Iterable[Path]) -> list[tuple[str, int, int]]:
    return [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]


def scan(root: Path, session_gap_minutes: int = 90, burst_gap_seconds: int = 5) -> tuple[list[PhotoRecord], dict[str, object]]:
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
            captured_at=taken.isoformat(timespec="seconds") if taken else None,
            camera_model=_text(tags.get("Model")) or "Unknown camera",
            lens_model=_text(tags.get("LensModel")) or "Unknown lens", lens_type=category,
            focal_actual_mm=actual, focal_35mm_mm=equivalent,
            focal_35mm_source="exif" if equivalent else "missing", crop_factor=None,
            lens_min_actual_mm=low, lens_max_actual_mm=high, zoom_position_log=zoom_position,
            zoom_position_bin=zoom_position_bin(zoom_position),
            is_exact_zoom_wide_end=(math.isclose(actual, low, rel_tol=.005) if category == "zoom" and actual and low else None),
            is_exact_zoom_tele_end=(math.isclose(actual, high, rel_tol=.005) if category == "zoom" and actual and high else None),
            focal_bin_35mm=focal_bin(equivalent), focal_log_cluster_mm=focal_log_cluster(equivalent), photo_weight=1.0,
        ))
    factors: dict[str, list[float]] = defaultdict(list)
    for record in preliminary:
        if record.camera_model != "Unknown camera" and record.focal_actual_mm and record.focal_35mm_mm:
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
            record.focal_log_cluster_mm = focal_log_cluster(record.focal_35mm_mm)
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
    decision_metadata = _assign_decision_units(preliminary, session_gap_minutes=session_gap_minutes, burst_gap_seconds=burst_gap_seconds)
    metadata = {
        "quality": dict(quality),
        "camera_crop_factors": {
            camera: {"median_crop_factor": factor, "sample_count": len(factors[camera])}
            for camera, factor in medians.items()
        },
        "focal_bins_35mm": [label for label, _, _ in FOCAL_BINS],
        "zoom_position_bins": [label for label, _, _ in ZOOM_POSITION_BINS],
        "decision_units": {"session_gap_minutes": session_gap_minutes, "burst_gap_seconds": burst_gap_seconds, **decision_metadata},
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
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"), ("session", "session_weight"), ("burst", "burst_weight"))
    for year in sorted({record.year for record in records if record.year is not None}):
        annual = [record for record in records if record.year == year]
        for lens_group in ("all", "prime", "zoom"):
            group = annual if lens_group == "all" else [record for record in annual if record.lens_type == lens_group]
            for weight_name, field in weight_specs:
                values = [(record.focal_35mm_mm, getattr(record, field)) for record in group if record.focal_35mm_mm is not None and getattr(record, field) is not None]
                weighted_n = sum(weight for _, weight in values)
                mean = sum(value * weight for value, weight in values) / weighted_n if weighted_n else None
                log_mean = sum(math.log2(value) * weight for value, weight in values) / weighted_n if weighted_n else None
                q1, median, q3 = _weighted_quantile(values, .25), _weighted_quantile(values, .5), _weighted_quantile(values, .75)
                rows.append({"year": year, "lens_group": lens_group, "weighting": weight_name,
                             "record_count": len(group), "usable_equivalent_count": len(values), "weighted_sample_size": weighted_n,
                             "mean_35mm_mm": mean, "geometric_mean_35mm_mm": 2 ** log_mean if log_mean is not None else None,
                             "mean_log2_focal": log_mean, "median_35mm_mm": median,
                             "q1_35mm_mm": q1, "q3_35mm_mm": q3,
                             "iqr_log2_stops": math.log2(q3 / q1) if q1 and q3 else None})
    return rows


def _exact_focal_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"), ("session", "session_weight"), ("burst", "burst_weight"))
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
                ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                rows.extend({"year": year, "lens_group": lens_group, "weighting": weight_name,
                             "rank": rank, "focal_35mm_mm": focal, "weighted_count": count,
                             "weighted_share": count / total if total else None}
                            for rank, (focal, count) in enumerate(ranked, start=1))
    return rows


def _log_cluster_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"), ("session", "session_weight"), ("burst", "burst_weight"))
    for year in sorted({record.year for record in records if record.year is not None}):
        for lens_group in ("all", "prime", "zoom"):
            group = [record for record in records if record.year == year and record.focal_log_cluster_mm is not None and (lens_group == "all" or record.lens_type == lens_group)]
            for weight_name, field in weight_specs:
                counts: defaultdict[float, float] = defaultdict(float)
                for record in group:
                    weight = getattr(record, field)
                    if weight is not None:
                        counts[record.focal_log_cluster_mm] += weight
                total = sum(counts.values())
                ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                rows.extend({"year": year, "lens_group": lens_group, "weighting": weight_name,
                             "rank": rank, "log_cluster_center_35mm_mm": focal, "weighted_count": count,
                             "weighted_share": count / total if total else None}
                            for rank, (focal, count) in enumerate(ranked, start=1))
    return rows


def _monthly_quality_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year, month in sorted({(r.year, r.month) for r in records if r.year and r.month}):
        group = [r for r in records if r.year == year and r.month == month]
        dated_days = {(r.year, r.month, r.day) for r in group if r.date_source == "DateTimeOriginal" and r.day}
        sessions = {r.session_id for r in group if r.session_id}
        usable = sum(r.focal_35mm_mm is not None for r in group)
        exif = sum(r.focal_35mm_source == "exif" for r in group)
        derived = sum(r.focal_35mm_source == "derived_camera_median" for r in group)
        rows.append({"year": year, "month": month, "photo_count": len(group), "usable_equivalent_count": usable,
                     "usable_equivalent_ratio": usable / len(group) if group else None, "exif_equivalent_count": exif,
                     "derived_equivalent_count": derived, "datetimeoriginal_count": sum(r.date_source == "DateTimeOriginal" for r in group),
                     "shooting_day_count": len(dated_days), "session_count": len(sessions),
                     "active_month": len(dated_days) >= 3 or len(group) >= 50})
    return rows


def _zoom_position_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    weight_specs = (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight"), ("session", "session_weight"), ("burst", "burst_weight"))
    for year in sorted({record.year for record in records if record.year is not None}):
        zooms = [r for r in records if r.year == year and r.lens_type == "zoom" and r.zoom_position_bin]
        for lens_model in sorted({r.lens_model for r in zooms} | {"__all_zoom_lenses__"}):
            group = zooms if lens_model == "__all_zoom_lenses__" else [r for r in zooms if r.lens_model == lens_model]
            for weight_name, field in weight_specs:
                counts: defaultdict[str, float] = defaultdict(float)
                wide_end = tele_end = 0.0
                for record in group:
                    weight = getattr(record, field)
                    if weight is not None:
                        counts[record.zoom_position_bin] += weight
                        wide_end += weight if record.is_exact_zoom_wide_end else 0
                        tele_end += weight if record.is_exact_zoom_tele_end else 0
                total = sum(counts.values())
                for position, count in counts.items():
                    rows.append({"year": year, "lens_model": lens_model, "weighting": weight_name,
                                 "zoom_position_bin": position, "weighted_count": count,
                                 "weighted_share": count / total if total else None,
                                 "exact_wide_end_share": wide_end / total if total else None,
                                 "exact_tele_end_share": tele_end / total if total else None})
    return rows


def _rolling_12m_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    endpoints = sorted({(r.year, r.month) for r in records if r.year and r.month})
    for end_year, end_month in endpoints:
        end_index = end_year * 12 + end_month - 1
        window = [r for r in records if r.year and r.month and end_index - 11 <= r.year * 12 + r.month - 1 <= end_index]
        observed_months = len({(r.year, r.month) for r in window})
        for lens_group in ("all", "prime", "zoom"):
            group = window if lens_group == "all" else [r for r in window if r.lens_type == lens_group]
            for weight_name, field in (("photo", "photo_weight"), ("shooting_day", "shooting_day_weight"), ("month", "month_weight")):
                values = [(r.focal_35mm_mm, getattr(r, field)) for r in group if r.focal_35mm_mm is not None and getattr(r, field) is not None]
                total = sum(weight for _, weight in values)
                log_mean = sum(math.log2(value) * weight for value, weight in values) / total if total else None
                q1, median, q3 = _weighted_quantile(values, .25), _weighted_quantile(values, .5), _weighted_quantile(values, .75)
                rows.append({"window_end_year": end_year, "window_end_month": end_month, "window_month_count": observed_months,
                             "lens_group": lens_group, "weighting": weight_name, "record_count": len(group),
                             "weighted_sample_size": total, "geometric_mean_35mm_mm": 2 ** log_mean if log_mean is not None else None,
                             "median_35mm_mm": median, "q1_35mm_mm": q1, "q3_35mm_mm": q3,
                             "iqr_log2_stops": math.log2(q3 / q1) if q1 and q3 else None})
    return rows


def _session_sensitivity_rows(records: list[PhotoRecord], selected_gap: int, burst_gap: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for gap in sorted({60, 90, 120, selected_gap}):
        copies = [replace(record, session_id=None, burst_id=None, session_weight=None, burst_weight=None) for record in records]
        counts = _assign_decision_units(copies, gap, burst_gap)
        for row in _summary_rows(copies):
            if row["weighting"] == "session":
                rows.append({"session_gap_minutes": gap, "session_count": counts["session_count"], **row})
    return rows


def _lens_observation_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    """Observed data coverage only; this does not claim acquisition or ownership dates."""
    rows: list[dict[str, object]] = []
    for camera, lens in sorted({(r.camera_model, r.lens_model) for r in records}):
        group = [r for r in records if r.camera_model == camera and r.lens_model == lens]
        timestamps = sorted(r.captured_at for r in group if r.captured_at)
        rows.append({"camera_model": camera, "lens_model": lens,
                     "lens_type": Counter(r.lens_type for r in group).most_common(1)[0][0],
                     "first_observed_at": timestamps[0] if timestamps else None,
                     "last_observed_at": timestamps[-1] if timestamps else None,
                     "photo_count": len(group),
                     "shooting_day_count": len({(r.year, r.month, r.day) for r in group if r.captured_at}),
                     "active_month_count": len({(r.year, r.month) for r in group if r.year and r.month}),
                     "session_count": len({r.session_id for r in group if r.session_id})})
    return rows


def _preference_score_rows(records: list[PhotoRecord]) -> list[dict[str, object]]:
    """Final-edit preference score from normalized photo/day/month shares."""
    rows: list[dict[str, object]] = []
    components = (("photo_share", "photo_weight"), ("shooting_day_share", "shooting_day_weight"), ("month_share", "month_weight"))
    for year in sorted({r.year for r in records if r.year is not None}):
        for lens_group in ("all", "prime", "zoom"):
            base = [r for r in records if r.year == year and (lens_group == "all" or r.lens_type == lens_group)]
            for representation, getter in (("exact", lambda r: r.focal_35mm_mm), ("log_cluster", lambda r: r.focal_log_cluster_mm)):
                shares: dict[str, dict[float, float]] = {}
                complete = True
                for label, field in components:
                    counts: defaultdict[float, float] = defaultdict(float)
                    for record in base:
                        focal, weight = getter(record), getattr(record, field)
                        if focal is not None and weight is not None:
                            counts[focal] += weight
                    total = sum(counts.values())
                    if not total:
                        complete = False
                    shares[label] = {focal: count / total for focal, count in counts.items()} if total else {}
                candidates = set().union(*(mapping.keys() for mapping in shares.values()))
                scored = []
                for focal in candidates:
                    photo = shares["photo_share"].get(focal, 0.0)
                    day = shares["shooting_day_share"].get(focal, 0.0)
                    month = shares["month_share"].get(focal, 0.0)
                    composite = .5 * photo + .3 * day + .2 * month if complete else None
                    concurrence = 2 * photo * day / (photo + day) if photo + day else 0.0
                    scored.append((focal, photo, day, month, composite, concurrence))
                scored.sort(key=lambda item: (-(item[4] if item[4] is not None else -1), item[0]))
                rows.extend({"year": year, "lens_group": lens_group, "representation": representation,
                             "rank": rank, "focal_35mm_mm": focal, "photo_share": photo,
                             "shooting_day_share": day, "month_share": month,
                             "composite_score_50_30_20": composite,
                             "photo_day_harmonic_share": concurrence,
                             "all_components_available": complete}
                            for rank, (focal, photo, day, month, composite, concurrence) in enumerate(scored, start=1))
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_ai_handoff(session_gap_minutes: int, burst_gap_seconds: int) -> str:
    return f"""# AI 분석용 핸드오프

이 폴더는 원본 촬영 전체가 아니라 **최종 선택·보정해서 남긴 사진**의 EXIF 데이터입니다. 아래 파일과 프롬프트를 함께 AI에 전달하세요.

## 첨부할 파일

기본 분석에는 다음 파일이면 충분합니다.

- `dataset.json`
- `monthly_data_quality.csv`
- `preference_score.csv`
- `yearly_summary.csv`
- `exact_focal_usage.csv`
- `log_focal_cluster_usage.csv`
- `rolling_12m_summary.csv`
- `zoom_position_usage.csv`
- `lens_observed_coverage.csv`

사진별 근거를 다시 계산하거나 폴더·카메라별 조건부 분석이 필요할 때만 `photos.csv` 또는 `photos.jsonl`을 추가하세요.

## 복사용 분석 프롬프트

```text
첨부 파일은 내가 최종 선택하고 보정해서 남긴 사진의 EXIF 기반 데이터다. 목표는 단순 사용량이 아니라, 어떤 35mm 환산 화각이 최종 결과물에서 반복적으로 살아남는지와 그 선호가 시간·렌즈 종류에 따라 어떻게 달라졌는지를 판단하는 것이다.

다음 순서와 규칙으로 분석하라.

1. 먼저 데이터 품질을 감사하라.
- dataset.json과 monthly_data_quality.csv에서 전체 레코드 수, 35mm 환산값 사용 가능 비율, EXIF 직접값/카메라별 crop factor 보완값/결측값을 구분하라.
- 관측된 월, active month, 촬영일 수를 이용해 불완전 연도를 식별하라. 불완전 연도끼리 또는 완전 연도와 원시 사진 장수를 직접 비교하지 마라.
- DateTimeOriginal이 아닌 폴더 날짜 대체 레코드와 lens_type=unknown의 규모를 한계로 표시하라.

2. 최종 결과물 선호도를 분석하라.
- photo_weight를 대표 지표로 사용하라. shooting_day_weight는 여러 촬영일에서 반복됐는지, month_weight는 여러 달에 걸쳐 지속됐는지 확인하는 보조 지표다.
- preference_score.csv의 50/30/20 점수는 편의상 만든 합성 점수이지 절대적 진실이 아니다. 순위만 인용하지 말고 photo_share, shooting_day_share, month_share, photo_day_harmonic_share를 함께 제시하라.
- '결과물 비중과 촬영일 비중이 모두 높은 구조적 핵심 화각', '결과물 비중은 높지만 일부 촬영일에 집중된 화각', '장수는 적어도 여러 촬영일에서 반복된 화각'을 구분하라.

3. 초점거리 분포를 올바르게 요약하라.
- 산술평균은 보조값으로만 쓰고 weighted median, Q1/Q3, geometric mean을 우선하라.
- exact_focal_usage.csv와 log_focal_cluster_usage.csv를 함께 보라. 줌렌즈의 인접 exact 값들을 서로 다른 강한 선호로 과대해석하지 마라.
- 각 핵심 주장에는 연도, 렌즈군, 가중 방식, 비중 또는 중앙값 등 수치 근거를 붙여라.

4. 전체·단렌즈·줌렌즈를 분리하라.
- 전체는 최종 결과물의 시야각 구성, prime은 단렌즈 선택 화각, zoom은 실제 선택된 환산 초점거리로 해석하라.
- zoom_position_usage.csv는 전체 줌 합계와 렌즈 모델별 결과를 모두 확인하라. 서로 다른 초점거리 범위의 줌렌즈에서 같은 zoom_position이 같은 시야각을 뜻한다고 가정하지 마라.
- 광각단·광각측·중간·망원측·망원단 분포와 exact_wide_end_share/exact_tele_end_share를 제시하라. 평균 줌 위치 하나로 분포 형태를 단정하지 마라.

5. 시간 변화를 보수적으로 판단하라.
- yearly_summary.csv와 rolling_12m_summary.csv를 함께 사용하라.
- 12개월 롤링 결과는 window_month_count가 12보다 작으면 부분 창으로 표시하라.
- 변화가 photo/day/month 관점에서 일관적인지 확인하고, 표본 범위 변화나 특정 렌즈의 관측 시작·종료와 겹치면 취향 변화로 단정하지 마라.

6. 교란요인을 명시하라.
- lens_observed_coverage.csv의 first/last_observed_at은 데이터상 최초·최종 관측일일 뿐 구매일·판매일·실제 보유기간이 아니다.
- 카메라·렌즈 구성 변화, 촬영 장르, 장소, 피사체는 데이터만으로 확정할 수 없다. 폴더명이나 초점거리만으로 장르를 지어내지 마라.
- focal_35mm_source=derived_camera_median은 추정값으로 구분하고 missing은 환산 초점거리 통계에서 제외하라.
- session_weight와 burst_weight는 {session_gap_minutes}분 세션 및 {burst_gap_seconds}초 burst 가정에 따른 진단값이다. 이 보정본 데이터의 주 결론에는 사용하지 말고, 사용한다면 민감도와 가정을 명시하라.

출력 형식:
A. 데이터 품질과 비교 가능 범위
B. 전체 핵심 화각: exact와 log cluster 기준 TOP 5 표
C. 연도 및 12개월 롤링 변화
D. 단렌즈 분석
E. 줌렌즈 실제 화각 및 렌즈별 줌 위치 분석
F. 구조적 핵심 / 특정 촬영 집중 / 넓게 반복되는 화각 분류
G. 데이터가 말해주는 결론과 말해주지 못하는 한계

마지막에는 근거가 강한 결론, 가능성 수준의 해석, 추가 데이터가 필요한 추정을 분리하라. 제품명이나 구매 추천은 가격·크기·조리개·용도 같은 조건이 제공되지 않았다면 단정하지 말고, 우선 적합한 35mm 환산 화각 범위만 제안하라.
```
"""


def export_dataset(root: Path, output: Path, session_gap_minutes: int = 90, burst_gap_seconds: int = 5) -> Path:
    root = root.resolve()
    paths = find_images(root)
    before = snapshot(paths)
    records, metadata = scan(root, session_gap_minutes=session_gap_minutes, burst_gap_seconds=burst_gap_seconds)
    if before != snapshot(paths):
        raise RuntimeError("원본 파일 변화가 감지되어 결과를 저장하지 않았습니다.")
    output.mkdir(parents=True, exist_ok=False)
    fields = list(asdict(records[0]).keys()) if records else list(PhotoRecord.__annotations__.keys())
    _write_csv(output / "photos.csv", [asdict(record) for record in records], fields)
    with (output / "photos.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    dataset = {
        "format": "focal-ai-export/v2", "created_at": datetime.now().isoformat(timespec="seconds"),
        "record_count": len(records), "schema": {name: str(type_) for name, type_ in PhotoRecord.__annotations__.items()},
        "metadata": metadata,
        "privacy": "Absolute paths, GPS, serial numbers, and image pixels are not exported. Relative filenames and EXIF capture timestamps are included for grouping and auditability.",
    }
    (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    summary, exact_focals = _summary_rows(records), _exact_focal_rows(records)
    log_clusters, monthly_quality = _log_cluster_rows(records), _monthly_quality_rows(records)
    zoom_positions, rolling = _zoom_position_rows(records), _rolling_12m_rows(records)
    sensitivity = _session_sensitivity_rows(records, session_gap_minutes, burst_gap_seconds)
    lens_observations = _lens_observation_rows(records)
    preference_scores = _preference_score_rows(records)
    _write_csv(output / "yearly_summary.csv", summary, list(summary[0]) if summary else ["year", "lens_group", "weighting", "record_count", "usable_equivalent_count", "weighted_sample_size", "mean_35mm_mm", "geometric_mean_35mm_mm", "mean_log2_focal", "median_35mm_mm", "q1_35mm_mm", "q3_35mm_mm", "iqr_log2_stops"])
    _write_csv(output / "exact_focal_usage.csv", exact_focals, list(exact_focals[0]) if exact_focals else ["year", "lens_group", "weighting", "rank", "focal_35mm_mm", "weighted_count", "weighted_share"])
    _write_csv(output / "log_focal_cluster_usage.csv", log_clusters, list(log_clusters[0]) if log_clusters else ["year", "lens_group", "weighting", "rank", "log_cluster_center_35mm_mm", "weighted_count", "weighted_share"])
    _write_csv(output / "monthly_data_quality.csv", monthly_quality, list(monthly_quality[0]) if monthly_quality else ["year", "month", "photo_count", "usable_equivalent_count", "usable_equivalent_ratio", "exif_equivalent_count", "derived_equivalent_count", "datetimeoriginal_count", "shooting_day_count", "session_count", "active_month"])
    _write_csv(output / "zoom_position_usage.csv", zoom_positions, list(zoom_positions[0]) if zoom_positions else ["year", "lens_model", "weighting", "zoom_position_bin", "weighted_count", "weighted_share", "exact_wide_end_share", "exact_tele_end_share"])
    _write_csv(output / "rolling_12m_summary.csv", rolling, list(rolling[0]) if rolling else ["window_end_year", "window_end_month", "window_month_count", "lens_group", "weighting", "record_count", "weighted_sample_size", "geometric_mean_35mm_mm", "median_35mm_mm", "q1_35mm_mm", "q3_35mm_mm", "iqr_log2_stops"])
    _write_csv(output / "session_gap_sensitivity.csv", sensitivity, list(sensitivity[0]) if sensitivity else ["session_gap_minutes", "session_count", "year", "lens_group", "weighting"])
    _write_csv(output / "lens_observed_coverage.csv", lens_observations, list(lens_observations[0]) if lens_observations else ["camera_model", "lens_model", "lens_type", "first_observed_at", "last_observed_at", "photo_count", "shooting_day_count", "active_month_count", "session_count"])
    _write_csv(output / "preference_score.csv", preference_scores, list(preference_scores[0]) if preference_scores else ["year", "lens_group", "representation", "rank", "focal_35mm_mm", "photo_share", "shooting_day_share", "month_share", "composite_score_50_30_20", "photo_day_harmonic_share", "all_components_available"])
    (output / "AI_HANDOFF.md").write_text(
        build_ai_handoff(session_gap_minutes, burst_gap_seconds),
        encoding="utf-8",
    )
    return output
