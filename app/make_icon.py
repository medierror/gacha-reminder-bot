"""Draws the app icon (pink/purple gradient tile with a white sparkle) -> app/icon.ico + app/icon.png."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 512


def sparkle(draw: ImageDraw.ImageDraw, cx, cy, r, fill):
    k = r * 0.22  # waist of the 4-point star
    pts = [(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
           (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)]
    draw.polygon(pts, fill=fill)


def make() -> Image.Image:
    # diagonal gradient pink -> violet
    grad = Image.new("RGB", (S, S))
    a, b = (255, 122, 198), (124, 92, 255)
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S)
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=118, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sparkle(ImageDraw.Draw(glow), S * 0.47, S * 0.53, S * 0.36, (255, 255, 255, 170))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    sparkle(d, S * 0.47, S * 0.53, S * 0.33, (255, 255, 255, 255))
    sparkle(d, S * 0.76, S * 0.25, S * 0.10, (255, 255, 255, 235))
    sparkle(d, S * 0.24, S * 0.22, S * 0.06, (255, 255, 255, 200))
    return img


if __name__ == "__main__":
    icon = make()
    icon.save(HERE / "icon.png")
    icon.resize((128, 128), Image.LANCZOS).save(HERE / "ui" / "icon.png")
    icon.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written")
