#!/usr/bin/env python3
"""
Organize nanobanana-output directory by archiving old/processed files.

This script helps keep the nanobanana-output directory clean by:
1. Moving files older than N hours to dated archive folders
2. Identifying files that have already been processed
3. Providing a dry-run mode to preview changes

Usage:
    python scripts/organize_nanobanana.py              # Archive files older than 24h
    python scripts/organize_nanobanana.py --hours 1    # Archive files older than 1h
    python scripts/organize_nanobanana.py --dry-run    # Preview without moving
    python scripts/organize_nanobanana.py --all        # Archive all files (clean slate)
"""

import os
import sys
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
SOURCE_DIR = "nanobanana-output"
ARCHIVE_DIR = os.path.join(SOURCE_DIR, ".archive")
MANIFEST_FILE = os.path.join(SOURCE_DIR, ".processed.json")


def load_manifest():
    """Load the processed files manifest."""
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, 'r') as f:
            return json.load(f)
    return {"processed": {}, "archived": {}}


def save_manifest(manifest):
    """Save the processed files manifest."""
    os.makedirs(os.path.dirname(MANIFEST_FILE) or '.', exist_ok=True)
    with open(MANIFEST_FILE, 'w') as f:
        json.dump(manifest, f, indent=2, default=str)


def get_file_age_hours(filepath):
    """Get file age in hours based on modification time."""
    mtime = os.path.getmtime(filepath)
    age = datetime.now() - datetime.fromtimestamp(mtime)
    return age.total_seconds() / 3600


def get_file_date(filepath):
    """Get the date the file was created/modified."""
    mtime = os.path.getmtime(filepath)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def archive_file(filename, source_dir, date_folder, dry_run=False):
    """Move a file to the dated archive folder."""
    archive_path = os.path.join(ARCHIVE_DIR, date_folder)
    src = os.path.join(source_dir, filename)
    dst = os.path.join(archive_path, filename)

    if dry_run:
        print(f"  [DRY-RUN] Would move: {filename} → .archive/{date_folder}/")
        return None

    os.makedirs(archive_path, exist_ok=True)
    if os.path.exists(src):
        os.rename(src, dst)
        print(f"  [Archive] {filename} → .archive/{date_folder}/")
        return dst
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Organize nanobanana-output by archiving old files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/organize_nanobanana.py              # Archive files > 24h old
    python scripts/organize_nanobanana.py --hours 1    # Archive files > 1h old
    python scripts/organize_nanobanana.py --dry-run    # Preview without moving
    python scripts/organize_nanobanana.py --all        # Archive everything
    python scripts/organize_nanobanana.py --by-date    # Organize all by creation date
        """
    )
    parser.add_argument('--hours', type=float, default=24,
                        help='Archive files older than N hours (default: 24)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without moving files')
    parser.add_argument('--all', action='store_true',
                        help='Archive all files regardless of age')
    parser.add_argument('--by-date', action='store_true',
                        help='Organize all files into date folders based on creation time')

    args = parser.parse_args()

    if not os.path.exists(SOURCE_DIR):
        print(f"Directory {SOURCE_DIR} not found.")
        return

    # Get all image files (excluding hidden and already archived)
    all_files = [f for f in os.listdir(SOURCE_DIR)
                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                 and not f.startswith('.')]

    if not all_files:
        print("No files to organize.")
        return

    manifest = load_manifest()
    archived_count = 0

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}Organizing {SOURCE_DIR}/")
    print(f"Found {len(all_files)} file(s)\n")

    for filename in all_files:
        filepath = os.path.join(SOURCE_DIR, filename)
        age_hours = get_file_age_hours(filepath)
        file_date = get_file_date(filepath)

        should_archive = False
        archive_date = file_date  # Default to file's own date

        if args.all or args.by_date:
            should_archive = True
        elif age_hours > args.hours:
            should_archive = True

        if should_archive:
            result = archive_file(filename, SOURCE_DIR, archive_date, args.dry_run)
            if result or args.dry_run:
                archived_count += 1
                if not args.dry_run:
                    manifest["archived"][filename] = {
                        "archived_at": datetime.now().isoformat(),
                        "location": result
                    }

    if not args.dry_run and archived_count > 0:
        save_manifest(manifest)

    print(f"\n{'Would archive' if args.dry_run else 'Archived'}: {archived_count} file(s)")

    # Show remaining
    remaining = len(all_files) - archived_count
    if remaining > 0 and not args.all:
        print(f"Remaining in source: {remaining} file(s) (newer than {args.hours}h)")


if __name__ == "__main__":
    main()
