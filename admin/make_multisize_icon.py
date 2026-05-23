"""
Convert a single-size .ico (or PNG) to a proper multi-size Windows .ico
that contains 16, 32, 48, 64, 128, and 256 pixel versions inside one file.

This makes the icon look crisp at every Windows display location:
    - 16x16 in file explorer details view
    - 32x32 default Desktop
    - 48x48 large icons
    - 256x256 jumbo / properties dialog

Usage:
    python admin/make_multisize_icon.py            # uses resources/favicon.ico
    python admin/make_multisize_icon.py input.png  # convert any image
"""
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).parent.parent
DEFAULT_INPUT = ROOT / "resources" / "favicon.ico"
OUTPUT = ROOT / "resources" / "pymail.ico"
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    src_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    if not src_path.is_file():
        print(f"ERROR: {src_path} not found.")
        sys.exit(1)

    print(f"Source: {src_path}")
    img = Image.open(src_path)

    # If multi-frame ICO, take the largest one as source
    if hasattr(img, "n_frames") and img.n_frames > 1:
        biggest_size = (0, 0)
        biggest_idx = 0
        for i in range(img.n_frames):
            img.seek(i)
            if img.size[0] * img.size[1] > biggest_size[0] * biggest_size[1]:
                biggest_size = img.size
                biggest_idx = i
        img.seek(biggest_idx)
        print(f"Using largest sub-image: {biggest_size}")

    img = img.convert("RGBA")
    print(f"Source size: {img.size}, mode: {img.mode}")

    # Pillow's ICO writer accepts a list of sizes via the `sizes` parameter
    # and will internally generate each size via thumbnail()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, format="ICO", sizes=SIZES)
    print(f"Wrote multi-size icon: {OUTPUT}")
    print(f"Embedded sizes: {SIZES}")
    print(f"Size on disk: {OUTPUT.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
