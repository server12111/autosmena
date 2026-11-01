"""Personal profile and wallet banners drawn on top of the static section artwork.

The URL carries an HMAC of the user ID, so nobody can read another user's card by
guessing IDs, and a hash of the shown data, so Telegram refetches when it changes.
"""

import hmac
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import settings

STATIC = Path(__file__).parent / "static"
BRAND = STATIC / "brand"
FONTS = STATIC / "fonts"
KINDS = ("profile", "wallet")
WHITE = (255, 255, 255, 255)
MUTED = (147, 163, 194, 255)


def _secret():
    return sha256(
        ("banner:" + (settings.bot_token or settings.admin_password_hash or "smena")).encode()
    ).digest()


def signature(user_id):
    return hmac.new(_secret(), str(user_id).encode(), sha256).hexdigest()[:20]


def valid(user_id, sig):
    return hmac.compare_digest(signature(user_id), sig)


@lru_cache(maxsize=None)
def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


@lru_cache(maxsize=None)
def base(kind):
    return Image.open(BRAND / f"{kind}.png").convert("RGBA")


def handle(username, user_id):
    return f"@{username}" if username else f"@user{user_id}"


def fit(draw, text, name, size, width):
    """Largest font size (down to 60%) at which text fits the width; long handles get an ellipsis."""
    for current in range(size, int(size * 0.6) - 1, -2):
        if draw.textlength(text, font=font(name, current)) <= width:
            return text, font(name, current)
    small = font(name, int(size * 0.6))
    while text and draw.textlength(text + "…", font=small) > width:
        text = text[:-1]
    return text + "…", small


def overlay(image):
    """Translucent shapes must be drawn on their own layer and blended, not painted over pixels."""
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def glass(image, box, radius=28):
    """Frosted card: blurred backdrop, light fill, top highlight, thin border and soft shadow."""
    x0, y0, x1, y1 = box
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 24, x1, y1 + 24), radius, fill=(4, 10, 35, 120))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius, fill=255)
    blurred = image.filter(ImageFilter.GaussianBlur(18))
    image.paste(blurred, (0, 0), mask)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for step in range(y1 - y0):
        alpha = int(40 - 26 * step / (y1 - y0))
        draw.line((x0, y0 + step, x1, y0 + step), fill=(255, 255, 255, alpha))
    clipped = Image.new("RGBA", image.size, (0, 0, 0, 0))
    clipped.paste(layer, (0, 0), mask)
    image.alpha_composite(clipped)
    layer, outline = overlay(image)
    outline.rounded_rectangle(box, radius, outline=(255, 255, 255, 60), width=2)
    outline.line((x0 + radius, y0 + 1, x1 - radius, y0 + 1), fill=(255, 255, 255, 100), width=1)
    image.alpha_composite(layer)


def tile(image, box, label, value):
    layer, shape = overlay(image)
    shape.rounded_rectangle(box, 18, fill=(255, 255, 255, 18), outline=(255, 255, 255, 34), width=1)
    image.alpha_composite(layer)
    draw = ImageDraw.Draw(image)
    x0, y0, x1, _ = box
    draw.text((x0 + 20, y0 + 16), label, font=font("InterDisplay-SemiBold.otf", 17), fill=MUTED)
    text, face = fit(draw, value, "InterDisplay-ExtraBold.otf", 30, x1 - x0 - 40)
    draw.text((x0 + 20, y0 + 42), text, font=face, fill=WHITE)


def avatar(image, center, radius, letter):
    cx, cy = center
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (cx - radius, cy - radius + 8, cx + radius, cy + radius + 8), fill=(42, 171, 238, 110)
    )
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(16)))
    disc = Image.new("RGBA", (radius * 2, radius * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(disc)
    for step in range(radius * 2):
        t = step / (radius * 2)
        color = (int(90 - 72 * t), int(200 - 86 * t), int(250 - 36 * t), 255)
        draw.line((0, step, radius * 2, step), fill=color)
    mask = Image.new("L", disc.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, radius * 2 - 1, radius * 2 - 1), fill=255)
    image.paste(disc, (cx - radius, cy - radius), mask)
    ImageDraw.Draw(image).text(
        (cx, cy + 1), letter, font=font("InterDisplay-Black.otf", int(radius * 0.9)), fill=WHITE, anchor="mm"
    )


def render(kind, data):
    image = base(kind).copy()
    draw = ImageDraw.Draw(image)
    if kind == "profile":
        box = (740, 150, 1180, 570)
        glass(image, box)
        name = handle(data["username"], data["user_id"])
        letter = (data["username"] or "U")[:1].upper()
        avatar(image, (818, 228), 41, letter)
        text, face = fit(draw, name, "InterDisplay-ExtraBold.otf", 30, 270)
        draw.text((880, 200), text, font=face, fill=WHITE)
        draw.text((881, 238), f"ID {data['user_id']}", font=font("JetBrainsMono-Medium.ttf", 19), fill=MUTED)
        draw.text((777, 306), "Баланс", font=font("InterDisplay-SemiBold.otf", 18), fill=MUTED)
        text, face = fit(draw, "$" + data["balance"], "InterDisplay-Black.otf", 58, 366)
        draw.text((775, 334), text, font=face, fill=WHITE)
        tile(image, (777, 430, 952, 521), "Номера", str(data["numbers"]))
        tile(image, (967, 430, 1142, 521), "Рефералы", str(data["referrals"]))
    else:
        box = (770, 380, 1170, 598)
        glass(image, box)
        draw.text((803, 412), "Ваш баланс", font=font("InterDisplay-SemiBold.otf", 18), fill=MUTED)
        text, face = fit(draw, "$" + data["balance"], "InterDisplay-Black.otf", 56, 334)
        draw.text((801, 440), text, font=face, fill=WHITE)
        label = f"Пополнено ${data['deposited']}"
        face = font("InterDisplay-SemiBold.otf", 18)
        width = draw.textlength(label, font=face)
        layer, shape = overlay(image)
        shape.rounded_rectangle(
            (803, 524, 803 + width + 32, 566), 12, fill=(52, 199, 89, 46), outline=(52, 199, 89, 128)
        )
        image.alpha_composite(layer)
        draw = ImageDraw.Draw(image)
        draw.text((819, 545), label, font=face, fill=(124, 240, 160, 255), anchor="lm")
    output = BytesIO()
    image.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()


def data_version(data):
    return sha256(repr(sorted(data.items())).encode()).hexdigest()[:10]


def user_data(user_id):
    from sqlalchemy import func, select

    from app.db import Session
    from app.models import Order, Payment, User
    from app.money import display

    with Session() as db:
        user = db.get(User, user_id)
        if not user:
            return None
        numbers = db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.user_id == user_id, Order.parent_id.is_(None), Order.status.in_(["waiting", "active"])
            )
        )
        referrals = db.scalar(select(func.count()).select_from(User).where(User.referrer_id == user_id))
        deposited = db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.user_id == user_id, Payment.status == "paid"
            )
        )
        return {
            "user_id": user_id,
            "username": user.username or "",
            "balance": display(user.balance),
            "numbers": numbers,
            "referrals": referrals,
            "deposited": display(deposited),
        }
