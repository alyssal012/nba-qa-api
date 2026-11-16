"""Turn the cached NBA JSON into a SQLite database."""
from __future__ import annotations

import json
import pathlib
import re
import sqlite3

from .fetch import RAW

CONFERENCE = {t: "East" for t in "ATL BOS BKN CHA CHI CLE DET IND MIA MIL NYK ORL PHI TOR WAS".split()}
CONFERENCE |= {t: "West" for t in "DAL DEN GSW HOU LAC LAL MEM MIN NOP OKC PHX POR SAC SAS UTA".split()}

SCHEMA = """
CREATE TABLE teams (team_id TEXT PRIMARY KEY, city TEXT, name TEXT, full_name TEXT, conference TEXT);
CREATE TABLE players (player_id INTEGER PRIMARY KEY, name TEXT, team_id TEXT);
CREATE TABLE games (game_id TEXT PRIMARY KEY, date TEXT, home_team_id TEXT, away_team_id TEXT,
                    home_points INTEGER, away_points INTEGER, winner_team_id TEXT);
CREATE TABLE box_scores (game_id TEXT, player_id INTEGER, team_id TEXT, starter INTEGER,
                         minutes REAL, points INTEGER, rebounds INTEGER, assists INTEGER,
                         steals INTEGER, blocks INTEGER, threes INTEGER, turnovers INTEGER,
                         fgm INTEGER, fga INTEGER, plus_minus REAL,
                         PRIMARY KEY (game_id, player_id));
CREATE TABLE inactive (game_id TEXT, player_id INTEGER, team_id TEXT, reason TEXT, description TEXT,
                       PRIMARY KEY (game_id, player_id));
"""


def minutes(iso: str | None) -> float:
    """'PT34M12.00S' -> 34.2"""
    m = re.match(r"PT(\d+)M([\d.]+)S", iso or "")
    return round(int(m.group(1)) + float(m.group(2)) / 60, 1) if m else 0.0


def load_box(con: sqlite3.Connection, box: dict) -> None:
    g = box["game"]
    gid = g["gameId"]
    date = (g.get("gameEt") or g["gameTimeUTC"])[:10]
    home, away = g["homeTeam"], g["awayTeam"]
    for t in (home, away):
        tri = t["teamTricode"]
        con.execute("INSERT OR IGNORE INTO teams VALUES (?,?,?,?,?)",
                    (tri, t["teamCity"], t["teamName"], f"{t['teamCity']} {t['teamName']}",
                     CONFERENCE.get(tri, "")))
    winner = home["teamTricode"] if home["score"] > away["score"] else away["teamTricode"]
    con.execute("INSERT OR REPLACE INTO games VALUES (?,?,?,?,?,?,?)",
                (gid, date, home["teamTricode"], away["teamTricode"], home["score"], away["score"], winner))
    for t in (home, away):
        tri = t["teamTricode"]
        for p in t.get("players", []):
            pid = p["personId"]
            con.execute("INSERT OR REPLACE INTO players VALUES (?,?,?)", (pid, p["name"], tri))
            if p.get("played") == "1":
                s = p["statistics"]
                con.execute("INSERT OR REPLACE INTO box_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (gid, pid, tri, int(p.get("starter") == "1"), minutes(s.get("minutes")),
                             s["points"], s["reboundsTotal"], s["assists"], s["steals"], s["blocks"],
                             s["threePointersMade"], s["turnovers"], s["fieldGoalsMade"],
                             s["fieldGoalsAttempted"], s.get("plusMinusPoints", 0.0)))
            elif p.get("notPlayingReason") or p.get("status") == "INACTIVE":
                con.execute("INSERT OR REPLACE INTO inactive VALUES (?,?,?,?,?)",
                            (gid, pid, tri, p.get("notPlayingReason", "INACTIVE"),
                             p.get("notPlayingDescription", "")))


def build(raw_dir: pathlib.Path = RAW, path: str = ":memory:") -> sqlite3.Connection:
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    files = sorted((raw_dir / "boxscores").glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No box scores in {raw_dir}. Run `python -m nbaqa.fetch` first.")
    for f in files:  # sorted by game ID, so the latest team for traded players wins
        load_box(con, json.loads(f.read_text()))
    con.commit()
    return con
