"""Turn a plain-English NBA question into a tool call (offline, deterministic)."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field

from .tools import Answer, Tools

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
ORDINALS = {"first": 0, "opening": 0, "second": 1, "third": 2, "last": -1, "most recent": -1,
            "latest": -1, "previous": -1}
STAT_WORDS = [("three", "threes"), ("3-pointer", "threes"), ("3pt", "threes"), ("triple", "threes"),
              ("rebound", "rebounds"), ("boards", "rebounds"), ("assist", "assists"), ("dimes", "assists"),
              ("steal", "steals"), ("block", "blocks"), ("turnover", "turnovers"),
              ("point", "points"), ("scor", "points"), ("ppg", "points")]
NICKNAMES = {"sixers": "PHI", "76ers": "PHI", "cavs": "CLE", "mavs": "DAL", "wolves": "MIN",
             "t-wolves": "MIN", "blazers": "POR", "pels": "NOP", "grizz": "MEM", "dubs": "GSW",
             "okc": "OKC", "nola": "NOP"}
UNTRACKED = (r"\b(coach\w*|salary|salaries|contracts?|draft\w*|trades?|traded|odds|betting|mvp|"
             r"championships?|titles?|playoffs?|all-stars?|injury report|free agen\w*)\b")


def fold(s: str) -> str:
    """Lowercase and strip accents so 'Jokic' matches 'Jokić'."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


@dataclass
class Entities:
    teams: list[str] = field(default_factory=list)
    players: list[int] = field(default_factory=list)
    date: str | None = None
    stat: str | None = None
    ordinal: int | None = None
    last_n: int | None = None
    conference: str | None = None


class Router:
    def __init__(self, tools: Tools):
        self.t = tools
        self.reload()

    def reload(self) -> None:
        t = self.t
        aliases: list[tuple[str, str]] = []
        for tid, info in t.team.items():
            for a in {info["full_name"], info["name"]}:
                aliases.append((fold(a), tid))
            if info["city"] not in ("LA", "Los Angeles"):  # two LA teams: require the nickname
                aliases.append((fold(info["city"]), tid))
        aliases += [(k, v) for k, v in NICKNAMES.items() if v in t.team]
        self.team_aliases = sorted(aliases, key=lambda a: -len(a[0]))

        last_count: dict[str, int] = {}
        for p in t.player.values():
            last = fold(p["name"].split()[-1])
            last_count[last] = last_count.get(last, 0) + 1
        pa = []
        self.shared_last: dict[str, list[str]] = {}
        for pid, p in t.player.items():
            pa.append((fold(p["name"]), pid))
            parts = p["name"].split()
            last = fold(parts[-1])
            if last in {"jr.", "sr.", "ii", "iii", "iv"} and len(parts) > 2:
                last = fold(parts[-2])
            if last_count.get(last, 0) == 1 and len(last) > 3:
                pa.append((last, pid))
            elif len(last) > 3:
                self.shared_last.setdefault(last, []).append(p["name"])
        self.player_aliases = sorted(pa, key=lambda a: -len(a[0]))

    def extract(self, question: str) -> Entities:
        text = fold(question)
        e = Entities()
        masked = text

        def take(m):
            nonlocal masked
            masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]

        for alias, pid in self.player_aliases:
            m = re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", masked)
            if m and pid not in e.players:
                e.players.append(pid)
                take(m)
        found = []
        for alias, tid in self.team_aliases:
            for m in re.finditer(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", masked):
                found.append((m.start(), tid))
                take(m)
        for m in re.finditer(r"\b([A-Z]{3})\b", question):  # tricodes, capitals only
            if m.group(1) in self.t.team and fold(m.group(1)) in masked[m.start():m.end()]:
                found.append((m.start(), m.group(1)))
        for _, tid in sorted(found):
            if tid not in e.teams:
                e.teams.append(tid)

        if m := re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text):
            e.date = m.group(1)
        elif m := re.search(rf"\b({'|'.join(MONTHS)})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text):
            month, day = MONTHS[m.group(1)], int(m.group(2))
            year = 2026 if month >= 8 else 2027  # 2026-27 season
            try:
                e.date = dt.date(year, month, day).isoformat()
            except ValueError:
                pass
        elif re.search(r"\b(last night|yesterday)\b", text):
            e.date = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        elif re.search(r"\btonight|today\b", text):
            e.date = dt.date.today().isoformat()
        for word, stat in STAT_WORDS:
            if word in text:
                e.stat = stat
                break
        if m := re.search(r"\blast (\d{1,2}|five|ten|three) games\b", text):
            e.last_n = {"five": 5, "ten": 10, "three": 3}.get(m.group(1)) or int(m.group(1))
        elif re.search(r"\blately|recently|hot streak|cold streak\b", text):
            e.last_n = 5
        else:
            for word, idx in ORDINALS.items():
                if re.search(rf"\b{word}\b", text):
                    e.ordinal = idx
                    break
        if re.search(r"\b(east|eastern)\b", text):
            e.conference = "East"
        elif re.search(r"\b(west|western)\b", text):
            e.conference = "West"
        return e

    def route(self, question: str) -> Answer:
        q = fold(question)
        e = self.extract(question)
        stat = e.stat or "points"

        if not e.players:
            for last, names in self.shared_last.items():
                if re.search(rf"(?<![a-z]){re.escape(last)}(?![a-z])", q):
                    return Answer(f"Which one? More than one player is named {names[0].split()[-1]}: "
                                  f"{', '.join(sorted(names))}.", answerable=False, tool="clarify")
        if m := re.search(UNTRACKED, q):
            return Answer.unknown(f"this database only has game results and box scores, nothing about {m.group(1)}",
                                  "router")
        if e.players and re.search(r"\b(why|miss|missed|missing|sit|sat|out|injur\w*|hurt|inactive|absent|dnp)\b", q):
            return self.t.why_missed(e.players[0], e.teams, e.date)
        if not e.players and re.search(r"standings|best record|worst record|first place|top of the|\bseeds?\b|"
                                       r"who.s the best team|best team", q):
            return self.t.standings(e.conference)
        if not e.teams and not e.players and re.search(
                r"lead(s|ing)? the (league|nba|east|west)|league leader|in the (league|nba)|most .* per game|"
                r"averag\w* the most|top scorer|leading scorer|best scorer", q):
            return self.t.league_leader(stat, e.conference)
        if len(e.teams) == 1 and not e.players and not e.date and e.ordinal is None and re.search(
                r"lead(s|ing)? the|leading scorer|top scorer|best scorer|who.s the best|most .* per game", q):
            return self.t.team_leader(e.teams[0], stat)
        if e.teams and re.search(r"\b(biggest|largest|worst|widest)\b", q) and re.search(
                r"\b(win|loss|lost|defeat|blowout|margin)", q):
            kind = "loss" if re.search(r"\b(loss|lost|defeat|worst)\b", q) else "win"
            return self.t.extreme_margin(e.teams[0], kind)
        if len(e.teams) >= 2 and re.search(r"head.to.head|season series|record (against|vs)|against each other|"
                                           r"how many times", q):
            return self.t.head_to_head(e.teams[0], e.teams[1])
        if e.teams and not e.players and re.search(r"\brecord\b|how many (games|wins)|\bw-l\b|win-loss", q):
            return self.t.team_record(e.teams[0], e.last_n)
        if e.players and (e.last_n or re.search(r"\baverag\w*|per game|\bppg\b|this season", q)):
            return self.t.player_average(e.players[0], stat, e.last_n)
        if e.teams and re.search(r"top scorer|leading scorer|led .* (in|with)|most (points|rebounds|assists|boards|"
                                 r"dimes|steals|blocks|threes|3s)|high scorer|who scored the most", q):
            team_only = None
            if m := re.search(r"which (\w+(?: \w+)?) player|for the (\w+(?: \w+)?)", q):
                named = self.extract(m.group(1) or m.group(2)).teams
                team_only = named[0] if named else None
            return self.t.game_leader(e.teams, stat, e.date, e.ordinal, team_only)
        if e.players:
            return self.t.player_game_line(e.players[0], e.teams, e.date, e.ordinal)
        if e.teams and re.search(r"\bscore\b|final|who won|result|\bbeat\b|\bwin\b|\bwon\b|\blose\b|\blost\b|"
                                 r"how did|what happened|game", q):
            return self.t.game_result(e.teams, e.date, e.ordinal)
        return Answer.unknown("I couldn't match that to a team, player or stat in this season's data", "router")
