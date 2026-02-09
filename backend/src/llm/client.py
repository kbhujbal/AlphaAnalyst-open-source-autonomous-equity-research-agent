from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import litellm
import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Raised when an LLM call fails after all retries and fallbacks."""


class CompletionResult(BaseModel):
    text: str
    model_used: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    task: str


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return m.startswith("claude") or m.startswith("anthropic/") or "/claude" in m


class AnalystLLM:
    def __init__(self, config_path: Path = Path("config/models.yaml")) -> None:
        self.config_path = Path(config_path)
        with self.config_path.open("r", encoding="utf-8") as f:
            self._config: dict[str, dict[str, Any]] = yaml.safe_load(f) or {}

    def _task_config(self, task: str) -> dict[str, Any]:
        if task not in self._config:
            raise KeyError(
                f"Unknown LLM task '{task}'. Define it in {self.config_path} "
                f"(known tasks: {sorted(self._config.keys())})."
            )
        return self._config[task]

    async def complete(
        self,
        task: str,
        system: str,
        prompt: str,
        cache_system: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> CompletionResult:
        cfg = self._task_config(task)
        model: str = cfg["model"]
        fallback: str | None = cfg.get("fallback")

        if cache_system and _is_anthropic_model(model):
            system_content: Any = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "num_retries": 2,
        }
        if "max_tokens" in cfg:
            kwargs["max_tokens"] = cfg["max_tokens"]
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]
        if fallback:
            kwargs["fallbacks"] = [fallback]
        if response_schema is not None:
            kwargs["response_format"] = response_schema

        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            raise LLMError(f"LLM call failed for task '{task}': {exc}") from exc

        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0.0)
        except Exception as exc:
            logger.warning("completion_cost failed for task=%s: %s", task, exc)
            cost = 0.0

        text = response.choices[0].message.content or ""

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cached_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        if not cached_tokens:
            details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = (
                int(getattr(details, "cached_tokens", 0) or 0) if details else 0
            )

        model_used = getattr(response, "model", model) or model

        return CompletionResult(
            text=text,
            model_used=model_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost,
            task=task,
        )
