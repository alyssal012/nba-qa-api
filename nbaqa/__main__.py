"""python -m nbaqa "Who leads the league in assists?"   (add --llm to route with your chosen LLM)"""
from __future__ import annotations

import argparse
import os
import pathlib

from . import db
from .fetch import RAW
from .router import Router
from .tools import Tools


def main() -> None:
    ap = argparse.ArgumentParser(prog="nbaqa")
    ap.add_argument("question", nargs="*")
    ap.add_argument("--llm", action="store_true")
    args = ap.parse_args()
    router = Router(Tools(db.build(pathlib.Path(os.environ.get("NBAQA_RAW", RAW)))))
    if args.llm:
        from .llm_router import LLMRouter
        router = LLMRouter(router)

    def ask(q):
        a = router.route(q)
        print(a.answer)
        if a.evidence:
            print("  sources:", ", ".join(a.evidence[:5]) + (f" (+{len(a.evidence) - 5} more)" if len(a.evidence) > 5 else ""))

    if args.question:
        return ask(" ".join(args.question))
    print("Ask about the 2026-27 NBA season (blank line to quit).")
    while (q := input("> ").strip()):
        ask(q)


if __name__ == "__main__":
    main()
