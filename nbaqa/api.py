"""REST API.

  uvicorn nbaqa.api:app --reload
  open http://127.0.0.1:8000/docs   (interactive docs)
"""
from __future__ import annotations

import os
import pathlib
import threading

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import db
from .fetch import RAW, fetch
from .router import Router, fold
from .tools import STATS, Tools

app = FastAPI(title="NBA Q&A API", version="1.0",
              description="Plain-English questions about the current NBA season, answered from box scores "
                          "with the source rows cited.")
_lock = threading.Lock()
_state: dict = {}


def raw_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("NBAQA_RAW", RAW))


def state() -> tuple[Tools, Router]:
    with _lock:
        if "router" not in _state:
            tools = Tools(db.build(raw_dir()))
            _state["tools"], _state["router"] = tools, Router(tools)
        return _state["tools"], _state["router"]


class AskIn(BaseModel):
    question: str = Field(..., min_length=3, examples=["Who leads the league in assists?"])
    use_llm: bool = Field(False, description="Route with the LLM configured on the server (LLM_PROVIDER / LLM_MODEL)")


@app.get("/health")
def health():
    tools, _ = state()
    n = tools.q("SELECT COUNT(*) AS n, MAX(date) AS last FROM games")[0]
    return {"status": "ok", "games": n["n"], "through": n["last"]}


@app.post("/ask")
def ask(body: AskIn):
    tools, router = state()
    if body.use_llm:
        from .llm_router import LLMRouter
        return LLMRouter(router).route(body.question).to_dict()
    return router.route(body.question).to_dict()


@app.get("/standings")
def standings(conference: str | None = Query(None, pattern="^(East|West)$")):
    tools, _ = state()
    return tools.standings(conference).to_dict()


@app.get("/leaders/{stat}")
def leaders(stat: str, conference: str | None = Query(None, pattern="^(East|West)$")):
    if stat not in STATS:
        raise HTTPException(404, f"stat must be one of {', '.join(STATS)}")
    tools, _ = state()
    return tools.league_leader(stat, conference).to_dict()


@app.get("/teams/{tricode}")
def team(tricode: str):
    tools, _ = state()
    t = tricode.upper()
    if t not in tools.team:
        raise HTTPException(404, f"unknown team {tricode}")
    return {"team": tools.team[t], "record": tools.team_record(t).value,
            "last_10": tools.team_record(t, 10).value, "leaders": {
                s: tools.team_leader(t, s).answer for s in ("points", "rebounds", "assists")}}


@app.get("/players")
def players(search: str = Query(..., min_length=2)):
    tools, _ = state()
    s = fold(search)
    hits = [p for p in tools.player.values() if s in fold(p["name"])][:20]
    return [{**p, "season": tools.player_average(p["player_id"]).answer} for p in hits]


@app.post("/refresh")
def refresh():
    """Download games that finished since the last refresh and rebuild the database."""
    new = fetch(raw_dir())
    with _lock:
        _state.clear()
    state()
    return {"new_games": new, **health()}
