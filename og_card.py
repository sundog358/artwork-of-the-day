"""Branded 1200×630 Open Graph cards for social link previews.

Composites the painting over a blurred, darkened copy of itself, with an
editorial text panel (wordmark · title · byline · domain) — the magazine-style
preview Facebook / X / LinkedIn render at a fixed 1.91:1. Pure: takes the
painting's bytes, returns JPEG bytes. Fonts are vendored under static/fonts so
the output is identical locally and on the Linux deploy (no system fonts needed).
"""

import io
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

_FONTS = os.path.join(os.path.dirname(__file__), "static", "fonts")
_LANCZOS = Image.Resampling.LANCZOS
W, H = 1200, 630
NAVY = (14, 28, 46)
BLUE = (143, 183, 217)


def _font(name, size):
    return ImageFont.truetype(os.path.join(_FONTS, name), size)


def _wrap(text, font, max_w, max_lines):
    """Greedy word-wrap to `max_lines`, ellipsizing if the text overflows."""
    words = (text or "").split()
    lines, cur = [], ""
    truncated = False
    for i, word in enumerate(words):
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
            if len(lines) == max_lines:
                truncated = i < len(words) - 1 or True
                cur = ""
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if truncated and lines:
        last = lines[-1]
        while last and font.getlength(last + "…") > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _tracked(draw, pos, text, font, fill, tracking):
    """Draw text with manual letter-spacing (Pillow has no native tracking)."""
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking


def render_card(image_bytes, *, title, subtitle, eyebrow="Meta History Book"):
    """A 1200×630 JPEG (bytes) branded card for `image_bytes` (the painting)."""
    painting = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Backdrop: the painting itself, cover-fit, blurred, with a navy scrim.
    canvas = ImageOps.fit(painting, (W, H), method=_LANCZOS)
    canvas = canvas.filter(ImageFilter.GaussianBlur(30)).convert("RGBA")
    canvas.alpha_composite(Image.new("RGBA", (W, H), NAVY + (212,)))

    # The painting, fully visible (contain-fit) in a framed card on the left.
    box = (56, 70, 620, 560)
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = min(bw / painting.width, bh / painting.height)
    pw, ph = max(1, round(painting.width * scale)), max(1, round(painting.height * scale))
    p = painting.resize((pw, ph), _LANCZOS)
    px = box[0] + (bw - pw) // 2
    py = box[1] + (bh - ph) // 2

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle(
        [px - 6, py - 4, px + pw + 12, py + ph + 16], fill=(0, 0, 0, 150)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(16)))
    ImageDraw.Draw(canvas).rectangle(
        [px - 5, py - 5, px + pw + 5, py + ph + 5], fill=(244, 244, 240, 255)
    )
    canvas.paste(p, (px, py))

    draw = ImageDraw.Draw(canvas)
    tx = 668
    right = W - 56

    _tracked(draw, (tx, 92), eyebrow.upper(), _font("DejaVuSans-Bold.ttf", 22), BLUE + (255,), 3)
    draw.rectangle([tx, 130, tx + 56, 133], fill=BLUE + (255,))

    title_font = _font("DejaVuSerif-Bold.ttf", 50)
    ty = 170
    for line in _wrap(title or "Untitled", title_font, right - tx, 4):
        draw.text((tx, ty), line, font=title_font, fill=(245, 247, 250, 255))
        ty += 62

    sub_font = _font("DejaVuSans.ttf", 26)
    ty += 10
    for line in _wrap(subtitle, sub_font, right - tx, 2):
        draw.text((tx, ty), line, font=sub_font, fill=(205, 214, 224, 255))
        ty += 36

    draw.text(
        (tx, H - 72), "metahistorybook.com", font=_font("DejaVuSans.ttf", 22), fill=BLUE + (255,)
    )

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue()
