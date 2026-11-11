#!/usr/bin/env python3
"""Build a terminal-style GitHub profile dashboard."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
USERNAME = os.getenv("GITHUB_USERNAME", "asher-tian")
API_ROOT = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN")

# Monospace glyphs are taller than they are wide. This sampling ratio keeps
# the rendered ASCII portrait visually square in the SVG terminal font.
AVATAR_WIDTH = 48
AVATAR_HEIGHT = 29
ASCII_CHARS = "@%#*+=-:. "


def request_json(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-profile-readme",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def optional_json(url: str):
    try:
        return request_json(url)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Warning: optional GitHub request failed: {url} ({exc})", file=sys.stderr)
        return None


def request_graphql(query: str, variables: dict):
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is required for the contribution calendar")
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}",
            "User-Agent": f"{USERNAME}-profile-readme",
        },
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("errors"):
        raise RuntimeError("; ".join(error.get("message", "GraphQL error") for error in result["errors"]))
    return result["data"]


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


def fetch_contribution_calendar() -> dict | None:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    end = today + timedelta(days=1)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(
          from: $from
          to: $to
        ) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    try:
        data = request_graphql(
            query,
            {
                "login": USERNAME,
                "from": f"{start.isoformat()}T00:00:00Z",
                "to": f"{end.isoformat()}T00:00:00Z",
            },
        )
        user = data.get("user")
        if not user:
            raise RuntimeError(f"GitHub user not found: {USERNAME}")
        return user["contributionsCollection"]["contributionCalendar"]
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Warning: contribution calendar unavailable: {exc}", file=sys.stderr)
        return None


def fallback_avatar() -> list[str]:
    art = [
        "                 .-''''-.",
        "              .-'  .--.  '-.",
        "             /    /    \\\\    \\\\",
        "            |    |  .-. |    |",
        "            |    | (o o)|    |",
        "            |    |  /\\\\  |    |",
        "             \\\\    \\\\ '--'   /",
        "              '.   '----'  .'",
        "                '-.______.-'",
    ]
    canvas = [" " * AVATAR_WIDTH for _ in range(AVATAR_HEIGHT)]
    start_y = (AVATAR_HEIGHT - len(art)) // 2
    for row, line in enumerate(art):
        line = line[:AVATAR_WIDTH]
        start_x = max(0, (AVATAR_WIDTH - len(line)) // 2)
        canvas[start_y + row] = (" " * start_x + line).ljust(AVATAR_WIDTH)
    return canvas


def avatar_to_ascii(avatar_url: str | None) -> list[str]:
    """Convert the current GitHub avatar into a square-proportioned ASCII portrait."""
    if not avatar_url:
        return fallback_avatar()

    try:
        from PIL import Image, ImageEnhance, ImageOps

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
            image = image.resize((AVATAR_WIDTH, AVATAR_HEIGHT), Image.Resampling.LANCZOS)
            grayscale = ImageOps.grayscale(image)
            grayscale = ImageOps.autocontrast(grayscale, cutoff=1)
            grayscale = ImageEnhance.Contrast(grayscale).enhance(1.12)
            pixels = list(grayscale.getdata())

        return [
            "".join(
                ASCII_CHARS[
                    min(
                        len(ASCII_CHARS) - 1,
                        round(pixel / 255 * (len(ASCII_CHARS) - 1)),
                    )
                ]
                for pixel in pixels[row * AVATAR_WIDTH : (row + 1) * AVATAR_WIDTH]
            )
            for row in range(AVATAR_HEIGHT)
        ]
    except Exception as exc:
        print(f"Warning: avatar conversion failed; using fallback art ({exc})", file=sys.stderr)
        return fallback_avatar()


def collect_data() -> dict:
    profile = fetch_profile()
    repositories = fetch_repositories()
    calendar = fetch_contribution_calendar()

    return {
        "name": profile.get("name") or USERNAME,
        "username": profile.get("login") or USERNAME,
        "bio": profile.get("bio") or "Building useful software and data-driven workflows.",
        "avatar": avatar_to_ascii(profile.get("avatar_url")),
        "public_repos": int(profile.get("public_repos", len(repositories))),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in repositories),
        "followers": int(profile.get("followers", 0)),
        "contributions": calendar.get("totalContributions") if calendar else None,
        "weeks": calendar.get("weeks", []) if calendar else [],
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def number(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def contribution_level(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 6:
        return 3
    return 4


def render_svg(theme: str, data: dict) -> str:
    if theme == "dark":
        colors = {
            "background": "#0d1117",
            "panel": "#161b22",
            "panel_alt": "#0f141b",
            "border": "#30363d",
            "text": "#e6edf3",
            "muted": "#8b949e",
            "accent": "#7ee787",
            "heat": ["#21262d", "#0e4429", "#006d32", "#26a641", "#39d353"],
        }
    else:
        colors = {
            "background": "#f6f8fa",
            "panel": "#ffffff",
            "panel_alt": "#f6f8fa",
            "border": "#d0d7de",
            "text": "#1f2328",
            "muted": "#656d76",
            "accent": "#1a7f37",
            "heat": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        }

    c = colors
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">',
        '<style>',
        'text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}',
        '.ascii{white-space:pre;font-size:8.4px;letter-spacing:0px;}',
        '</style>',
        f'<rect width="1200" height="760" rx="18" fill="{c["background"]}"/>',
        f'<rect x="24" y="16" width="1152" height="36" rx="9" fill="{c["panel"]}" stroke="{c["border"]}"/>',
        f'<circle cx="43" cy="34" r="5" fill="#ffa657"/>',
        f'<circle cx="61" cy="34" r="5" fill="{c["accent"]}"/>',
        f'<circle cx="79" cy="34" r="5" fill="#79c0ff"/>',
        f'<text x="98" y="39" font-size="13" font-weight="700" fill="{c["text"]}">{escape(USERNAME)}@github:~</text>',
        f'<text x="1154" y="38" font-size="9" text-anchor="end" letter-spacing="1.2" fill="{c["muted"]}">PERSONAL GITHUB DASHBOARD</text>',
        f'<rect x="24" y="66" width="365" height="314" rx="14" fill="{c["panel"]}" stroke="{c["border"]}"/>',
        f'<rect x="408" y="66" width="768" height="314" rx="14" fill="{c["panel"]}" stroke="{c["border"]}"/>',
    ]

    def text(x, y, value, size=12, color=None, weight=400, anchor=None, spacing=None):
        attrs = [
            f'x="{x}"',
            f'y="{y}"',
            f'font-size="{size}"',
            f'font-weight="{weight}"',
            f'fill="{color or c["text"]}"',
        ]
        if anchor:
            attrs.append(f'text-anchor="{anchor}"')
        if spacing is not None:
            attrs.append(f'letter-spacing="{spacing}"')
        parts.append(f'<text {" ".join(attrs)}>{escape(str(value))}</text>')

    # Hero: square-proportioned ASCII avatar on the left, identity on the right.
    text(44, 98, "AVATAR // LIVE FROM GITHUB", 9, c["muted"], 700, spacing=0.9)
    parts.append(
        f'<text x="85" y="128" class="ascii" xml:space="preserve" fill="{c["accent"]}">'
    )
    for index, line in enumerate(data["avatar"]):
        y = 128 + index * 8.4
        parts.append(f'<tspan x="85" y="{y:.1f}">{escape(line)}</tspan>')
    parts.append("</text>")

    text(442, 116, "HELLO, I'M", 10, c["muted"], 700, spacing=1.4)
    text(442, 174, data["name"], 38, c["text"], 700)
    text(444, 204, f'@{data["username"]}', 13, c["accent"], 600)
    parts.append(f'<line x1="442" y1="230" x2="1142" y2="230" stroke="{c["border"]}"/>')
    bio = str(data["bio"]).replace("\n", " ").strip()
    if len(bio) > 76:
        bio = bio[:73].rstrip() + "…"
    text(442, 274, bio, 15, c["text"], 400)
    text(442, 320, "PUBLIC GITHUB PROFILE", 9, c["muted"], 600, spacing=1.1)

    # Four high-signal GitHub metrics.
    metrics = [
        ("PUBLIC REPOSITORIES", number(data["public_repos"]), "owned public repositories"),
        ("TOTAL STARS", number(data["stars"]), "across owned public repositories"),
        ("FOLLOWERS", number(data["followers"]), "GitHub followers"),
        ("CONTRIBUTIONS", number(data["contributions"]), "public · last 12 months"),
    ]
    metric_x = [24, 318, 612, 906]
    for index, (label, value, note) in enumerate(metrics):
        x = metric_x[index]
        parts.append(
            f'<rect x="{x}" y="402" width="270" height="98" rx="11" fill="{c["panel"]}" stroke="{c["border"]}"/>'
        )
        text(x + 18, 429, label, 9, c["muted"], 700, spacing=0.65)
        text(x + 18, 469, value, 27, c["text"], 700)
        text(x + 18, 487, note, 8, c["muted"], 400)

    # One-year GitHub contribution calendar.
    parts.append(
        f'<rect x="24" y="520" width="1152" height="190" rx="11" fill="{c["panel"]}" stroke="{c["border"]}"/>'
    )
    text(44, 550, "CONTRIBUTION ACTIVITY // LAST 12 MONTHS", 10, c["text"], 700, spacing=0.55)
    contribution_total = (
        "unavailable" if data["contributions"] is None
        else f'{number(data["contributions"])} public contributions'
    )
    text(1154, 550, contribution_total, 9, c["muted"], 500, "end")

    weeks = data["weeks"]
    cell_size = 11
    gap = 3
    step = cell_size + gap
    grid_width = max(0, len(weeks) * step - gap)
    start_x = (1200 - grid_width) / 2 if weeks else 230
    start_y = 590

    previous_month = None
    for week_index, week in enumerate(weeks):
        days = week.get("contributionDays", [])
        if days:
            try:
                first_day = date.fromisoformat(days[0]["date"])
                month_key = (first_day.year, first_day.month)
                if month_key != previous_month:
                    text(start_x + week_index * step, 578, first_day.strftime("%b").upper(), 8, c["muted"])
                    previous_month = month_key
            except (KeyError, ValueError):
                pass

        for day_index, day in enumerate(days):
            count = int(day.get("contributionCount", 0))
            level = contribution_level(count)
            x = start_x + week_index * step
            y = start_y + day_index * step
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_size}" height="{cell_size}" rx="2" fill="{c["heat"][level]}"/>'
            )

    if not weeks:
        text(230, 635, "Contribution calendar unavailable", 11, c["muted"])

    # Activity intensity legend.
    legend_y = 696
    text(924, legend_y + 8, "LESS", 8, c["muted"])
    for index, color in enumerate(c["heat"]):
        x = 960 + index * 15
        parts.append(
            f'<rect x="{x}" y="{legend_y}" width="10" height="10" rx="2" fill="{color}"/>'
        )
    text(1042, legend_y + 8, "MORE", 8, c["muted"])

    text(24, 744, "PUBLIC CONTRIBUTIONS ONLY  ·  UPDATED " + data["updated"], 8, c["muted"], 500, spacing=0.45)
    text(1176, 744, "github.com/" + USERNAME, 8, c["muted"], 500, "end")
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    print(f"Building profile for @{USERNAME}...")
    data = collect_data()
    for theme in ("dark", "light"):
        output = ROOT / f"{theme}_mode.svg"
        output.write_text(render_svg(theme, data), encoding="utf-8")
        print(f"Wrote {output.relative_to(ROOT)}")
    print("Profile build complete.")


if __name__ == "__main__":
    main()
