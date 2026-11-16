"""One small interface over several LLM providers, chosen by the user.

Set these environment variables:

  LLM_PROVIDER   openai | anthropic | compatible
  LLM_MODEL      the model name your provider uses
  LLM_BASE_URL   only for "compatible": any OpenAI-compatible endpoint, e.g. a local
                 Ollama server (http://localhost:11434/v1), Groq, Together, vLLM...
  plus the provider's own API key (OPENAI_API_KEY or ANTHROPIC_API_KEY;
  local servers usually don't need one)

The rest of the code only calls `choose_tool()` and `complete()`, so swapping
models is a config change, not a code change.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

PROVIDERS = ("openai", "anthropic", "compatible")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None,
                 base_url: str | None = None, client=None):
        self.provider = (provider or os.environ.get("LLM_PROVIDER", "")).lower()
        self.model = model or os.environ.get("LLM_MODEL", "")
        if self.provider not in PROVIDERS:
            raise LLMError(f"set LLM_PROVIDER to one of {', '.join(PROVIDERS)}")
        if not self.model:
            raise LLMError("set LLM_MODEL to the model name your provider uses")
        self.client = client or self._make_client(base_url or os.environ.get("LLM_BASE_URL"))

    def _make_client(self, base_url):
        try:
            if self.provider == "anthropic":
                import anthropic
                return anthropic.Anthropic()
            import openai
            if self.provider == "compatible":
                if not base_url:
                    raise LLMError("set LLM_BASE_URL for an OpenAI-compatible provider")
                return openai.OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "not-needed"))
            return openai.OpenAI()
        except ImportError as e:
            pkg = "anthropic" if self.provider == "anthropic" else "openai"
            raise LLMError(f"pip install {pkg} to use LLM_PROVIDER={self.provider}") from e

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    # ---------- tool selection ----------
    def choose_tool(self, system: str, user: str, tools: list[Tool]) -> tuple[str, dict] | None:
        """Ask the model to pick exactly one tool. Returns (tool_name, arguments)."""
        if self.provider == "anthropic":
            msg = self.client.messages.create(
                model=self.model, max_tokens=400, temperature=0, system=system,
                tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
                tool_choice={"type": "any"}, messages=[{"role": "user", "content": user}])
            call = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
            return (call.name, dict(call.input)) if call else None

        resp = self.client.chat.completions.create(
            model=self.model, temperature=0, tool_choice="required",
            tools=[{"type": "function", "function": {"name": t.name, "description": t.description,
                                                     "parameters": t.parameters}} for t in tools],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        calls = resp.choices[0].message.tool_calls or []
        if not calls:
            return None
        args = calls[0].function.arguments
        return calls[0].function.name, json.loads(args) if isinstance(args, str) else dict(args)

    # ---------- plain text ----------
    def complete(self, prompt: str, max_tokens: int = 1500) -> str:
        if self.provider == "anthropic":
            msg = self.client.messages.create(model=self.model, max_tokens=max_tokens, temperature=0,
                                              messages=[{"role": "user", "content": prompt}])
            return "".join(getattr(b, "text", "") for b in msg.content)
        resp = self.client.chat.completions.create(model=self.model, temperature=0, max_tokens=max_tokens,
                                                   messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content or ""
