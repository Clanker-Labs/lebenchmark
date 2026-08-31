"""Where the endpoint, the models and the run size come from.

Precedence, highest first: a command-line flag, then the environment, then
``.env`` in the working directory, then the defaults below.

The defaults point at a local Ollama on loopback rather than at any particular
machine, because the interesting version of this benchmark is the one somebody
else runs against their own gateway. Anything OpenAI-compatible works — Ollama,
vLLM, llama.cpp's server, LM Studio, a hosted provider — so pointing it
somewhere new is a line in ``.env``, not a patch.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Loopback Ollama. Not this fleet's gateway — see the module docstring.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODELS = "chat,coder,fast,vision"
DEFAULT_BUDGETS = "512,1024,2048,4096,8192"
ENV_PREFIX = "LEBENCHMARK_"


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Parse a KEY=value file. No interpolation, no export, no dependency.

    A real environment variable always wins, so a CI job does not have to delete
    a checked-out ``.env`` to override it.
    """
    file = Path(path)
    if not file.exists():
        return {}
    values: dict[str, str] = {}
    for raw in file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(slots=True)
class Config:
    base_url: str
    api_key: str
    models: str
    budgets: str
    #: Optional URL of an ops dashboard exposing `/api/status`, used only to
    #: record which engine and preset produced a result. Absent is fine.
    harness_url: str | None
    reps: int
    budget_reps: int
    latency_reps: int
    concurrency: int
    tasks_dir: str
    results_dir: str

    @classmethod
    def load(cls, dotenv: str | Path = ".env") -> Config:
        file_values = load_dotenv(dotenv)

        def get(name: str, default: str) -> str:
            key = ENV_PREFIX + name
            return os.environ.get(key) or file_values.get(key) or default

        def get_int(name: str, default: int) -> int:
            raw = get(name, str(default))
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{ENV_PREFIX}{name} must be a whole number, got {raw!r}") from exc

        harness = get("HARNESS_URL", "")
        return cls(
            base_url=get("BASE_URL", DEFAULT_BASE_URL),
            # Gateways that need no auth still tend to reject a missing header,
            # so a placeholder is sent unless a real key is configured.
            api_key=get("API_KEY", "lebenchmark"),
            models=get("MODELS", DEFAULT_MODELS),
            budgets=get("BUDGETS", DEFAULT_BUDGETS),
            harness_url=harness or None,
            reps=get_int("REPS", 18),
            budget_reps=get_int("BUDGET_REPS", 15),
            latency_reps=get_int("LATENCY_REPS", 20),
            concurrency=get_int("CONCURRENCY", 1),
            tasks_dir=get("TASKS_DIR", "tasks"),
            results_dir=get("RESULTS_DIR", "results"),
        )
