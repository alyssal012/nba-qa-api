# NBA Q&A API

A REST API that answers plain-English questions about the **current NBA season** (2026-27) from official box scores. Every answer cites the games and stat lines it came from, and when the data can't support an answer, the API says so instead of guessing.

```bash
$ curl -X POST localhost:8000/ask -H 'content-type: application/json' \
       -d '{"question": "Who leads the West in assists?"}'
{
  "answer": "<player> (<team>) leads the West at 9.8 assists per game (min. 10 games). Next: ...",
  "answerable": true,
  "tool": "league_leader",
  "evidence": ["players:...", "..."],
  "value": 9.8
}
```

Things you can ask:

| Question | What it does |
|---|---|
| "What's the Thunder's record?" / "OKC record in their last 10 games?" | counts wins and losses, citing every game |
| "East standings" / "Who has the best record in the league?" | builds standings by win percentage |
| "Who leads the league in blocks?" | per-game leaderboard with a games-played minimum, like the NBA's |
| "Who leads the Knicks in rebounds?" | team leaderboard |
| "How has [player] played in his last 5 games?" | rolling averages |
| "How many threes did [player] make against Denver on November 12?" | single-game box score line |
| "Who scored the most for the Celtics in their first game against the Heat?" | game leaders, filtered by team, date, or first/second/last meeting |
| "Why did [player] miss the game on 2026-11-10?" | the inactive reason listed in that game's box score |
| "What was the Lakers' biggest loss?" / "Thunder vs Nuggets season series?" | margins and head-to-head |
| "Who's the Thunder's head coach?" | **declines**: the database only has results and box scores |

## How it works

```
cdn.nba.com feeds ──► fetch.py ──► data/raw/*.json ──► db.py ──► SQLite
                                                                   │
question ──► router (rules, or your LLM) ──► tools.py    ──────┘──► answer + cited rows
```

1. **`fetch.py`** downloads the season schedule and the box score of every finished regular-season game from the NBA's public JSON feeds. It caches everything, so a refresh only downloads new games.
2. **`db.py`** loads them into SQLite: games, box scores (points, rebounds, assists, steals, blocks, threes, turnovers, FG, minutes, plus-minus) and inactive players with the reason listed.
3. **`tools.py`** has 11 query functions. Each returns the value **and the IDs of the rows it used**.
4. **`router.py`** maps a question to one tool by pulling out teams (full names, cities, nicknames like "Sixers" and "Cavs", and tricodes like OKC), players (accent-insensitive, so "Jokic" matches "Jokić"), dates ("November 12", "last night"), stats, and phrases like "first meeting" or "last 5 games".
5. **`llm_router.py` + `llm_client.py`** (optional): **bring your own AI model.** An LLM picks the tool for unusually phrased questions, using whichever provider the user chooses: OpenAI, Anthropic, or any OpenAI-compatible service, including free local models through Ollama. **The model never produces the numbers**; it only chooses which query to run. That keeps answers grounded no matter which model is plugged in.
6. **`api.py`** exposes it all with FastAPI, with interactive docs at `/docs`.

### Design choices

- **Compute, don't retrieve.** Questions like "what's their record?" need counting and aggregation over many rows. Embedding search plus an LLM summary gets those wrong, so the numbers come from SQL.
- **Decline instead of guessing.** Unknown stats, coaches, contracts and odds get a clear "can't answer". A last name shared by two players gets a follow-up question ("Which one? ..."). Two teams with several meetings and no date gets "say which date".
- **Edge cases handled:** the two LA teams (the city alone matches neither, so you need "Lakers" or "Clippers"); traded players (they're credited to their most recent team); and "Jr."/"III" suffixes in last-name matching.

## Run it

```bash
pip install -r requirements.txt
python -m nbaqa.fetch                      # first run downloads every finished game (a few minutes)
python -m nbaqa "Who leads the league in points?"
uvicorn nbaqa.api:app --reload             # then open http://127.0.0.1:8000/docs
```

Endpoints: `POST /ask`, `GET /standings?conference=West`, `GET /leaders/{stat}`, `GET /teams/{tricode}`, `GET /players?search=`, `POST /refresh` (downloads new games and rebuilds the database), and `GET /health`.

### Bring your own AI model (optional)

The LLM layer is provider-neutral. Pick a provider with two settings, and nothing in the code changes:

```bash
# OpenAI
pip install openai && export OPENAI_API_KEY=...
export LLM_PROVIDER=openai LLM_MODEL=<an OpenAI model name>

# Anthropic
pip install anthropic && export ANTHROPIC_API_KEY=...
export LLM_PROVIDER=anthropic LLM_MODEL=<an Anthropic model name>

# Free and local, e.g. Ollama (or Groq, Together, vLLM: anything OpenAI-compatible)
pip install openai
export LLM_PROVIDER=compatible LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=<a local model with tool support>

python -m nbaqa --llm "how have the knicks looked lately?"
```

Over the API, send `{"question": "...", "use_llm": true}` to `/ask`. The tests run the LLM router against fake OpenAI-style and Anthropic-style clients, so every provider path is checked offline.

## Tests

```bash
pytest -q
```

The 20 tests run offline. They use small fixture files in the exact shape of the NBA feeds (real teams, invented players and stats) plus the sample box score documented by the `nba_api` project. They cover parsing, every answer type, edge cases (accents, shared last names, the two LA teams), the LLM router with every supported provider (using fake clients), and every API endpoint.

## Next steps

- Play-by-play data for clutch stats and lineup combinations
- Caching common answers and refreshing on a schedule
- An eval set of real fan questions to measure the rule router against the LLM router

## Data

Game data comes from the NBA's public stats feeds and is downloaded at runtime, not stored in this repo. This is an unofficial personal project with no affiliation to the NBA.

## Stack

Python, FastAPI, SQLite, pytest, optional LLM tool calling (OpenAI, Anthropic, or OpenAI-compatible/local models)
