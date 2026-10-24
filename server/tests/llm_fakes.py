"""A mock LiteLLM proxy for tests that run Agno models without a real gateway."""

import json
from typing import Any

import httpx


def completion(message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    """A non-streaming chat completion with one choice."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "local-main",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def tool_call_completion(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> Any:
    """A completion asking for one tool call."""
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return completion({"role": "assistant", "content": "", "tool_calls": [call]}, "tool_calls")


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "local-main",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(body)}\n\n"


def _sse_response(events: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        content="".join([*events, "data: [DONE]\n\n"]).encode(),
        headers={"content-type": "text/event-stream"},
    )


def streamed_completion(*deltas: str, usage: tuple[int, int] | None = None) -> httpx.Response:
    """A streamed (SSE) chat completion sending `deltas` as separate content chunks.

    `usage` (prompt tokens, completion tokens) adds the final usage chunk the proxy sends.
    """
    events = [_chunk({"role": "assistant", "content": ""})]
    events += [_chunk({"content": d}) for d in deltas]
    events += [_chunk({}, "stop")]
    if usage is not None:
        prompt, output = usage
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "local-main",
            "choices": [],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": output,
                "total_tokens": prompt + output,
            },
        }
        events.append(f"data: {json.dumps(body)}\n\n")
    return _sse_response(events)


def streamed_tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> Any:
    """A streamed completion asking for one tool call."""
    call = {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    events = [_chunk({"role": "assistant", "content": None, "tool_calls": [call]})]
    events += [_chunk({}, "tool_calls")]
    return _sse_response(events)


class FakeProxy:
    """Mock LiteLLM proxy: records requests and replays canned chat completions.

    Each response is a completion dict (sent as JSON) or a ready `httpx.Response`.
    """

    def __init__(self, *responses: dict[str, Any] | httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def body(self, i: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


def route_completion(
    *,
    agent: str = "general",
    level: str = "standard",
    long: bool = False,
    tools: int = 1,
    cloud: bool = False,
    provider: str = "none",
) -> dict[str, Any]:
    """The router classifier's answer (`local-router`'s compact JSON)."""
    answer = {
        "agent": agent,
        "level": level,
        "long": long,
        "tools": tools,
        "cloud": cloud,
        "provider": provider,
    }
    return completion({"role": "assistant", "content": json.dumps(answer)})


def reply(text: str) -> dict[str, Any]:
    """A plain assistant reply."""
    return completion({"role": "assistant", "content": text})
