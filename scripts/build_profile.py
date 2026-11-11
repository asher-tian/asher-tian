#!/usr/bin/env python3
"""Build a Chinese-pigment-inspired animated ASCII GitHub profile card."""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
USERNAME = os.getenv("GITHUB_USERNAME", "asher-tian")
API_ROOT = "https://api.github.com"

CARD_SIZE = (1200, 550)
FRAME_COUNT = 12
FRAME_DURATION_MS = 180

# Character cells are deliberately wider than they are tall. The 40x22
# sampling grid compensates for monospace glyph proportions, keeping the
# square-cropped GitHub avatar visually close to a square.
AVATAR_WIDTH = 40
AVATAR_HEIGHT = 22
ASCII_CHARS = " .:-=+*#%@"

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
]

THEMES = {
    "dark": {
        "background": "#111715",
        "panel": "#19211e",
        "panel_alt": "#151d1a",
        "border": "#35413a",
        "text": "#e8e9dc",
        "muted": "#a3ada0",
        "accent": "#9ab49b",
        "window_red": "#b65a43",
        "window_green": "#78936e",
        "window_blue": "#54798a",
        "avatar_palette": [
            "#203744",  # ink blue
            "#35586a",  # stone blue
            "#557b82",  # blue-green mineral
            "#668b68",  # malachite
            "#a5bba0",  # jade
            "#e5e7d9",  # moon white
        ],
        "avatar_shadow": "#0d1210",
        "seal": "#b64c38",  # cinnabar
        "footer": "#8e9b8e",
    },
    "light": {
        "background": "#f4f0e5",  # xuan-paper
        "panel": "#fbf8ef",
        "panel_alt": "#f2eddf",
        "border": "#d9d0be",
        "text": "#303831",
        "muted": "#787568",
        "accent": "#587761",
        "window_red": "#c36c55",
        "window_green": "#809a74",
        "window_blue": "#6c8ea5",
        "avatar_palette": [
            "#344b5a",
            "#526f78",
            "#64887b",
            "#77946b",
            "#b2be9b",
            "#f2e9d7",
        ],
        "avatar_shadow": "#e6dfd0",
        "seal": "#b64c38",
        "footer": "#817a6d",
    },
}


def request_json(url: str) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-profile-readme",
        "X-GitHub-API-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_profile() -> dict:
    return request_json(f"{API_ROOT}/users/{quote(USERNAME)}")


def fetch_repositories() -> list[dict]:
    repositories: list[dict] = []
    for page in range(1, 11):
        url = (
            f"{API_ROOT}/users/{quote(USERNAME)}/repos"
            f"?type=owner&sort=updated&per_page=100&page={page}"
        )
        batch = request_json(url)
        repositories.extend(batch)
        if len(batch) < 100:
            break
    return repositories


def pick_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def fit_font(draw: ImageDraw.ImageDraw, value: str, max_width: int, start_size: int):
    size = start_size
    while size > 18:
        font = pick_font(size)
        if draw.textlength(value, font=font) <= max_width:
            return font
        size -= 1
    return pick_font(size)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def blend(
    first: tuple[int, int, int],
    second: tuple[int, int, int],
    amount: float,
) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second))


def interpolate_palette(colors: list[str], position: float) -> tuple[int, int, int]:
    palette = [hex_to_rgb(color) for color in colors]
    position = max(0.0, min(1.0, position)) * (len(palette) - 1)
    index = min(len(palette) - 2, int(position))
    return blend(palette[index], palette[index + 1], position - index)


def number(value: int) -> str:
    return f"{int(value):,}"


def fallback_avatar() -> tuple[list[str], list[list[int]]]:
    art = [
        "          .-''''-.",
        "        .'  .--.  '.",
        "       /   /    \\   \\",
        "      |   | .--. |   |",
        "      |   ||    ||   |",
        "      |   | '--' |   |",
        "       \\   '----'   /",
        "        '.        .'",
        "          '-.__.-'",
    ]
    lines = [" " * AVATAR_WIDTH for _ in range(AVATAR_HEIGHT)]
    levels = [[0] * AVATAR_WIDTH for _ in range(AVATAR_HEIGHT)]
    top = (AVATAR_HEIGHT - len(art)) // 2
    for row, line in enumerate(art):
        line = line[:AVATAR_WIDTH]
        left = max(0, (AVATAR_WIDTH - len(line)) // 2)
        padded = (" " * left + line).ljust(AVATAR_WIDTH)
        lines[top + row] = padded
        levels[top + row] = [220 if char != " " else 0 for char in padded]
    return lines, levels


def avatar_to_ascii(avatar_url: str | None) -> tuple[list[str], list[list[int]]]:
    if not avatar_url:
        return fallback_avatar()

    try:
        separator = "&" if "?" in avatar_url else "?"
        request = Request(
            f"{avatar_url}{separator}s=460",
            headers={"User-Agent": f"{USERNAME}-profile-readme"},
        )
        with urlopen(request, timeout=20) as response:
            image_bytes = response.read()

        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            image = image.resize(
                (AVATAR_WIDTH, AVATAR_HEIGHT),
                Image.Resampling.LANCZOS,
            )
            grayscale = ImageOps.grayscale(image)
            grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
            grayscale = ImageEnhance.Contrast(grayscale).enhance(1.15)
            pixels = list(grayscale.getdata())

        lines: list[str] = []
        levels: list[list[int]] = []
        for row in range(AVATAR_HEIGHT):
            row_pixels = pixels[row * AVATAR_WIDTH : (row + 1) * AVATAR_WIDTH]
            lines.append(
                "".join(
                    ASCII_CHARS[
                        min(
                            len(ASCII_CHARS) - 1,
                            round(pixel / 255 * (len(ASCII_CHARS) - 1)),
                        )
                    ]
                    for pixel in row_pixels
                )
            )
            levels.append(row_pixels)
        return lines, levels
    except Exception as exc:
        print(
            f"Warning: avatar conversion failed; using fallback art ({exc})",
            file=sys.stderr,
        )
        return fallback_avatar()


def collect_data() -> dict:
    profile = fetch_profile()
    repositories = fetch_repositories()
    avatar_lines, avatar_levels = avatar_to_ascii(profile.get("avatar_url"))
    bio = " ".join(
        str(profile.get("bio") or "Building useful software and data-driven workflows.")
        .replace("\n", " ")
        .split()
    )

    return {
        "name": profile.get("name") or USERNAME,
        "username": profile.get("login") or USERNAME,
        "bio": bio,
        "avatar_lines": avatar_lines,
        "avatar_levels": avatar_levels,
        "public_repos": int(profile.get("public_repos", len(repositories))),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in repositories),
        "followers": int(profile.get("followers", 0)),
        "following": int(profile.get("following", 0)),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def avatar_color(
    theme: dict,
    level: int,
    x: int,
    y: int,
    frame_index: int,
) -> tuple[int, int, int]:
    x_norm = x / max(1, AVATAR_WIDTH - 1)
    y_norm = y / max(1, AVATAR_HEIGHT - 1)
    phase = 2 * math.pi * frame_index / FRAME_COUNT

    base = 0.18 + 0.42 * x_norm + 0.15 * (1 - y_norm) + 0.25 * (level / 255)
    wave = 0.035 * math.sin(phase + x_norm * 5.5 - y_norm * 3.8)

    # A soft light moves back and forth on a sine wave, so the loop closes
    # smoothly instead of jumping from the last frame to the first.
    sweep_center = 0.5 + 0.58 * math.sin(phase)
    sweep = 0.12 * math.exp(-((x_norm - sweep_center) ** 2) / 0.025)
    position = max(0.0, min(1.0, base + wave + sweep))
    color = interpolate_palette(theme["avatar_palette"], position)

    # Rare cinnabar glints are intentionally restrained.
    glint = math.sin(phase + x_norm * 8.0 + y_norm * 5.0)
    if level > 185 and glint > 0.985:
        color = blend(color, hex_to_rgb(theme["seal"]), 0.42)
    return color


def draw_seal(draw: ImageDraw.ImageDraw, theme: dict) -> None:
    color = hex_to_rgb(theme["seal"])
    draw.rectangle((347, 78, 367, 98), outline=color, width=1)
    draw.line((351, 83, 363, 83), fill=color, width=1)
    draw.line((351, 88, 360, 88), fill=color, width=1)
    draw.line((351, 93, 363, 93), fill=color, width=1)


def render_frame(theme_name: str, data: dict, frame_index: int) -> Image.Image:
    theme = THEMES[theme_name]
    width, height = CARD_SIZE
    background = hex_to_rgb(theme["background"])
    panel = hex_to_rgb(theme["panel"])
    panel_alt = hex_to_rgb(theme["panel_alt"])
    border = hex_to_rgb(theme["border"])
    text_color = hex_to_rgb(theme["text"])
    muted = hex_to_rgb(theme["muted"])
    accent = hex_to_rgb(theme["accent"])

    image = Image.new("RGB", CARD_SIZE, background)
    draw = ImageDraw.Draw(image)

    font_small = pick_font(13)
    font_tiny = pick_font(10)
    font_body = pick_font(16)
    font_label = pick_font(11)
    font_metric_label = pick_font(11)
    font_metric_value = pick_font(32)
    font_title = pick_font(17)
    font_avatar = pick_font(10)

    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=18,
        fill=background,
    )
    draw.rounded_rectangle(
        (24, 16, 1176, 52),
        radius=9,
        fill=panel,
        outline=border,
        width=1,
    )
    draw.ellipse((38, 29, 48, 39), fill=hex_to_rgb(theme["window_red"]))
    draw.ellipse((56, 29, 66, 39), fill=hex_to_rgb(theme["window_green"]))
    draw.ellipse((74, 29, 84, 39), fill=hex_to_rgb(theme["window_blue"]))
    draw.text((98, 39), f"{USERNAME}@github:~", font=font_small, fill=text_color, anchor="lm")
    draw.text(
        (1154, 38),
        "GUOFENG // INK & MINERAL PIGMENTS",
        font=font_tiny,
        fill=muted,
        anchor="rm",
    )

    draw.rounded_rectangle(
        (24, 66, 389, 380),
        radius=14,
        fill=panel,
        outline=border,
        width=1,
    )
    draw.rounded_rectangle(
        (408, 66, 1176, 380),
        radius=14,
        fill=panel,
        outline=border,
        width=1,
    )

    draw.text(
        (44, 98),
        "AVATAR // LIVE FROM GITHUB",
        font=font_label,
        fill=muted,
    )
    draw_seal(draw, theme)

    # A quiet inset gives the ASCII portrait the feel of a mounted painting.
    draw.rounded_rectangle(
        (58, 112, 356, 354),
        radius=12,
        fill=panel_alt,
    )
    char_width = draw.textlength("M", font=font_avatar)
    line_height = 10.5
    grid_width = char_width * AVATAR_WIDTH
    grid_height = line_height * AVATAR_HEIGHT
    start_x = 58 + (298 - grid_width) / 2
    start_y = 112 + (242 - grid_height) / 2
    shadow = hex_to_rgb(theme["avatar_shadow"])

    for row_index, line in enumerate(data["avatar_lines"]):
        y = start_y + row_index * line_height
        for col_index, char in enumerate(line):
            if char == " ":
                continue
            level = data["avatar_levels"][row_index][col_index]
            fill = avatar_color(theme, level, col_index, row_index, frame_index)
            x = start_x + col_index * char_width
            draw.text((x + 0.7, y + 0.8), char, font=font_avatar, fill=shadow)
            draw.text((x, y), char, font=font_avatar, fill=fill)

    draw.text((442, 118), "HELLO, I'M", font=font_title, fill=muted)
    name = str(data["name"])
    name_font = fit_font(draw, name, 690, 43)
    draw.text((442, 176), name, font=name_font, fill=text_color)
    draw.text(
        (444, 209),
        f"@{data['username']}",
        font=font_body,
        fill=accent,
    )
    draw.line((442, 233, 1142, 233), fill=border, width=1)

    bio = data["bio"]
    if len(bio) > 76:
        bio = bio[:73].rstrip() + "..."
    draw.text((442, 276), bio, font=font_body, fill=text_color)
    draw.text(
        (442, 322),
        "PUBLIC GITHUB PROFILE",
        font=font_label,
        fill=muted,
    )

    metrics = [
        ("PUBLIC REPOSITORIES", number(data["public_repos"]), "owned public repositories"),
        ("TOTAL STARS", number(data["stars"]), "across owned public repositories"),
        ("FOLLOWERS", number(data["followers"]), "GitHub followers"),
        ("FOLLOWING", number(data["following"]), "following on GitHub"),
    ]
    metric_x = [24, 318, 612, 906]
    for index, (label, value, note) in enumerate(metrics):
        x = metric_x[index]
        draw.rounded_rectangle(
            (x, 402, x + 270, 500),
            radius=11,
            fill=panel,
            outline=border,
            width=1,
        )
        draw.text(
            (x + 18, 430),
            label,
            font=font_metric_label,
            fill=muted,
        )
        draw.text(
            (x + 18, 468),
            value,
            font=font_metric_value,
            fill=text_color,
        )
        draw.text(
            (x + 18, 491),
            note,
            font=font_tiny,
            fill=muted,
        )

    draw.text(
        (24, 532),
        f"GITHUB PROFILE  ·  UPDATED {data['updated']}",
        font=font_tiny,
        fill=hex_to_rgb(theme["footer"]),
    )
    draw.text(
        (1176, 532),
        f"github.com/{USERNAME}",
        font=font_tiny,
        fill=hex_to_rgb(theme["footer"]),
        anchor="ra",
    )
    return image


def save_gif(theme_name: str, data: dict) -> Path:
    output = ROOT / f"{theme_name}_mode.gif"
    frames = [
        render_frame(theme_name, data, frame_index)
        for frame_index in range(FRAME_COUNT)
    ]

    # Quantize all frames against one shared palette to avoid color flicker.
    palette_sheet = Image.new(
        "RGB",
        (CARD_SIZE[0], CARD_SIZE[1] * FRAME_COUNT),
    )
    for index, frame in enumerate(frames):
        palette_sheet.paste(frame, (0, index * CARD_SIZE[1]))
    palette = palette_sheet.quantize(
        colors=192,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    indexed_frames = [
        frame.quantize(palette=palette, dither=Image.Dither.NONE)
        for frame in frames
    ]

    indexed_frames[0].save(
        output,
        save_all=True,
        append_images=indexed_frames[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        optimize=True,
        disposal=1,
    )
    return output


def main() -> None:
    print(f"Building animated guofeng profile for @{USERNAME}...")
    data = collect_data()
    for theme_name in ("dark", "light"):
        output = save_gif(theme_name, data)
        print(f"Wrote {output.relative_to(ROOT)}")
    print("Animated profile build complete.")


if __name__ == "__main__":
    main()
