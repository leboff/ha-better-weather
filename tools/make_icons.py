"""Reproduce the transparent sun/cloud brand icons with Pillow."""

import math
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    out = Path(__file__).resolve().parents[1] / "custom_components/better_weather/brand"
    out.mkdir(parents=True, exist_ok=True)
    scale = 4
    image = Image.new("RGBA", (512 * scale, 512 * scale))
    draw = ImageDraw.Draw(image)

    def box(bounds):
        return tuple(int(v * scale) for v in bounds)

    sun = "#FFC857"
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        draw.line(
            [
                (
                    int((202 + 105 * math.cos(rad)) * scale),
                    int((182 + 105 * math.sin(rad)) * scale),
                ),
                (
                    int((202 + 135 * math.cos(rad)) * scale),
                    int((182 + 135 * math.sin(rad)) * scale),
                ),
            ],
            fill=sun,
            width=18 * scale,
        )
    draw.ellipse(box((118, 98, 286, 266)), fill=sun)
    cloud = "#63B7DE"
    draw.ellipse(box((111, 243, 259, 391)), fill=cloud)
    draw.ellipse(box((188, 185, 376, 373)), fill=cloud)
    draw.ellipse(box((293, 255, 425, 387)), fill=cloud)
    draw.rounded_rectangle(box((163, 285, 385, 391)), radius=40 * scale, fill=cloud)
    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        image.resize((size, size), Image.Resampling.LANCZOS).save(out / name)


if __name__ == "__main__":
    main()
