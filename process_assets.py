import os
import sys
import json
import argparse
from datetime import datetime
import numpy as np
from PIL import Image

# ==========================================
# CONFIGURATION
# ==========================================
SOURCE_DIR = "nanobanana-output"
DEST_DIR = "assets/processed"
ARCHIVE_DIR = os.path.join(SOURCE_DIR, ".archive")
MANIFEST_FILE = os.path.join(SOURCE_DIR, ".processed.json")

# ==========================================
# 1. HYBRID BACKGROUND REMOVAL ENGINE
# ==========================================

def remove_background(input_path):
    """
    Attempts to remove background using 'rembg' (AI).
    Falls back to 'Green Screen Chroma Key' if rembg is not installed.
    """
    img = Image.open(input_path).convert("RGBA")
    
    # Priority 1: AI Background Removal (rembg)
    try:
        from rembg import remove
        print(f"[Process] Using rembg (AI) for {os.path.basename(input_path)}...")
        return remove(img)
    except ImportError:
        pass

    # Priority 2: High-Tolerance Chroma Key (Green Screen)
    print(f"[Process] rembg not found. Using Green Screen Key for {os.path.basename(input_path)}...")
    data = np.array(img).astype(float)
    
    # Target: Standard Green Screen (#00FF00)
    key_color = np.array([0, 255, 0])
    
    # Calculate distance
    diff = data[..., :3] - key_color
    dist = np.sqrt(np.sum(diff**2, axis=2))
    
    # Masking (Soft Edge)
    lower_thresh = 50.0
    upper_thresh = 100.0
    mask = (dist - lower_thresh) / (upper_thresh - lower_thresh)
    mask = np.clip(mask, 0.0, 1.0)
    
    # Despill (Green reduction)
    r, g, b, a = data[..., 0], data[..., 1], data[..., 2], data[..., 3]
    avg_rb = (r + b) / 2.0
    # If green is dominant, clamp it to the average of R and B
    g_despilled = np.minimum(g, avg_rb * 1.1)
    
    data[..., 1] = g_despilled
    data[..., 3] = mask * 255.0
    
    return Image.fromarray(data.astype(np.uint8))

# ==========================================
# 2. INTELLIGENT CROPPING & FORMATTING
# ==========================================

def smart_crop(img):
    """Crops the image to the content content with 2px safety padding."""
    bbox = img.getbbox()
    if bbox:
        # Add 2px padding
        left, upper, right, lower = bbox
        left = max(0, left - 2)
        upper = max(0, upper - 2)
        right = min(img.width, right + 2)
        lower = min(img.height, lower + 2)
        return img.crop((left, upper, right, lower))
    return img

# ==========================================
# 3. VERIFICATION ARTIFACT GENERATION
# ==========================================

def generate_verification_sheet(asset_path, output_dir):
    """
    Creates a simple HTML file to visualize the asset against
    White, Black, and Magenta backgrounds for quality control.
    """
    filename = os.path.basename(asset_path)
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Verify: {filename}</title>
        <style>
            body {{ font-family: sans-serif; background: #333; color: white; padding: 20px; }}
            .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-top: 20px; }}
            .check {{ padding: 20px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; }}
            .white {{ background: white; color: black; }}
            .black {{ background: black; color: white; }}
            .magenta {{ background: #ff00ff; color: white; }}
            img {{ max-width: 100%; height: auto; display: block; }}
            .label {{ margin-bottom: 10px; font-weight: bold; opacity: 0.7; }}
        </style>
    </head>
    <body>
        <h2>Verification: {filename}</h2>
        <p>Inspect edges for green spill or jagged cuts.</p>
        <div class="grid">
            <div class="check white"><span class="label">White BG</span><img src="{filename}" /></div>
            <div class="check black"><span class="label">Black BG</span><img src="{filename}" /></div>
            <div class="check magenta"><span class="label">Mask Check</span><img src="{filename}" /></div>
        </div>
    </body>
    </html>
    """
    
    sheet_path = os.path.join(output_dir, f"verify_{filename}.html")
    with open(sheet_path, "w") as f:
        f.write(html_content)
    print(f"[Verify] Created verification sheet: {sheet_path}")

# ==========================================
# 4. MANIFEST & ARCHIVE MANAGEMENT
# ==========================================

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

def get_unprocessed_files(source_dir):
    """Get list of files that haven't been processed yet."""
    if not os.path.exists(source_dir):
        return []

    manifest = load_manifest()
    processed = set(manifest.get("processed", {}).keys())
    archived = set(manifest.get("archived", {}).keys())
    known = processed | archived

    all_files = [f for f in os.listdir(source_dir)
                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                 and not f.startswith('.')]

    return [f for f in all_files if f not in known]

def archive_file(filename, source_dir):
    """Move a processed file to the dated archive folder."""
    today = datetime.now().strftime("%Y-%m-%d")
    archive_path = os.path.join(ARCHIVE_DIR, today)
    os.makedirs(archive_path, exist_ok=True)

    src = os.path.join(source_dir, filename)
    dst = os.path.join(archive_path, filename)

    if os.path.exists(src):
        os.rename(src, dst)
        print(f"[Archive] {filename} → .archive/{today}/")
        return dst
    return None

def list_status():
    """Print status of nanobanana-output directory."""
    manifest = load_manifest()

    print("\n" + "=" * 50)
    print("NANOBANANA OUTPUT STATUS")
    print("=" * 50)

    # Count files in source
    if os.path.exists(SOURCE_DIR):
        all_files = [f for f in os.listdir(SOURCE_DIR)
                     if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                     and not f.startswith('.')]
        unprocessed = get_unprocessed_files(SOURCE_DIR)

        print(f"\n📁 Source: {SOURCE_DIR}/")
        print(f"   Total files: {len(all_files)}")
        print(f"   Unprocessed: {len(unprocessed)}")

        if unprocessed:
            print(f"\n   New files:")
            for f in unprocessed[:10]:
                print(f"   • {f}")
            if len(unprocessed) > 10:
                print(f"   ... and {len(unprocessed) - 10} more")
    else:
        print(f"\n📁 Source: {SOURCE_DIR}/ (not found)")

    # Count archived
    if os.path.exists(ARCHIVE_DIR):
        archive_dates = sorted([d for d in os.listdir(ARCHIVE_DIR)
                               if os.path.isdir(os.path.join(ARCHIVE_DIR, d))])
        total_archived = sum(
            len([f for f in os.listdir(os.path.join(ARCHIVE_DIR, d))
                 if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
            for d in archive_dates
        )
        print(f"\n📦 Archive: {ARCHIVE_DIR}/")
        print(f"   Total archived: {total_archived}")
        print(f"   Date folders: {len(archive_dates)}")
        if archive_dates:
            print(f"   Recent: {', '.join(archive_dates[-3:])}")

    # Count processed
    if os.path.exists(DEST_DIR):
        processed_files = [f for f in os.listdir(DEST_DIR)
                          if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        print(f"\n✅ Processed: {DEST_DIR}/")
        print(f"   Total: {len(processed_files)}")

    print("\n" + "=" * 50)

# ==========================================
# MAIN EXECUTION LOOP
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="Process Nano Banana generated images",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python process_assets.py                    # Process all new images
  python process_assets.py --status           # Show status of nanobanana-output
  python process_assets.py --archive          # Process and archive originals
  python process_assets.py image.png          # Process specific file
  python process_assets.py --list-new         # List unprocessed files
        """
    )
    parser.add_argument('file', nargs='?', help='Specific file to process')
    parser.add_argument('--status', action='store_true',
                        help='Show status of nanobanana-output directory')
    parser.add_argument('--archive', action='store_true',
                        help='Archive original files after processing')
    parser.add_argument('--list-new', action='store_true',
                        help='List unprocessed files only')
    parser.add_argument('--only-new', action='store_true',
                        help='Only process files not in manifest')

    args = parser.parse_args()

    # Handle status command
    if args.status:
        list_status()
        return

    # Handle list-new command
    if args.list_new:
        unprocessed = get_unprocessed_files(SOURCE_DIR)
        if unprocessed:
            print("Unprocessed files:")
            for f in unprocessed:
                print(f"  • {f}")
        else:
            print("No unprocessed files found.")
        return

    # Determine files to process
    source_dir = SOURCE_DIR
    if args.file:
        if os.path.isabs(args.file):
            files = [os.path.basename(args.file)]
            source_dir = os.path.dirname(args.file)
        else:
            files = [args.file]
    elif args.only_new:
        files = get_unprocessed_files(source_dir)
        if not files:
            print("No new files to process.")
            return
    else:
        if not os.path.exists(source_dir):
            print(f"Directory {source_dir} not found. Creating it.")
            os.makedirs(source_dir, exist_ok=True)
            files = []
        else:
            files = [f for f in os.listdir(source_dir)
                    if f.lower().endswith(('.png', '.jpg', '.jpeg'))
                    and not f.startswith('.')]

    if not files:
        print("No files to process.")
        return

    os.makedirs(DEST_DIR, exist_ok=True)
    manifest = load_manifest()
    processed_count = 0

    for f in files:
        full_path = os.path.join(source_dir, f)
        if not os.path.isfile(full_path):
            continue

        print(f"Processing: {f}")

        try:
            # 1. Remove Background
            processed_img = remove_background(full_path)

            # 2. Smart Crop
            processed_img = smart_crop(processed_img)

            # 3. Save
            out_path = os.path.join(DEST_DIR, f)
            processed_img.save(out_path)

            # 4. Generate Verification Sheet
            generate_verification_sheet(out_path, DEST_DIR)

            # 5. Update manifest
            manifest["processed"][f] = {
                "processed_at": datetime.now().isoformat(),
                "output": out_path
            }

            # 6. Archive if requested
            if args.archive:
                archive_path = archive_file(f, source_dir)
                if archive_path:
                    manifest["archived"][f] = {
                        "archived_at": datetime.now().isoformat(),
                        "location": archive_path
                    }

            processed_count += 1

        except Exception as e:
            print(f"[Error] Failed to process {f}: {e}")

    # Save manifest
    save_manifest(manifest)

    print(f"\nDone. Processed {processed_count} file(s).")
    print(f"Check {DEST_DIR}/ for results and verification sheets.")
    if args.archive:
        print(f"Originals archived to {ARCHIVE_DIR}/")

if __name__ == "__main__":
    main()
