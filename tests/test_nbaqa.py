import json
import pathlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nbaqa import db
from nbaqa.fetch import finished_regular_season_games
from nbaqa.llm_client import LLM, LLMError
from nbaqa.llm_router import LLMRouter
from nbaqa.router import Router
from nbaqa.tools import Tools

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures"


@pytest.fixture(scope="module")
def tools():
    return Tools(db.build(FIX))


@pytest.fixture(scope="module")
def router(tools):
    return Router(tools)


@pytest.fixture(scope="module")
def okc(tools):
    ps = sorted((p for p in tools.player.values() if p["team_id"] == "OKC"), key=lambda p: p["player_id"])
    return ps[0]["name"], ps[1]["name"]  # (starter, player who misses two games)


# ---------- parsing the NBA feed format ----------
def test_loads_real_nba_sample_boxscore(tmp_path):
    """The sample response documented in nba_api (Celtics 124, Magic 97) parses correctly."""
    (tmp_path / "boxscores").mkdir()
    src = HERE / "fixtures_nba_sample" / "boxscore_0022000180_sample.json"
    (tmp_path / "boxscores" / "0022000180.json").write_text(src.read_text())
    t = Tools(db.build(tmp_path))
    g = t.q("SELECT * FROM games")[0]
    assert (g["home_team_id"], g["home_points"], g["away_points"], g["winner_team_id"]) == ("BOS", 124, 97, "BOS")
    row = t.q("SELECT * FROM box_scores b JOIN players USING (player_id) WHERE name='Jaylen Brown'")[0]
    assert (row["points"], row["minutes"], row["plus_minus"]) == (21, 25.0, 15.0)


def test_minutes_parsing():
    assert db.minutes("PT34M30.00S") == 34.5 and db.minutes(None) == 0.0


def test_only_finished_regular_season_games_are_fetched():
    sched = {"leagueSchedule": {"gameDates": [{"games": [
        {"gameId": "0012600001", "gameStatus": 3},   # preseason
        {"gameId": "0022600001", "gameStatus": 3},   # regular season, final
        {"gameId": "0022600002", "gameStatus": 1}]}]}}  # not started
    assert finished_regular_season_games(sched) == ["0022600001"]


def test_box_scores_add_up_to_final_score(tools):
    for g in tools.q("SELECT * FROM games"):
        s = tools.q("SELECT SUM(points) AS s FROM box_scores WHERE game_id=? AND team_id=?",
                    g["game_id"], g["home_team_id"])[0]["s"]
        assert s == g["home_points"]


# ---------- answers ----------
def test_record_and_nicknames(router):
    a, b = router.route("What's the Thunder's record?"), router.route("OKC W-L?")
    assert a.value == b.value and a.tool == "team_record"


def test_standings_by_conference(router):
    a = router.route("East standings")
    assert a.tool == "standings" and all(v.split()[0] in {"BOS", "NYK"} for v in a.value)


def test_injury_reason_from_box_score(router, okc):
    a = router.route(f"Why did {okc[1]} miss the game on 2026-10-22?")
    assert "Ankle" in a.answer and a.evidence[0].startswith("inactive:")


def test_inactive_player_game_line_explains_absence(router, okc):
    a = router.route(f"How did {okc[1]} do on 2026-10-22?")
    assert not a.answerable and "did not play" in a.answer


def test_last_n_games(router, okc):
    full = router.route(f"How many points is {okc[0]} averaging?")
    recent = router.route(f"How has {okc[0]} played in his last 2 games?")
    assert len(recent.evidence) == 2 and len(full.evidence) >= 2


def test_accents_are_ignored(router, tools):
    name = next(p["name"] for p in tools.player.values() if "ć" in p["name"])
    plain = name.replace("ć", "c").replace("š", "s")
    assert router.route(f"what is {plain} averaging").answerable


def test_shared_last_name_asks_which(router):
    a = router.route("Is Bellweather hurt?")
    assert a.tool == "clarify" and "Brooks Bellweather" in a.answer


def test_two_la_teams_need_nicknames(router):
    assert router.extract("Los Angeles").teams == []
    assert router.extract("Lakers vs Clippers").teams == ["LAL", "LAC"]


def test_declines_what_box_scores_cannot_answer(router):
    for q in ["Who is the Thunder's head coach?", "Who won the 2026 championship?", "Thunder MVP odds?"]:
        assert not router.route(q).answerable


def test_one_team_question_defaults_to_latest_game(router, tools):
    a = router.route("How did the Thunder do in their last game?")
    latest = tools.find_games(["OKC"])[-1]["game_id"]
    assert f"games:{latest}" in a.evidence


# ---------- LLM router (offline, with fake provider clients) ----------
def fake_anthropic(name, args):
    call = SimpleNamespace(type="tool_use", name=name, input=args)
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[call])))


def fake_openai(name, args):
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    msg = SimpleNamespace(tool_calls=[call], content=None)
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: resp)))


@pytest.mark.parametrize("provider,make", [("anthropic", fake_anthropic), ("openai", fake_openai),
                                           ("compatible", fake_openai)])
def test_llm_router_works_with_every_provider(router, tools, provider, make):
    llm = LLM(provider=provider, model="any-model", client=make("team_record", {"teams": ["OKC Thunder"]}))
    assert LLMRouter(router, llm).route("how's okc doing") == tools.team_record("OKC")


def test_llm_router_rejects_unknown_player(router):
    llm = LLM(provider="openai", model="m", client=fake_openai("player_average", {"player": "Wilt Chamberlain"}))
    assert not LLMRouter(router, llm).route("wilt ppg").answerable


def test_llm_config_errors_are_clear(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(LLMError, match="LLM_PROVIDER"):
        LLM(model="m", client=object())
    with pytest.raises(LLMError, match="LLM_MODEL"):
        LLM(provider="openai", model="", client=object())


# ---------- REST API ----------
def test_api(monkeypatch):
    monkeypatch.setenv("NBAQA_RAW", str(FIX))
    from nbaqa import api
    api._state.clear()
    c = TestClient(api.app)
    assert c.get("/health").json()["games"] == 12
    r = c.post("/ask", json={"question": "Who leads the league in rebounds?"}).json()
    assert r["tool"] == "league_leader" and r["answerable"]
    assert c.get("/standings", params={"conference": "West"}).json()["tool"] == "standings"
    assert c.get("/leaders/blocks").status_code == 200
    assert c.get("/leaders/dunks").status_code == 404
    assert c.get("/teams/okc").json()["team"]["name"] == "Thunder"
    assert c.get("/players", params={"search": "abara"}).status_code == 200
