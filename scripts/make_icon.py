"""One-off: render the Groundwork mark (the same shape as the inline SVG in
frontend/index.html) into favicon.ico + icon PNGs for the desktop shortcut.

Run with a Pillow-equipped interpreter, not the app's own venv:

    py scripts\\make_icon.py
"""
import pathlib

from PIL import Image, ImageDraw

TEAL = (15, 118, 110, 255)   # #0f766e — --brand in shell.css
MINT = (94, 234, 212, 255)   # #5eead4
WHITE = (255, 255, 255, 255)

SIZES = [16, 24, 32, 48, 64, 128, 256]
SCALE = 8  # supersample a 32-unit viewBox, then downsample per target size

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "static"


def render(size: int) -> Image.Image:
    s = size * SCALE / 32   # px per viewBox unit at this target, supersampled
    canvas = size * SCALE
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def pt(x, y):
        return (x * s, y * s)

    # Rounded teal tile.
    d.rounded_rectangle([pt(0, 0), pt(32, 32)], radius=7 * s, fill=TEAL)
    # Ridge line — two peaks, the "ground" in the mark.
    d.polygon([pt(6, 21), pt(13, 12), pt(18, 18), pt(22, 13), pt(26, 21)], fill=WHITE)
    # Sun / marker dot.
    d.ellipse([pt(20.4, 6.4), pt(25.6, 11.6)], fill=MINT)

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ico = OUT_DIR / "favicon.ico"
    render(256).save(ico, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"  + {ico}")
    for n in (256, 192):
        png = OUT_DIR / f"icon-{n}.png"
        render(n).save(png, format="PNG")
        print(f"  + {png}")


if __name__ == "__main__":
    main()
