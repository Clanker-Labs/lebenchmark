"""One OpenAI-compatible client, used for every call the benchmark makes.

LeHarness speaks ``/v1/chat/completions``. So does OpenRouter, so does
LeClanker's own gateway. Keeping the client to that one interface is what lets
the same task file be pointed at a local model or a cloud one without a code
change — which is the only way the local numbers mean anything, because they
only mean something next to a baseline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Self

import httpx


@dataclass(slots=True)
class Response:
    """One completion, plus what it cost in wall-clock and tokens."""

    ok: bool
    latency_s: float
    #: Seconds to the first token that carried content or a tool-call fragment.
    #: None outside streaming mode.
    ttft_s: float | None = None
    finish_reason: str | None = None
    content: str = ""
    #: Some models return chain-of-thought in a separate `message.reasoning`
    #: field rather than in `content`. A caller cannot use it, so it is not
    #: content — but it is the difference between "the model said nothing" and
    #: "the model reasoned until the budget ran out and never answered", which
    #: are different bugs. The first run of this benchmark could not tell them
    #: apart because this field was being discarded.
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    #: Populated only when `keep_raw` is set, so a 3000-call run does not carry
    #: 3000 full response bodies in memory.
    raw: dict[str, Any] | None = None


class Client:
    def __init__(
        self,
        base_url: str,
        api_key: str = "lebenchmark",
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # LeHarness has no auth — tailnet reachability is the access control —
        # but it rejects an *absent* Authorization header on some builds, and
        # OpenRouter needs a real one. Sending a placeholder satisfies both.
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        keep_raw: bool = False,
    ) -> Response:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"

        t0 = time.perf_counter()
        try:
            r = self._client.post("/chat/completions", json=body)
            latency = time.perf_counter() - t0
            if r.status_code != 200:
                return Response(
                    ok=False,
                    latency_s=latency,
                    error=f"HTTP {r.status_code}: {r.text[:300]}",
                )
            payload = r.json()
        except Exception as exc:  # noqa: BLE001 — a transport failure is a datum
            return Response(
                ok=False,
                latency_s=time.perf_counter() - t0,
                error=f"{type(exc).__name__}: {exc}",
            )

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        return Response(
            ok=True,
            latency_s=latency,
            finish_reason=choice.get("finish_reason"),
            content=message.get("content") or "",
            reasoning=message.get("reasoning") or "",
            tool_calls=message.get("tool_calls") or [],
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=payload if keep_raw else None,
        )

    def complete_streaming(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> Response:
        """Same call, but timed for time-to-first-token.

        TTFT is measured separately because it is the number a person actually
        feels, and it is invisible in a non-streaming total: a thinking model
        can spend twenty seconds reasoning and then emit an answer in one burst,
        which reads as a hang rather than as slow output.
        """
        import json

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            # Without this the final chunk carries no usage and tokens/second is
            # uncomputable for every streamed call.
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"

        t0 = time.perf_counter()
        ttft: float | None = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        # Streamed tool calls arrive as indexed fragments that must be stitched.
        fragments: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        completion_tokens: int | None = None
        prompt_tokens: int | None = None

        try:
            with self._client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code != 200:
                    r.read()
                    return Response(
                        ok=False,
                        latency_s=time.perf_counter() - t0,
                        error=f"HTTP {r.status_code}: {r.text[:300]}",
                    )
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if usage := chunk.get("usage"):
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                    choice = (chunk.get("choices") or [{}])[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    if think := delta.get("reasoning"):
                        reasoning_parts.append(think)
                    piece = delta.get("content")
                    if piece:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        content_parts.append(piece)
                    for frag in delta.get("tool_calls") or []:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        idx = frag.get("index", 0)
                        slot = fragments.setdefault(
                            idx, {"id": "", "type": "function",
                                  "function": {"name": "", "arguments": ""}}
                        )
                        if frag.get("id"):
                            slot["id"] = frag["id"]
                        fn = frag.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
        except Exception as exc:  # noqa: BLE001
            return Response(
                ok=False,
                latency_s=time.perf_counter() - t0,
                error=f"{type(exc).__name__}: {exc}",
            )

        return Response(
            ok=True,
            latency_s=time.perf_counter() - t0,
            ttft_s=ttft,
            finish_reason=finish_reason,
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=[fragments[i] for i in sorted(fragments)],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
