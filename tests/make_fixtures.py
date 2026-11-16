"""Write small test files in the exact shape of the NBA's public feeds
(scheduleLeagueV2.json and boxscore_<id>.json). Teams are real; every player
and stat line is invented, so tests never depend on live data."""
from __future__ import annotations

import json
import pathlib
import random

TEAMS = {"OKC": ("Oklahoma City", "Thunder"), "DEN": ("Denver", "Nuggets"), "BOS": ("Boston", "Celtics"),
         "NYK": ("New York", "Knicks"), "LAL": ("Los Angeles", "Lakers"), "LAC": ("LA", "Clippers")}
FIRST = ["Avery", "Brooks", "Corey", "Devin", "Elliot", "Flynn", "Grant", "Hayden", "Iker", "Jules",
         "Kai", "Lorenzo", "Milo", "Nash", "Orion", "Parker", "Quinn", "Reid", "Sasha", "Tate"]
LAST = ["Abara", "Bellweather", "Cordero", "Dunmore", "Ellery", "Fairbanks", "Gallo", "Hartwell", "Ishida",
        "Jovanović", "Kingsley", "Lacroix", "Monteiro", "Northcott", "Oyelaran", "Pellegrini", "Quist",
        "Radcliffe", "Stanišić", "Thorne", "Underwood", "Valdés", "Winslow", "Xu", "Yarrow", "Zeller",
        "Ashford", "Blackwood", "Castillo", "Dorsey", "Eastman", "Fontaine", "Gaskin", "Holt", "Irving-Lane",
        "Jessup", "Kaminski", "Lindgren", "Marsh", "Nobles", "Ortega", "Prentice", "Rollins", "Sutter",
        "Tolliver", "Vance", "Whitlock", "Yoon"]


def make(out: pathlib.Path, seed: int = 11) -> None:
    rng = random.Random(seed)
    (out / "boxscores").mkdir(parents=True, exist_ok=True)
    roster, pid, i = {}, 1630000, 0
    for tri in TEAMS:
        roster[tri] = []
        for slot in range(9):
            roster[tri].append((pid, f"{FIRST[i % 20]} {LAST[i % len(LAST)]}", 1.5 - slot * 0.12))
            pid += 1
            i += 1
    pairs = [("OKC", "DEN"), ("BOS", "NYK"), ("LAL", "LAC"), ("DEN", "BOS"), ("NYK", "OKC"), ("LAC", "DEN"),
             ("OKC", "LAL"), ("BOS", "LAC"), ("NYK", "LAL"), ("DEN", "OKC"), ("LAL", "BOS"), ("LAC", "NYK")]
    dates, games = {}, []
    for n, (home, away) in enumerate(pairs):
        day = f"2026-10-{21 + n // 3:02d}"
        gid = f"00226{n + 1:05d}"
        teams_json, scores = {}, {}
        for side, tri, opp in (("homeTeam", home, away), ("awayTeam", away, home)):
            players, total = [], 0
            for k, (p, name, skill) in enumerate(roster[tri]):
                out_reason = (tri == "OKC" and k == 1 and n in (4, 9))  # one OKC starter misses two games
                if out_reason:
                    players.append({"status": "INACTIVE", "notPlayingReason": "INACTIVE_INJURY",
                                    "notPlayingDescription": "Left Ankle; Sprain", "personId": p, "name": name,
                                    "played": "0", "starter": "0", "statistics": {}})
                    continue
                mins = max(8, round(rng.gauss(34 - k * 3, 2)))
                pts = max(0, round(rng.gauss(skill * 0.6 * mins, 3)))
                st = {"points": pts, "reboundsTotal": max(0, round(rng.gauss(mins * 0.18, 2))),
                      "assists": max(0, round(rng.gauss(mins * 0.12, 1.5))), "steals": rng.randint(0, 3),
                      "blocks": rng.randint(0, 2), "threePointersMade": rng.randint(0, 5),
                      "turnovers": rng.randint(0, 4), "fieldGoalsMade": pts // 2 - 1 if pts > 2 else 0,
                      "fieldGoalsAttempted": pts // 2 + 6, "plusMinusPoints": float(rng.randint(-15, 15)),
                      "minutes": f"PT{mins}M{rng.randint(0, 59):02d}.00S"}
                players.append({"status": "ACTIVE", "personId": p, "name": name, "played": "1",
                                "starter": "1" if k < 5 else "0", "statistics": st})
                total += pts
            teams_json[side] = {"teamId": 1610612700 + list(TEAMS).index(tri), "teamName": TEAMS[tri][1],
                                "teamCity": TEAMS[tri][0], "teamTricode": tri, "score": total, "players": players}
            scores[side] = total
        if scores["homeTeam"] == scores["awayTeam"]:
            teams_json["homeTeam"]["players"][0]["statistics"]["points"] += 1
            teams_json["homeTeam"]["score"] += 1
        box = {"meta": {"version": 1}, "game": {"gameId": gid, "gameTimeUTC": f"{day}T23:30:00Z",
                                                "gameEt": f"{day}T19:30:00-04:00", "gameStatus": 3,
                                                "gameStatusText": "Final", **teams_json}}
        (out / "boxscores" / f"{gid}.json").write_text(json.dumps(box))
        dates.setdefault(day, []).append({"gameId": gid, "gameStatus": 3,
                                          "homeTeam": {"teamTricode": home}, "awayTeam": {"teamTricode": away}})
    sched = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [
        {"gameDate": f"{d} 00:00:00", "games": g} for d, g in dates.items()]}}
    (out / "schedule.json").write_text(json.dumps(sched))


if __name__ == "__main__":
    make(pathlib.Path(__file__).parent / "fixtures")
