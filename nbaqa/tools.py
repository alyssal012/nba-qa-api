"""Query tools. Each returns an Answer with the source rows it used, or
answerable=False instead of a guess."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field

STATS = ("points", "rebounds", "assists", "steals", "blocks", "threes", "turnovers")
LABEL = {"threes": "threes", "points": "points", "rebounds": "rebounds", "assists": "assists",
         "steals": "steals", "blocks": "blocks", "turnovers": "turnovers"}


@dataclass
class Answer:
    answer: str
    answerable: bool = True
    tool: str = ""
    evidence: list[str] = field(default_factory=list)
    value: object = None

    @classmethod
    def unknown(cls, why: str, tool: str = "") -> "Answer":
        return cls(answer=f"I can't answer that from the data: {why}", answerable=False, tool=tool)

    def to_dict(self) -> dict:
        return asdict(self)


def poss(name: str) -> str:
    return name + ("'" if name.endswith("s") else "'s")


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class Tools:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.refresh_cache()

    def refresh_cache(self) -> None:
        self.team = {r["team_id"]: dict(r) for r in self.q("SELECT * FROM teams")}
        self.player = {r["player_id"]: dict(r) for r in self.q("SELECT * FROM players")}

    def q(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.con.execute(sql, args).fetchall()

    def tn(self, t: str) -> str:
        return self.team[t]["full_name"]

    def find_games(self, teams: list[str], date: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM games WHERE 1=1", []
        for t in teams[:2]:
            sql += " AND (home_team_id = ? OR away_team_id = ?)"
            args += [t, t]
        if date:
            sql += " AND date = ?"
            args.append(date)
        return self.q(sql + " ORDER BY date, game_id", *args)

    def pick_game(self, teams, date=None, ordinal=None, default_latest=False):
        games = self.find_games(teams, date)
        if not games:
            return None, "no finished game matches those teams and date"
        if ordinal is not None:
            try:
                return games[ordinal], None
            except IndexError:
                return None, f"there are only {len(games)} matching games so far"
        if len(games) > 1 and not default_latest:
            dates = ", ".join(g["date"] for g in games[-6:])
            return None, f"{len(games)} games match (latest: {dates}); say which date"
        return games[-1], None

    @staticmethod
    def score(g) -> str:
        hi, lo = sorted([g["home_points"], g["away_points"]], reverse=True)
        return f"{hi}-{lo}"

    def min_games(self) -> int:
        """Leaderboards need a games-played floor, as the NBA uses (about 70% of team games)."""
        row = self.q("SELECT MAX(n) AS n FROM (SELECT COUNT(*) AS n FROM games GROUP BY home_team_id)")
        team_games = max((r["n"] or 0) for r in row) * 2
        return max(1, int(team_games * 0.7))

    # ---------- game tools ----------
    def game_result(self, teams, date=None, ordinal=None) -> Answer:
        g, err = self.pick_game(teams, date, ordinal, default_latest=len(teams) == 1)
        if err:
            return Answer.unknown(err, "game_result")
        loser = g["away_team_id"] if g["winner_team_id"] == g["home_team_id"] else g["home_team_id"]
        ev = [f"games:{g['game_id']}"]
        parts = []
        for t in (g["winner_team_id"], loser):
            r = self.q("SELECT * FROM box_scores WHERE game_id=? AND team_id=? ORDER BY points DESC, player_id LIMIT 1",
                       g["game_id"], t)
            if r:
                parts.append(f"{self.player[r[0]['player_id']]['name']} scored {r[0]['points']} for {self.team[t]['name']}")
                ev.append(f"box_scores:{g['game_id']}/{r[0]['player_id']}")
        lead = "; ".join(parts)
        return Answer(f"The {self.tn(g['winner_team_id'])} beat the {self.tn(loser)} {self.score(g)} on "
                      f"{g['date']}." + (f" {lead}." if lead else ""),
                      tool="game_result", evidence=ev, value=self.score(g))

    def game_leader(self, teams, stat="points", date=None, ordinal=None, team_only=None) -> Answer:
        g, err = self.pick_game(teams, date, ordinal, default_latest=len(teams) == 1)
        if err:
            return Answer.unknown(err, "game_leader")
        sql, args = "SELECT * FROM box_scores WHERE game_id = ?", [g["game_id"]]
        if team_only:
            sql += " AND team_id = ?"
            args.append(team_only)
        rows = self.q(sql + f" ORDER BY {stat} DESC, player_id", *args)
        best = rows[0][stat]
        top = [r for r in rows if r[stat] == best]
        return Answer(f"{_join([self.player[r['player_id']]['name'] for r in top])} led with {best} "
                      f"{LABEL[stat]} on {g['date']}.", tool="game_leader", value=best,
                      evidence=[f"games:{g['game_id']}"] + [f"box_scores:{g['game_id']}/{r['player_id']}" for r in top])

    # ---------- player tools ----------
    def player_game_line(self, pid, teams=None, date=None, ordinal=None) -> Answer:
        p = self.player[pid]
        teams = [p["team_id"]] + [t for t in (teams or []) if t != p["team_id"]]
        g, err = self.pick_game(teams, date, ordinal, default_latest=len(teams) == 1)
        if err:
            return Answer.unknown(err, "player_game_line")
        row = self.q("SELECT * FROM box_scores WHERE game_id = ? AND player_id = ?", g["game_id"], pid)
        if not row:
            out = self.q("SELECT * FROM inactive WHERE game_id = ? AND player_id = ?", g["game_id"], pid)
            why = f" ({out[0]['description'] or out[0]['reason']})" if out and (out[0]['description'] or out[0]['reason']) else ""
            return Answer.unknown(f"{p['name']} did not play on {g['date']}{why}", "player_game_line")
        r = row[0]
        return Answer(f"{p['name']} had {r['points']} points, {r['rebounds']} rebounds, {r['assists']} assists, "
                      f"{r['steals']} steal{'s' * (r['steals'] != 1)}, {r['blocks']} block{'s' * (r['blocks'] != 1)} and "
                      f"{r['threes']} three{'s' * (r['threes'] != 1)} in {r['minutes']:.0f} "
                      f"minutes on {g['date']} ({r['fgm']}-{r['fga']} FG, {r['plus_minus']:+.0f}).",
                      tool="player_game_line", value={s: r[s] for s in STATS},
                      evidence=[f"games:{g['game_id']}", f"box_scores:{g['game_id']}/{pid}"])

    def player_average(self, pid, stat="points", last_n: int | None = None) -> Answer:
        rows = self.q("""SELECT b.* FROM box_scores b JOIN games g USING (game_id)
                         WHERE player_id = ? ORDER BY g.date DESC, game_id DESC""", pid)
        name = self.player[pid]["name"]
        if not rows:
            return Answer.unknown(f"{name} hasn't played this season", "player_average")
        if last_n:
            rows = rows[:last_n]
        avg = round(sum(r[stat] for r in rows) / len(rows), 1)
        span = f"over his last {len(rows)} games" if last_n else f"this season ({len(rows)} games)"
        return Answer(f"{name} is averaging {avg} {LABEL[stat]} per game {span}.", tool="player_average",
                      value=avg, evidence=[f"box_scores:{r['game_id']}/{pid}" for r in rows])

    def why_missed(self, pid, teams=None, date=None) -> Answer:
        p = self.player[pid]
        sql = "SELECT i.*, g.date FROM inactive i JOIN games g USING (game_id) WHERE player_id = ?"
        args: list = [pid]
        if date:
            sql += " AND g.date = ?"
            args.append(date)
        rows = self.q(sql + " ORDER BY g.date", *args)
        opp = [t for t in (teams or []) if t != p["team_id"]]
        if opp:
            rows = [r for r in rows if opp[0] in (self.q("SELECT home_team_id, away_team_id FROM games WHERE game_id=?", r["game_id"])[0])]
        if not rows:
            return Answer.unknown(f"no game this season lists {p['name']} as inactive"
                                  + (" on that date" if date else ""), "why_missed")
        r = rows[-1]
        desc = r["description"] or r["reason"].replace("_", " ").lower()
        extra = "" if len(rows) == 1 or date else f" He has been listed inactive for {len(rows)} games this season."
        return Answer(f"{p['name']} was inactive on {r['date']}: {desc}.{extra}", tool="why_missed",
                      evidence=[f"inactive:{r['game_id']}/{pid}", f"games:{r['game_id']}"])

    # ---------- league and team tools ----------
    def league_leader(self, stat="points", conference=None) -> Answer:
        mg = self.min_games()
        sql = f"""SELECT b.player_id, AVG(b.{stat}) AS avg, COUNT(*) AS gp FROM box_scores b
                  JOIN players p USING (player_id) JOIN teams t ON t.team_id = p.team_id
                  {'WHERE t.conference = ?' if conference else ''}
                  GROUP BY b.player_id HAVING gp >= ? ORDER BY avg DESC, b.player_id LIMIT 5"""
        rows = self.q(sql, *([conference] if conference else []), mg)
        if not rows:
            return Answer.unknown("not enough games have been played yet", "league_leader")
        top = rows[0]
        p = self.player[top["player_id"]]
        others = ", ".join(f"{self.player[r['player_id']]['name']} {r['avg']:.1f}" for r in rows[1:])
        where = f"the {conference}" if conference else "the league"
        return Answer(f"{p['name']} ({self.team[p['team_id']]['name']}) leads {where} at {top['avg']:.1f} "
                      f"{LABEL[stat]} per game (min. {mg} games). Next: {others}.", tool="league_leader",
                      value=round(top["avg"], 1), evidence=[f"players:{r['player_id']}" for r in rows])

    def team_leader(self, team, stat="points") -> Answer:
        rows = self.q(f"""SELECT player_id, AVG({stat}) AS avg, COUNT(*) AS gp FROM box_scores
                          WHERE team_id = ? GROUP BY player_id HAVING gp >= 3
                          ORDER BY avg DESC, player_id LIMIT 3""", team)
        if not rows:
            return Answer.unknown("not enough games played yet", "team_leader")
        lst = "; ".join(f"{self.player[r['player_id']]['name']} {r['avg']:.1f}" for r in rows)
        return Answer(f"{self.player[rows[0]['player_id']]['name']} leads the {self.team[team]['name']} in "
                      f"{LABEL[stat]} per game. Top 3: {lst}.", tool="team_leader", value=round(rows[0]["avg"], 1),
                      evidence=[f"players:{r['player_id']}" for r in rows])

    def team_record(self, team, last_n: int | None = None) -> Answer:
        games = self.find_games([team])
        if last_n:
            games = games[-last_n:]
        w = sum(g["winner_team_id"] == team for g in games)
        span = f" in their last {len(games)}" if last_n else ""
        return Answer(f"The {self.tn(team)} are {w}-{len(games) - w}{span}.", tool="team_record",
                      value=f"{w}-{len(games) - w}", evidence=[f"games:{g['game_id']}" for g in games])

    def standings(self, conference=None) -> Answer:
        rows = []
        for t, info in self.team.items():
            if conference and info["conference"] != conference:
                continue
            g = self.find_games([t])
            w = sum(x["winner_team_id"] == t for x in g)
            rows.append((t, w, len(g) - w))
        rows.sort(key=lambda r: (-(r[1] / max(1, r[1] + r[2])), -r[1], r[0]))
        top = rows[0]
        lines = ", ".join(f"{i}. {self.team[t]['name']} {w}-{l}" for i, (t, w, l) in enumerate(rows[:8], 1))
        where = f"the {conference}" if conference else "the league"
        return Answer(f"The {self.tn(top[0])} have the best record in {where} at {top[1]}-{top[2]}. {lines}.",
                      tool="standings", value=[f"{t} {w}-{l}" for t, w, l in rows],
                      evidence=[f"teams:{t}" for t, _, _ in rows])

    def head_to_head(self, a, b) -> Answer:
        games = self.find_games([a, b])
        if not games:
            return Answer.unknown(f"the {self.team[a]['name']} and {self.team[b]['name']} haven't played yet this season",
                                  "head_to_head")
        wa = sum(g["winner_team_id"] == a for g in games)
        return Answer(f"The {self.tn(a)} are {wa}-{len(games) - wa} against the {self.tn(b)} this season.",
                      tool="head_to_head", value=f"{wa}-{len(games) - wa}",
                      evidence=[f"games:{g['game_id']}" for g in games])

    def extreme_margin(self, team, kind="win") -> Answer:
        games = [g for g in self.find_games([team]) if (g["winner_team_id"] == team) == (kind == "win")]
        if not games:
            return Answer.unknown(f"the {self.team[team]['name']} have no {'losses' if kind == 'loss' else 'wins'} yet",
                                  "extreme_margin")
        g = max(games, key=lambda g: (abs(g["home_points"] - g["away_points"]), g["date"]))
        opp = g["away_team_id"] if g["home_team_id"] == team else g["home_team_id"]
        m = abs(g["home_points"] - g["away_points"])
        return Answer(f"The {poss(self.team[team]['name'])} biggest {kind} was by {m} against the {self.tn(opp)} on "
                      f"{g['date']} ({self.score(g)}).", tool="extreme_margin", value=m,
                      evidence=[f"games:{g['game_id']}"])
