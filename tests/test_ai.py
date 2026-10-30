"""The AI provider switch: an open model through Ollama, or Claude. No network: HTTP is mocked."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from wrangle import ai
from wrangle.matcher import MatchResponse


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setenv("WRANGLE_AI", "ollama")
    monkeypatch.setenv("WRANGLE_AI_MODEL", "qwen3:14b")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")


def test_claude_keeps_its_recording_key(monkeypatch):
    monkeypatch.setenv("WRANGLE_AI", "anthropic")
    assert ai.model_key() == "claude-opus-5-5"  # recordings made before open models still replay


def test_open_models_get_their_own_key(ollama):
    assert ai.model_key() == "ollama:qwen3:14b"


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setenv("WRANGLE_AI", "somethingelse")
    with pytest.raises(ValueError):
        ai.provider()


@respx.mock
async def test_ollama_is_asked_for_the_schema_and_its_answer_is_parsed(ollama):
    answer = {"matches": [{"target_field": "name", "source_field": "FULLSTREET",
                           "confidence": "high", "reasoning": "complete names"}]}
    reply = {
        "model": "qwen3:14b", "message": {"role": "assistant", "content": json.dumps(answer)},
        "prompt_eval_count": 900, "eval_count": 120, "total_duration": 4_200_000_000,
    }
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json=reply)
    )
    record = await ai.ask("system", "prompt", MatchResponse)
    sent = json.loads(route.calls.last.request.content)
    assert sent["format"] == MatchResponse.model_json_schema()  # output constrained to the schema
    assert sent["think"] is False and sent["options"]["temperature"] == 0
    assert sent["options"]["num_ctx"] >= 12000  # never Ollama's silent 4,096-token cut
    assert record["model"] == "ollama:qwen3:14b"
    assert record["response"] == answer
    assert record["usage"] == {"input_tokens": 900, "output_tokens": 120, "seconds": 4.2}


@respx.mock
async def test_a_missing_model_says_how_to_get_it(ollama):
    respx.post("http://localhost:11434/api/chat").mock(return_value=httpx.Response(404))
    with pytest.raises(RuntimeError, match="ollama pull qwen3:14b"):
        await ai.ask("system", "prompt", MatchResponse)
