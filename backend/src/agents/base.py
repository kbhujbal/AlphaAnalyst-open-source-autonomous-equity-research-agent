from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from src.llm.client import AnalystLLM, CompletionResult
from src.models.finding import Finding
from src.settings import settings


class LLMProtocol(Protocol):
    async def complete(
        self,
        task: str,
        system: str,
        prompt: str,
        cache_system: bool = False,
        response_schema: type[BaseModel] | None = None,
    ) -> CompletionResult: ...


class AgentOutput(BaseModel):
    agent_name: str
    ticker: str
    findings: list[Finding] = []
    errors: list[str] = []
    llm_calls: int = 0
    cost_usd: Decimal = Decimal("0")


class Agent(ABC):
    name: str = "agent"

    def __init__(self, llm: LLMProtocol | None = None) -> None:
        self.llm: LLMProtocol = llm or AnalystLLM(
            config_path=settings.models_config_path
        )

    @abstractmethod
    async def run(self, ticker: str) -> AgentOutput: ...
