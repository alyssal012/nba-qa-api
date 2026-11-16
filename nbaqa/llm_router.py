"""Optional: let an LLM choose the tool instead of the rule-based router.

Works with any provider the user configures (OpenAI, Anthropic, or an
OpenAI-compatible endpoint such as a local Ollama model); see llm_client.py.
The model only picks a tool and its arguments. The numbers always come from the
database, so answers stay grounded and cited even for unusual phrasing.
"""
from __future__ import annotations

from .llm_client import LLM, Tool
from .router import Router
from .tools import STATS, Answer

TEAM = {"type": "array", "items": {"type": "string"}, "description": "Team names as written in the question."}
PLAYER = {"type": "string", "description": "Player name as written in the question."}
DATE = {"type": "string", "description": "YYYY-MM-DD, only if the question gives a date."}
ORD = {"type": "integer", "description": "0 = first meeting, 1 = second, -1 = most recent."}
STAT = {"type": "string", "enum": list(STATS)}
CONF = {"type": "string", "enum": ["East", "West"]}
LASTN = {"type": "integer", "description": "Only the last N games, if asked about recent form."}


def spec(name, desc, props, req=()):
    return Tool(name, desc, {"type": "object", "properties": props, "required": list(req)})


TOOL_SPECS = [
    spec("game_result", "Who won a game, the score and top scorers.", {"teams": TEAM, "date": DATE, "ordinal": ORD}, ["teams"]),
    spec("game_leader", "Which player led one game in a stat.", {"teams": TEAM, "stat": STAT, "date": DATE, "ordinal": ORD}, ["teams"]),
    spec("player_game_line", "A player's box score in one game.", {"player": PLAYER, "teams": TEAM, "date": DATE, "ordinal": ORD}, ["player"]),
    spec("player_average", "A player's per-game average, season or last N games.", {"player": PLAYER, "stat": STAT, "last_n": LASTN}, ["player"]),
    spec("why_missed", "Why a player was inactive for a game.", {"player": PLAYER, "teams": TEAM, "date": DATE}, ["player"]),
    spec("league_leader", "Who leads the league or a conference in a per-game stat.", {"stat": STAT, "conference": CONF}),
    spec("team_leader", "Who leads one team in a per-game stat.", {"teams": TEAM, "stat": STAT}, ["teams"]),
    spec("team_record", "A team's win-loss record, optionally over the last N games.", {"teams": TEAM, "last_n": LASTN}, ["teams"]),
    spec("standings", "League or conference standings.", {"conference": CONF}),
    spec("head_to_head", "Season series between two teams.", {"teams": TEAM}, ["teams"]),
    spec("extreme_margin", "A team's biggest win or loss.", {"teams": TEAM, "kind": {"type": "string", "enum": ["win", "loss"]}}, ["teams", "kind"]),
    spec("cannot_answer", "The question needs data beyond this season's results and box scores.", {"reason": {"type": "string"}}, ["reason"]),
]
SYSTEM = ("You route NBA questions about the current season to exactly one tool. Copy names from the question. "
          "Never answer from memory. If no tool fits, call cannot_answer.")


class LLMRouter:
    def __init__(self, router: Router, llm: LLM | None = None):
        self.router, self.t = router, router.t
        self.llm = llm or LLM()

    def route(self, question: str) -> Answer:
        picked = self.llm.choose_tool(SYSTEM, question, TOOL_SPECS)
        if picked is None:
            return Answer.unknown("the model did not pick a tool", "llm")
        name, a = picked
        if name == "cannot_answer":
            return Answer.unknown(a.get("reason", "outside the data"), "llm")
        teams = self.router.extract(" , ".join(a.get("teams") or [])).teams
        pid = None
        if "player" in a:
            found = self.router.extract(a["player"]).players
            if not found:
                return Answer.unknown(f"no player named {a['player']!r} has appeared this season", name)
            pid = found[0]
        needs_team = {"game_result", "game_leader", "team_leader", "team_record", "head_to_head", "extreme_margin"}
        if name in needs_team and not teams:
            return Answer.unknown("no NBA team in the question", name)
        stat, date, ordinal = a.get("stat", "points"), a.get("date"), a.get("ordinal")
        t = self.t
        calls = {
            "game_result": lambda: t.game_result(teams, date, ordinal),
            "game_leader": lambda: t.game_leader(teams, stat, date, ordinal),
            "player_game_line": lambda: t.player_game_line(pid, teams, date, ordinal),
            "player_average": lambda: t.player_average(pid, stat, a.get("last_n")),
            "why_missed": lambda: t.why_missed(pid, teams, date),
            "league_leader": lambda: t.league_leader(stat, a.get("conference")),
            "team_leader": lambda: t.team_leader(teams[0], stat),
            "team_record": lambda: t.team_record(teams[0], a.get("last_n")),
            "standings": lambda: t.standings(a.get("conference")),
            "head_to_head": lambda: t.head_to_head(*teams[:2]) if len(teams) > 1 else Answer.unknown("needs two teams", name),
            "extreme_margin": lambda: t.extreme_margin(teams[0], a.get("kind", "win")),
        }
        return calls[name]() if name in calls else Answer.unknown(f"unknown tool {name}", "llm")
