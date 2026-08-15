from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from focal_ai_export.core import export_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Export read-only EXIF focal-length data for AI analysis.")
    parser.add_argument("photo_folder", type=Path, help="Folder containing photos to scan")
    parser.add_argument("--output-dir", type=Path, required=True, help="Existing destination folder")
    parser.add_argument("--session-gap-minutes", type=int, default=90, help="New session after this EXIF time gap (default: 90)")
    parser.add_argument("--burst-gap-seconds", type=int, default=5, help="Same lens/focal burst gap (default: 5)")
    args = parser.parse_args()
    if not args.photo_folder.is_dir() or not args.output_dir.is_dir():
        parser.error("photo_folder and --output-dir must be existing directories")
    if args.session_gap_minutes < 1 or args.burst_gap_seconds < 0:
        parser.error("session gap must be at least 1 minute and burst gap must be non-negative")
    target = args.output_dir / f"FocalAIExport_{datetime.now():%Y%m%d_%H%M%S}"
    print(export_dataset(args.photo_folder, target, args.session_gap_minutes, args.burst_gap_seconds))


if __name__ == "__main__":
    main()
