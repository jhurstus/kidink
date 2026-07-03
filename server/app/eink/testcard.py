"""A deterministic 1600x1200 test card for on-panel verification.

Checks two things on real hardware: that the six inks land as the colors we
packed (labeled stripes — a wrong palette order swaps them), and how edges
survive quantization (a diagonal line fan, concentric shapes, text at several
sizes, and a gray ramp for the AA-speckle failure mode).
"""

from PIL import Image, ImageDraw

_INK_STRIPES = [
    ("BLACK", (0, 0, 0)),
    ("WHITE", (255, 255, 255)),
    ("YELLOW", (255, 255, 0)),
    ("RED", (255, 0, 0)),
    ("BLUE", (0, 0, 255)),
    ("GREEN", (0, 255, 0)),
]


def make_test_card(width: int = 1600, height: int = 1200) -> Image.Image:
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Left half, top: six labeled ink stripes.
    stripe_w = width // 2 // len(_INK_STRIPES)
    stripe_h = height // 2
    for i, (label, color) in enumerate(_INK_STRIPES):
        x0 = i * stripe_w
        draw.rectangle([x0, 0, x0 + stripe_w - 1, stripe_h - 1], fill=color)
        text_color = (255, 255, 255) if label in ("BLACK", "BLUE", "RED") else (0, 0, 0)
        draw.text((x0 + 8, 8), label, fill=text_color, font_size=28)

    # Right half, top: diagonal line fan at several stroke widths.
    fan_x, fan_y = width // 2 + 40, 40
    fan_w, fan_h = width // 2 - 80, stripe_h - 80
    for i, stroke in enumerate([1, 2, 3, 5, 8]):
        for frac in range(0, 11):
            x1 = fan_x + fan_w * frac // 10
            draw.line(
                [
                    fan_x,
                    fan_y + i * fan_h // 5 + fan_h // 10,
                    x1,
                    fan_y + (i + 1) * fan_h // 5,
                ],
                fill=(0, 0, 0),
                width=stroke,
            )

    # Bottom left: concentric circles and a rotated square outline.
    cx, cy = width // 4, height * 3 // 4
    for radius in range(30, 271, 40):
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=(0, 0, 0),
            width=3,
        )
    half = 200
    draw.polygon(
        [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)],
        outline=(0, 0, 0),
        width=3,
    )

    # Bottom middle: text sizes (default vector font, antialiased by Pillow).
    tx = width // 2 - 60
    ty = height // 2 + 40
    for size in [16, 24, 36, 56, 80]:
        draw.text((tx, ty), "Waxy 47", fill=(0, 0, 0), font_size=size)
        ty += size + 18

    # Bottom right: horizontal gray ramp (the AA-speckle failure mode).
    ramp_x, ramp_w = width * 3 // 4 + 20, width // 4 - 60
    ramp_y0, ramp_y1 = height // 2 + 60, height - 60
    for i in range(ramp_w):
        v = 255 * i // (ramp_w - 1)
        draw.line([ramp_x + i, ramp_y0, ramp_x + i, ramp_y1], fill=(v, v, v))

    return img
