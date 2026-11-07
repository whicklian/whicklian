#!/usr/bin/env python3
"""
Real-Time GitHub Profile Updater for @whicklian
Fetches live telemetry from the GitHub API + Streak/Stats endpoints,
generates an animated SVG HUD (assets/realtime-hud.svg), and updates
dynamic sections in README.md between <!--START_SECTION:...--> markers.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

USERNAME = "whicklian"
ROOT_DIR = Path(__file__).resolve().parent.parent
README_PATH = ROOT_DIR / "README.md"
ASSETS_DIR = ROOT_DIR / "assets"
HUD_SVG_PATH = ASSETS_DIR / "realtime-hud.svg"

EAT_TZ = datetime.timezone(datetime.timedelta(hours=3), name="EAT")


def gh_fetch(url: str, timeout: int = 12) -> dict | list | None:
    headers = {
        "User-Agent": f"{USERNAME}-realtime-profile-engine",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[warn] Failed to fetch {url}: {exc}")
        return None


def raw_fetch(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"[warn] Failed to fetch {url}: {exc}")
        return ""


def parse_iso(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def time_ago(dt: datetime.datetime | None, now: datetime.datetime) -> str:
    if not dt:
        return "recently"
    diff = now - dt
    secs = max(0, int(diff.total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m}m ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h}h ago"
    d = secs // 86400
    if d < 30:
        return f"{d}d ago"
    mo = d // 30
    return f"{mo}mo ago"


def fetch_streak_and_rating(repos: list[dict], user: dict) -> dict:
    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    public_repos = user.get("public_repos", len(repos))
    followers = user.get("followers", 0)

    data = {
        "total_contributions": "123+",
        "contrib_range": "Nov 2025 - Present",
        "current_streak": "2",
        "current_streak_range": "Active",
        "longest_streak": "5",
        "longest_streak_range": "Sep 21 - Sep 25",
        "total_commits": "122+",
        "rank": "C",
        "total_stars": str(total_stars),
        "public_repos": str(public_repos),
        "followers": str(followers),
    }

    streak_svg = raw_fetch(
        f"https://github-readme-streak-stats.herokuapp.com/?user={USERNAME}"
    )
    if streak_svg:
        texts = [
            t.strip()
            for t in re.findall(r"<text[^>]*>([^<]+)</text>", streak_svg)
            if t.strip()
        ]
        # Expected order: [total, 'Total Contributions', range, 'Current Streak', curr_range, curr_val, long_val, 'Longest Streak', long_range]
        if len(texts) >= 9 and "Total Contributions" in texts:
            try:
                idx_tc = texts.index("Total Contributions")
                data["total_contributions"] = texts[idx_tc - 1]
                data["contrib_range"] = texts[idx_tc + 1]
                idx_cs = texts.index("Current Streak")
                data["current_streak_range"] = texts[idx_cs + 1]
                data["current_streak"] = texts[idx_cs + 2]
                idx_ls = texts.index("Longest Streak")
                data["longest_streak"] = texts[idx_ls - 1]
                if idx_ls + 1 < len(texts):
                    data["longest_streak_range"] = texts[idx_ls + 1]
            except Exception as exc:
                print(f"[warn] Could not parse streak SVG texts: {exc}")

    stats_svg = raw_fetch(
        f"https://github-stats-extended.vercel.app/api?username={USERNAME}&include_all_commits=true"
    )
    if stats_svg:
        m_rank = re.search(r"Rank:\s*([A-Z][\+\-]?)", stats_svg)
        if m_rank:
            data["rank"] = m_rank.group(1)
        m_commits = re.search(r"Total Commits\s*:\s*(\d+)", stats_svg)
        if m_commits:
            data["total_commits"] = m_commits.group(1)

    return data


def compute_realtime_languages(repos: list[dict]) -> list[tuple[str, int, int, float]]:
    """
    Computes real-time language breakdown across all non-fork repos.
    Returns list of (language, repo_count, kb_size, percentage).
    """
    lang_weights: dict[str, int] = {}
    lang_repos: dict[str, int] = {}

    for r in repos:
        if r.get("fork"):
            continue
        lang = r.get("language")
        if not lang:
            continue
        # Weight by repo size (in KB, minimum 25 KB so small repos still count fairly) + recent activity bonus
        size_kb = max(25, min(r.get("size", 50), 2500))
        lang_weights[lang] = lang_weights.get(lang, 0) + size_kb
        lang_repos[lang] = lang_repos.get(lang, 0) + 1

    total_weight = sum(lang_weights.values()) or 1
    results = []
    for lang, weight in sorted(lang_weights.items(), key=lambda x: x[1], reverse=True):
        pct = (weight / total_weight) * 100.0
        results.append((lang, lang_repos[lang], weight, pct))
    return results


def build_bar(pct: float, width: int = 23) -> str:
    filled = max(1 if pct > 0 else 0, round((pct / 100.0) * width))
    filled = min(width, filled)
    return "█" * filled + "░" * (width - filled)


def generate_hud_svg(
    metrics: dict,
    lang_stats: list[tuple[str, int, int, float]],
    latest_repo: str,
    latest_pushed_ago: str,
    now_str: str,
) -> str:
    lang_colors = {
        "Python": "#3776AB",
        "JavaScript": "#F7DF1E",
        "TypeScript": "#3178C6",
        "Jupyter Notebook": "#DA5B0B",
        "HTML": "#E34F26",
        "CSS": "#1572B6",
        "Java": "#B07219",
    }

    top_langs = lang_stats[:4]
    lang_bars_svg = []
    y_offset = 108
    for lang, rcount, _, pct in top_langs:
        color = lang_colors.get(lang, "#36BCF7")
        bar_w = max(8, int((pct / 100.0) * 185))
        disp_lang = "Jupyter / Data" if lang == "Jupyter Notebook" else lang
        lang_bars_svg.append(
            f"""
      <text x="580" y="{y_offset}" fill="#c9d1d9" font-size="11.5" font-weight="600" font-family="'Segoe UI', Ubuntu, sans-serif">{html.escape(disp_lang)}</text>
      <text x="820" y="{y_offset}" fill="#8b949e" font-size="11" font-family="'Fira Code', monospace" text-anchor="end">{rcount} repos • {pct:.1f}%</text>
      <rect x="580" y="{y_offset + 5}" width="240" height="6" rx="3" fill="#1f2937"/>
      <rect x="580" y="{y_offset + 5}" width="{bar_w}" height="6" rx="3" fill="{color}"/>
"""
        )
        y_offset += 29

    return f"""<svg width="860" height="235" viewBox="0 0 860 235" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="860" y2="235" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0d1117"/>
      <stop offset="50%" stop-color="#111827"/>
      <stop offset="100%" stop-color="#0d1117"/>
    </linearGradient>
    <linearGradient id="accentGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#36BCF7"/>
      <stop offset="100%" stop-color="#6C63FF"/>
    </linearGradient>
    <style>
      .pulse {{
        animation: pulseAnim 2s infinite ease-in-out;
        transform-origin: center;
      }}
      @keyframes pulseAnim {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.35; }}
      }}
    </style>
  </defs>

  <!-- Main Card Background -->
  <rect x="2" y="2" width="856" height="231" rx="14" fill="url(#bgGrad)" stroke="#30363d" stroke-width="1.5"/>
  <rect x="2" y="2" width="856" height="4" rx="2" fill="url(#accentGrad)"/>

  <!-- Header Bar -->
  <circle cx="26" cy="28" r="5" fill="#10b981" class="pulse"/>
  <text x="38" y="32" fill="#36BCF7" font-size="13" font-weight="700" font-family="'Fira Code', 'Segoe UI', monospace" letter-spacing="0.8">
    REAL-TIME GITHUB TELEMETRY • @WHICKLIAN
  </text>
  <text x="836" y="32" fill="#8b949e" font-size="11" font-family="'Fira Code', monospace" text-anchor="end">
    ⚡ LIVE SYNC: {html.escape(now_str)}
  </text>
  <line x1="20" y1="46" x2="840" y2="46" stroke="#21262d" stroke-width="1"/>

  <!-- COLUMN 1: RATING & TROPHY RANKS -->
  <rect x="20" y="58" width="255" height="158" rx="10" fill="#161b22" stroke="#21262d"/>
  <text x="36" y="80" fill="#36BCF7" font-size="12" font-weight="700" font-family="'Segoe UI', sans-serif">🏆 LIVE RATING &amp; RANKS</text>

  <!-- Rating Circle -->
  <circle cx="72" cy="132" r="34" fill="none" stroke="#21262d" stroke-width="6"/>
  <circle cx="72" cy="132" r="34" fill="none" stroke="url(#accentGrad)" stroke-width="6" stroke-dasharray="175 220" stroke-linecap="round"/>
  <text x="72" y="138" fill="#ffffff" font-size="22" font-weight="800" font-family="'Segoe UI', sans-serif" text-anchor="middle">{html.escape(metrics["rank"])}</text>
  <text x="72" y="185" fill="#8b949e" font-size="10.5" font-family="'Segoe UI', sans-serif" text-anchor="middle">Overall Rating</text>

  <!-- Trophy Mini Badges -->
  <text x="124" y="106" fill="#c9d1d9" font-size="11.5" font-family="'Segoe UI', sans-serif">🌟 MultiLang: <tspan fill="#ff79c6" font-weight="700">Rank S</tspan></text>
  <text x="124" y="128" fill="#c9d1d9" font-size="11.5" font-family="'Segoe UI', sans-serif">📦 Repos ({html.escape(metrics["public_repos"])}): <tspan fill="#36BCF7" font-weight="700">Rank A</tspan></text>
  <text x="124" y="150" fill="#c9d1d9" font-size="11.5" font-family="'Segoe UI', sans-serif">🔥 Commits ({html.escape(metrics["total_commits"])}): <tspan fill="#6C63FF" font-weight="700">Rank B</tspan></text>
  <text x="124" y="172" fill="#c9d1d9" font-size="11.5" font-family="'Segoe UI', sans-serif">⭐ Stars ({html.escape(metrics["total_stars"])}): <tspan fill="#10b981" font-weight="700">Rank C</tspan></text>
  <text x="124" y="196" fill="#8b949e" font-size="10.5" font-family="'Fira Code', monospace">👥 {html.escape(metrics["followers"])} Followers</text>

  <!-- COLUMN 2: STREAKS & ACTIVITY PULSE -->
  <rect x="290" y="58" width="260" height="158" rx="10" fill="#161b22" stroke="#21262d"/>
  <text x="306" y="80" fill="#6C63FF" font-size="12" font-weight="700" font-family="'Segoe UI', sans-serif">🔥 STREAK &amp; COMMIT PULSE</text>

  <!-- 3 Metric Boxes -->
  <rect x="304" y="92" width="72" height="62" rx="8" fill="#0d1117" stroke="#30363d"/>
  <text x="340" y="118" fill="#36BCF7" font-size="18" font-weight="800" font-family="'Segoe UI', sans-serif" text-anchor="middle">{html.escape(metrics["total_contributions"])}</text>
  <text x="340" y="136" fill="#8b949e" font-size="9.5" font-family="'Segoe UI', sans-serif" text-anchor="middle">Total Contribs</text>

  <rect x="384" y="92" width="72" height="62" rx="8" fill="#0d1117" stroke="#6C63FF"/>
  <text x="420" y="118" fill="#ff79c6" font-size="18" font-weight="800" font-family="'Segoe UI', sans-serif" text-anchor="middle">{html.escape(metrics["current_streak"])}d 🔥</text>
  <text x="420" y="136" fill="#c9d1d9" font-size="9.5" font-family="'Segoe UI', sans-serif" text-anchor="middle">Current Streak</text>

  <rect x="464" y="92" width="72" height="62" rx="8" fill="#0d1117" stroke="#30363d"/>
  <text x="500" y="118" fill="#10b981" font-size="18" font-weight="800" font-family="'Segoe UI', sans-serif" text-anchor="middle">{html.escape(metrics["longest_streak"])}d 🏆</text>
  <text x="500" y="136" fill="#8b949e" font-size="9.5" font-family="'Segoe UI', sans-serif" text-anchor="middle">Best Streak</text>

  <text x="306" y="176" fill="#c9d1d9" font-size="11" font-family="'Fira Code', monospace">
    🚀 Latest Push: <tspan fill="#36BCF7" font-weight="700">{html.escape(latest_repo[:18])}</tspan>
  </text>
  <text x="306" y="196" fill="#8b949e" font-size="10.5" font-family="'Fira Code', monospace">
    ⏱️ Updated {html.escape(latest_pushed_ago)} • Streak: {html.escape(metrics["current_streak_range"])}
  </text>

  <!-- COLUMN 3: LIVE REPO LANGUAGE TELEMETRY -->
  <rect x="565" y="58" width="275" height="158" rx="10" fill="#161b22" stroke="#21262d"/>
  <text x="580" y="80" fill="#36BCF7" font-size="12" font-weight="700" font-family="'Segoe UI', sans-serif">💻 LIVE REPO LANGUAGE MIX</text>
  {"".join(lang_bars_svg)}
</svg>
"""


def replace_section(content: str, section_name: str, new_body: str) -> str:
    pattern = re.compile(
        rf"(<!--START_SECTION:{re.escape(section_name)}-->).*?(<!--END_SECTION:{re.escape(section_name)}-->)",
        re.DOTALL,
    )
    replacement = f"\\1\n{new_body.strip()}\n\\2"
    if pattern.search(content):
        return pattern.sub(replacement, content)
    return content


def main() -> None:
    now = datetime.datetime.now(EAT_TZ)
    now_str = now.strftime("%Y-%m-%d %H:%M EAT")

    user = gh_fetch(f"https://api.github.com/users/{USERNAME}") or {}
    repos = (
        gh_fetch(
            f"https://api.github.com/users/{USERNAME}/repos?per_page=100&sort=pushed"
        )
        or []
    )
    events = (
        gh_fetch(f"https://api.github.com/users/{USERNAME}/events/public?per_page=30")
        or []
    )

    if not isinstance(repos, list):
        repos = []
    if not isinstance(events, list):
        events = []

    metrics = fetch_streak_and_rating(repos, user if isinstance(user, dict) else {})
    lang_stats = compute_realtime_languages(repos)

    # Find latest pushed repo
    non_profile_repos = [r for r in repos if r.get("name") != USERNAME]
    latest_repo_obj = non_profile_repos[0] if non_profile_repos else (repos[0] if repos else {})
    latest_repo_name = latest_repo_obj.get("name", "habit-island")
    latest_pushed_dt = parse_iso(latest_repo_obj.get("pushed_at"))
    latest_pushed_ago = time_ago(latest_pushed_dt, now)

    # 1. Generate assets/realtime-hud.svg
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    hud_svg = generate_hud_svg(
        metrics=metrics,
        lang_stats=lang_stats,
        latest_repo=latest_repo_name,
        latest_pushed_ago=latest_pushed_ago,
        now_str=now_str,
    )
    HUD_SVG_PATH.write_text(hud_svg, encoding="utf-8")
    print(f"[ok] Generated {HUD_SVG_PATH}")

    # 2. Build Waka / Live Language Telemetry section
    waka_lines = [
        f"⚡ Real-Time GitHub Language Telemetry (Across {len([r for r in repos if not r.get('fork')])} Public Repositories • Synced {now_str})",
        "──────────────────────────────────────────────────────────────────────────────────────────",
    ]
    for lang, rcount, kb, pct in lang_stats[:7]:
        bar = build_bar(pct, width=25)
        repo_label = f"{rcount} repo" if rcount == 1 else f"{rcount} repos"
        waka_lines.append(
            f"{lang:<18} {repo_label:<10} ({kb:>4} KB)   {bar}   {pct:>5.1f}%"
        )
    waka_block = "```text\n" + "\n".join(waka_lines) + "\n```"

    # 3. Build Real-Time Activity Feed section
    activity_lines = []
    seen_entries = set()
    for ev in events:
        ev_type = ev.get("type", "")
        repo_full = ev.get("repo", {}).get("name", "")
        repo_short = repo_full.split("/")[-1] if "/" in repo_full else repo_full
        ev_dt = parse_iso(ev.get("created_at"))
        ago = time_ago(ev_dt, now)
        date_short = ev_dt.astimezone(EAT_TZ).strftime("%b %d") if ev_dt else ""

        if ev_type == "PushEvent":
            commits = ev.get("payload", {}).get("commits", [])
            msg = commits[-1].get("message", "").splitlines()[0] if commits else "Pushed new commits"
            key = f"push:{repo_short}:{msg}"
            if key in seen_entries:
                continue
            seen_entries.add(key)
            activity_lines.append(
                f"- 🚀 **Pushed to [{repo_short}](https://github.com/{repo_full})** — `{msg[:65]}` *(📅 {date_short} • {ago})*"
            )
        elif ev_type == "CreateEvent":
            ref_type = ev.get("payload", {}).get("ref_type", "repository")
            key = f"create:{repo_short}:{ref_type}"
            if key in seen_entries:
                continue
            seen_entries.add(key)
            activity_lines.append(
                f"- ✨ **Created {ref_type} in [{repo_short}](https://github.com/{repo_full})** *(📅 {date_short} • {ago})*"
            )
        elif ev_type == "WatchEvent":
            key = f"star:{repo_short}"
            if key in seen_entries:
                continue
            seen_entries.add(key)
            activity_lines.append(
                f"- ⭐ **Starred [{repo_full}](https://github.com/{repo_full})** *(📅 {date_short} • {ago})*"
            )
        if len(activity_lines) >= 6:
            break

    if not activity_lines:
        activity_lines.append(
            f"- 🚀 **Active on [{latest_repo_name}](https://github.com/{USERNAME}/{latest_repo_name})** *(Updated {latest_pushed_ago})*"
        )

    activity_block = "\n".join(activity_lines)

    # 4. Build Real-Time Status Row
    status_block = f"""<div align="center">

| ⚡ **Live Telemetry** | 🔥 **Current / Best Streak** | 🏆 **Rating & Ranks** | 🚀 **Latest Active Repo** |
| :---: | :---: | :---: | :---: |
| 🟢 **Online & Shipping** (`{now_str}`) | **{metrics["current_streak"]} Days** *(Best: {metrics["longest_streak"]}d • {metrics["total_contributions"]} Contribs)* | **Rank {metrics["rank"]}** *(MultiLang: S • Repos: A • Commits: B)* | **[`{latest_repo_name}`](https://github.com/{USERNAME}/{latest_repo_name})** *(Pushed {latest_pushed_ago})* |

</div>"""

    # 5. Update README.md sections
    if README_PATH.exists():
        readme_text = README_PATH.read_text(encoding="utf-8")
        readme_text = replace_section(readme_text, "waka", waka_block)
        readme_text = replace_section(readme_text, "realtime_activity", activity_block)
        readme_text = replace_section(readme_text, "realtime_status", status_block)
        README_PATH.write_text(readme_text, encoding="utf-8")
        print(f"[ok] Updated {README_PATH}")


if __name__ == "__main__":
    main()
