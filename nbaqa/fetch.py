"""Download the current NBA season from the league's public JSON feeds.

  python -m nbaqa.fetch            # pull every finished regular-season game
  python -m nbaqa.fetch --limit 20 # quick smoke test

Raw JSON is cached in data/raw/, so re-running only downloads games that
finished since the last run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.request

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
BOX_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (nba-qa-api; personal project)",
           "Referer": "https://www.nba.com/", "Accept": "application/json"}
RAW = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"


def get_json(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def finished_regular_season_games(schedule: dict) -> list[str]:
    """Game IDs look like 0022600123: '002' means regular season (NBA Cup group games included)."""
    ids = []
    for day in schedule["leagueSchedule"]["gameDates"]:
        for g in day["games"]:
            if g["gameId"].startswith("002") and g.get("gameStatus") == 3:
                ids.append(g["gameId"])
    return ids


def fetch(raw_dir: pathlib.Path = RAW, limit: int | None = None, pause: float = 0.3) -> int:
    (raw_dir / "boxscores").mkdir(parents=True, exist_ok=True)
    schedule = get_json(SCHEDULE_URL)
    (raw_dir / "schedule.json").write_text(json.dumps(schedule))
    season = schedule["leagueSchedule"].get("seasonYear", "?")
    ids = finished_regular_season_games(schedule)
    todo = [g for g in ids if not (raw_dir / "boxscores" / f"{g}.json").exists()]
    if limit:
        todo = todo[:limit]
    print(f"Season {season}: {len(ids)} finished games, downloading {len(todo)} new box scores")
    for i, gid in enumerate(todo, 1):
        box = get_json(BOX_URL.format(game_id=gid))
        (raw_dir / "boxscores" / f"{gid}.json").write_text(json.dumps(box))
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}")
        time.sleep(pause)  # be polite to the CDN
    return len(todo)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    fetch(limit=ap.parse_args().limit)
