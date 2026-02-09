from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from src.llm import AnalystLLM, CompletionResult


@pytest.fixture
def models_config(tmp_path: Path) -> Path:
    cfg = {
        "news_classification": {
            "model": "anthropic/claude-haiku-4-5-20251001",
            "fallback": "openai/gpt-4o-mini",
            "max_tokens": 1024,
            "temperature": 0.0,
        },
        "synthesis": {
            "model": "openai/gpt-4o",
            "fallback": "anthropic/claude-sonnet-4-6",
            "max_tokens": 4096,
            "temperature": 0.2,
        },
    }
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return path


def _fake_response(
    text: str = "ok",
    model: str = "anthropic/claude-haiku-4-5-20251001",
) -> SimpleNamespace:
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=45,
            cache_read_input_tokens=80,
        ),
    )


async def test_complete_returns_completion_result_with_all_fields(
    mocker, models_config: Path
) -> None:
    mocker.patch(
        "src.llm.client.litellm.acompletion",
        new=AsyncMock(return_value=_fake_response()),
    )
    mocker.patch("src.llm.client.litellm.completion_cost", return_value=0.0123)

    llm = AnalystLLM(config_path=models_config)
    result = await llm.complete(
        task="news_classification", system="sys", prompt="hi"
    )

    assert isinstance(result, CompletionResult)
    assert result.text == "ok"
    assert result.task == "news_classification"
    assert result.model_used == "anthropic/claude-haiku-4-5-20251001"
    assert result.input_tokens == 120
    assert result.output_tokens == 45
    assert result.cached_tokens == 80
    assert result.cost_usd == pytest.approx(0.0123)


async def test_missing_task_raises_keyerror_with_helpful_message(
    models_config: Path,
) -> None:
    llm = AnalystLLM(config_path=models_config)
    with pytest.raises(KeyError) as exc_info:
        await llm.complete(task="not_a_real_task", system="s", prompt="p")

    message = str(exc_info.value)
    assert "not_a_real_task" in message
    assert "news_classification" in message
    assert str(models_config) in message


async def test_cache_system_transforms_to_cache_control_for_claude_models(
    mocker, models_config: Path
) -> None:
    mock_acompletion = AsyncMock(return_value=_fake_response())
    mocker.patch("src.llm.client.litellm.acompletion", new=mock_acompletion)
    mocker.patch("src.llm.client.litellm.completion_cost", return_value=0.0)

    llm = AnalystLLM(config_path=models_config)
    await llm.complete(
        task="news_classification",
        system="long system prompt",
        prompt="user msg",
        cache_system=True,
    )

    kwargs = mock_acompletion.await_args.kwargs
    system_msg = kwargs["messages"][0]
    assert system_msg["role"] == "system"
    assert isinstance(system_msg["content"], list)
    block = system_msg["content"][0]
    assert block == {
        "type": "text",
        "text": "long system prompt",
        "cache_control": {"type": "ephemeral"},
    }
    assert kwargs["fallbacks"] == ["openai/gpt-4o-mini"]
    assert kwargs["num_retries"] == 2


async def test_cache_system_does_not_transform_for_non_anthropic_models(
    mocker, models_config: Path
) -> None:
    mock_acompletion = AsyncMock(
        return_value=_fake_response(model="openai/gpt-4o")
    )
    mocker.patch("src.llm.client.litellm.acompletion", new=mock_acompletion)
    mocker.patch("src.llm.client.litellm.completion_cost", return_value=0.0)

    llm = AnalystLLM(config_path=models_config)
    await llm.complete(
        task="synthesis",
        system="sys text",
        prompt="user",
        cache_system=True,
    )

    system_msg = mock_acompletion.await_args.kwargs["messages"][0]
    assert system_msg["content"] == "sys text"
