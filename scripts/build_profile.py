#!/usr/bin/env python3
"""Build a terminal-style GitHub profile from public GitHub data."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
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
# Monospace glyphs are taller than they are wide. A 48x29 sampling grid
# compensates for the font cell aspect ratio so the rendered portrait is square.
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


def fetch_commit_count() -> int | None:
    result = optional_json(
        f"{API_ROOT}/search/commits?q=author:{quote(USERNAME)}&per_page=1"
    )
    if not result:
        return None
    return int(result.get("total_count", 0))


def fetch_public_events() -> list[dict] | None:
    events: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    for page in range(1, 4):
        batch = optional_json(
            f"{API_ROOT}/users/{quote(USERNAME)}/events/public"
            f"?per_page=100&page={page}"
        )
        if batch is None:
            return None
        if not batch:
            break

        events.extend(batch)
        last_created = batch[-1].get("created_at")
        if last_created:
            try:
                last_date = datetime.fromisoformat(last_created.replace("Z", "+00:00"))
                if last_date < cutoff:
                    break
            except ValueError:
                pass

    return events


def fetch_language_bytes(repositories: list[dict]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    candidates = sorted(
        repositories,
        key=lambda repo: (
            repo.get("stargazers_count", 0),
            repo.get("updated_at", ""),
        ),
        reverse=True,
    )[:12]

    for repo in candidates:
        url = repo.get("languages_url")
        if not url:
            continue
        languages = optional_json(url)
        if isinstance(languages, dict):
            for language, byte_count in languages.items():
                totals[language] += int(byte_count)

    if totals:
        return dict(totals)

    # If language endpoints are unavailable, fall back to each repository's
    # primary language. This fallback is a repository count, not byte share.
    primary_languages = Counter(
        repo["language"] for repo in repositories if repo.get("language")
    )
    return dict(primary_languages)


def build_activity(events: list[dict] | None) -> tuple[list[int], int | None]:
    days = [date.today() - timedelta(days=offset) for offset in range(29, -1, -1)]
    counts = {day: 0 for day in days}

    if events is None:
        return [0] * len(days), None

    for event in events:
        created_at = event.get("created_at")
        if not created_at:
            continue
        try:
            event_date = datetime.fromisoformat(
                created_at.replace("Z", "+00:00")
            ).date()
        except ValueError:
            continue
        if event_date in counts:
            counts[event_date] += 1

    values = [counts[day] for day in days]
    return values, sum(values)


def fallback_avatar() -> list[str]:
    art = [
        "                 .-''''-.",
        "              .-'  .--.  '-.",
        "             /    /    \\    \\",
        "            |    |  .-. |    |",
        "            |    | (o o)|    |",
        "            |    |  /\\  |    |",
        "             \\    \\ '--'   /",
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
    """Convert the current GitHub avatar into a proportion-correct ASCII portrait."""
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

            # GitHub avatars are square. Center-crop defensively if the source
            # ever changes, then sample a wide character grid. The grid is
            # intentionally wider than tall to compensate for tall monospace
            # glyph cells; the rendered portrait itself remains square.
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
            grayscale = ImageEnhance.Contrast(grayscale).enhance(1.12)
            pixels = list(grayscale.getdata())

        rows = []
        for row in range(AVATAR_HEIGHT):
            values = pixels[row * AVATAR_WIDTH : (row + 1) * AVATAR_WIDTH]
            rows.append(
                "".join(
                    ASCII_CHARS[
                        min(
                            len(ASCII_CHARS) - 1,
                            round(pixel / 255 * (len(ASCII_CHARS) - 1)),
                        )
                    ]
                    for pixel in values
                )
            )
        return rows

    except Exception as exc:
        print(
            f"Warning: GitHub avatar conversion failed; using fallback art ({exc})",
            file=sys.stderr,
        )
        return fallback_avatar()

def collect_data() -> dict:
    profile = fetch_profile()
    repositories = fetch_repositories()
    events = fetch_public_events()

    languages = fetch_language_bytes(repositories)
    language_total = sum(languages.values())
    language_rows = sorted(languages.items(), key=lambda item: item[1], reverse=True)[:4]
    if language_total:
        language_rows = [
            (name, value, round(value / language_total * 100))
            for name, value in language_rows
        ]
    else:
        language_rows = [(name, value, 0) for name, value in language_rows]

    activity, activity_total = build_activity(events)

    owned = [repo for repo in repositories if not repo.get("fork")]
    featured_source = owned or repositories
    featured = sorted(
        featured_source,
        key=lambda repo: (
            repo.get("stargazers_count", 0),
            repo.get("updated_at", ""),
        ),
        reverse=True,
    )[:3]

    return {
        "name": profile.get("name") or USERNAME,
        "username": profile.get("login") or USERNAME,
        "avatar": avatar_to_ascii(profile.get("avatar_url")),
        "public_repos": int(profile.get("public_repos", len(repositories))),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in repositories),
        "followers": int(profile.get("followers", 0)),
        "following": int(profile.get("following", 0)),
        "commits": fetch_commit_count(),
        "activity": activity,
        "activity_total": activity_total,
        "languages": language_rows,
        "featured": featured,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def number(value) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def short_text(value: str, limit: int = 23) -> str:
    value = str(value or "Untitled")
    return value if len(value) <= limit else value[: limit - 1] + "…"


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
            "blue": "#79c0ff",
            "orange": "#ffa657",
            "purple": "#d2a8ff",
            "bar_bg": "#30363d",
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
            "blue": "#0969da",
            "orange": "#bc4c00",
            "purple": "#8250df",
            "bar_bg": "#d8dee4",
        }

    c = colors
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700" viewBox="0 0 1200 700">',
        '<style>',
        'text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}',
        '.ascii{white-space:pre;font-size:8.4px;letter-spacing:0px;}',
        '</style>',
        f'<rect width="1200" height="700" rx="18" fill="{c["background"]}"/>',
        f'<rect x="24" y="16" width="1152" height="36" rx="9" fill="{c["panel"]}" stroke="{c["border"]}"/>',
        f'<circle cx="43" cy="34" r="5" fill="{c["orange"]}"/>',
        f'<circle cx="61" cy="34" r="5" fill="{c["accent"]}"/>',
        f'<circle cx="79" cy="34" r="5" fill="{c["blue"]}"/>',
        f'<text x="98" y="39" font-size="13" font-weight="700" fill="{c["text"]}">{escape(USERNAME)}@github:~</text>',
        f'<text x="1154" y="38" font-size="9" text-anchor="end" letter-spacing="1.2" fill="{c["muted"]}">PROFILE TERMINAL // PUBLIC DATA</text>',
        f'<rect x="24" y="66" width="365" height="610" rx="14" fill="{c["panel"]}" stroke="{c["border"]}"/>',
        f'<rect x="408" y="66" width="768" height="610" rx="14" fill="{c["panel"]}" stroke="{c["border"]}"/>',
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

    # Left identity panel and ASCII portrait.
    text(44, 98, "IDENTITY // 001", 10, c["muted"], 700, spacing=1.2)
    parts.append(
        f'<text x="85" y="128" class="ascii" xml:space="preserve" fill="{c["accent"]}">'
    )
    for index, line in enumerate(data["avatar"]):
        y = 128 + index * 8.4
        parts.append(f'<tspan x="130" y="{y:.1f}">{escape(line)}</tspan>')
    parts.append("</text>")
    parts.append(f'<line x1="44" y1="414" x2="369" y2="414" stroke="{c["border"]}"/>')

    text(44, 452, short_text(data["name"], 22).upper(), 22, c["text"], 700)
    text(44, 475, f'@{data["username"]}', 12, c["accent"], 600)
    text(44, 514, "FOCUS", 9, c["muted"], 700, spacing=1.1)
    text(44, 534, "Developer tooling · CLI · reporting", 11, c["text"])
    text(44, 562, "WORKSTATION", 9, c["muted"], 700, spacing=1.1)
    text(44, 582, "macOS · Windows", 11, c["text"])
    text(44, 610, "AI TOOLCHAIN", 9, c["muted"], 700, spacing=1.1)
    text(44, 630, "Copilot · Claude Code", 11, c["text"])
    text(44, 654, "STATUS / PROFILE GENERATED", 8, c["muted"], 500, spacing=0.8)

    # Right-side terminal details.
    text(432, 99, "SYSTEM PROFILE", 15, c["text"], 700, spacing=0.8)
    text(1154, 98, "LIVE GITHUB TELEMETRY", 9, c["muted"], 600, "end", 1.0)

    details = [
        ("ROLE", "Software developer"),
        ("FOCUS", "Developer tooling · CLI · workflow · reporting"),
        ("STACK", "Python · Go · Vue · JavaScript"),
        ("ENV", "macOS · Windows"),
        ("TOOLS", "VS Code · PyCharm · iTerm2 · Copilot · Claude Code"),
    ]
    for index, (label, value) in enumerate(details):
        y = 143 + index * 23
        text(432, y, label, 9, c["muted"], 700, spacing=0.7)
        text(535, y, value, 11, c["text"], 500)

    # Metric cards.
    metrics = [
        ("PUBLIC REPOS", number(data["public_repos"]), "repositories"),
        ("STARS", number(data["stars"]), "across owned public repos"),
        ("FOLLOWERS", number(data["followers"]), "GitHub followers"),
        ("PUBLIC COMMITS", number(data["commits"]), "matched by GitHub author"),
    ]
    metric_x = [432, 616, 800, 984]
    for index, (label, value, note) in enumerate(metrics):
        x = metric_x[index]
        parts.append(
            f'<rect x="{x}" y="268" width="172" height="91" rx="10" fill="{c["panel_alt"]}" stroke="{c["border"]}"/>'
        )
        text(x + 14, 291, label, 9, c["muted"], 700, spacing=0.6)
        text(x + 14, 326, value, 25, c["text"], 700)
        text(x + 14, 346, note, 8, c["muted"], 400)

    # Public activity chart.
    parts.append(
        f'<rect x="432" y="375" width="350" height="168" rx="10" fill="{c["panel_alt"]}" stroke="{c["border"]}"/>'
    )
    text(450, 402, "PUBLIC ACTIVITY // 30 DAYS", 10, c["text"], 700, spacing=0.5)
    activity_total = data["activity_total"]
    activity_label = "unavailable" if activity_total is None else f"{activity_total:,} events"
    text(450, 423, activity_label, 9, c["muted"])
    values = data["activity"]
    maximum = max(values) if values else 0
    chart_x, baseline, bar_width, gap, chart_height = 450, 510, 7.2, 3.0, 52
    for index, value in enumerate(values):
        height = 3 if maximum == 0 else max(3, chart_height * value / maximum)
        x = chart_x + index * (bar_width + gap)
        y = baseline - height
        opacity = 0.32 if value == 0 else 0.55 + 0.45 * value / maximum
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width}" height="{height:.1f}" rx="2" fill="{c["accent"]}" opacity="{opacity:.2f}"/>'
        )
    parts.append(f'<line x1="450" y1="{baseline + 1}" x2="761" y2="{baseline + 1}" stroke="{c["border"]}"/>')
    text(450, 531, "30 DAYS AGO", 8, c["muted"])
    text(761, 531, "TODAY", 8, c["muted"], 500, "end")

    # Language distribution.
    parts.append(
        f'<rect x="798" y="375" width="358" height="168" rx="10" fill="{c["panel_alt"]}" stroke="{c["border"]}"/>'
    )
    text(816, 402, "LANGUAGE MIX // TOP 4", 10, c["text"], 700, spacing=0.5)
    languages = data["languages"]
    language_colors = [c["blue"], c["accent"], c["orange"], c["purple"]]
    if not languages:
        text(816, 455, "No public language data", 10, c["muted"])
    else:
        for index, (language, amount, percent) in enumerate(languages[:4]):
            y = 438 + index * 24
            text(816, y + 4, short_text(language, 13), 9, c["text"], 600)
            parts.append(
                f'<rect x="925" y="{y - 5}" width="176" height="7" rx="3.5" fill="{c["bar_bg"]}"/>'
            )
            width = max(2, 176 * percent / 100) if percent else 2
            parts.append(
                f'<rect x="925" y="{y - 5}" width="{width:.1f}" height="7" rx="3.5" fill="{language_colors[index]}"/>'
            )
            text(1138, y + 4, f"{percent}%", 9, c["muted"], 500, "end")

    # Featured repositories.
    parts.append(
        f'<rect x="432" y="555" width="724" height="91" rx="10" fill="{c["panel_alt"]}" stroke="{c["border"]}"/>'
    )
    text(450, 580, "FEATURED REPOSITORIES", 10, c["text"], 700, spacing=0.5)
    featured = data["featured"]
    if not featured:
        text(450, 615, "No public repositories found", 10, c["muted"])
    else:
        column_x = [450, 690, 930]
        for index, repo in enumerate(featured):
            x = column_x[index]
            text(x, 609, short_text(repo.get("name", "repository"), 24), 11, c["blue"], 600)
            stars = int(repo.get("stargazers_count", 0))
            language = repo.get("language") or "—"
            text(x, 630, f"★ {stars:,}   ·   {language}", 9, c["muted"])

    text(432, 665, "PUBLIC DATA ONLY  ·  UPDATED " + data["updated"], 8, c["muted"], 500, spacing=0.5)
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
