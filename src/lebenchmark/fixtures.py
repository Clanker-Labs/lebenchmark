"""A realistic tool result, for the experiments that need context in the window.

The reasoning-budget failure only appears on a *second* turn: the model calls a
tool, the loop feeds the whole result back, and the answer has to be produced
after reasoning over it. With a two-line result there is no failure to see. This
payload is the shape `ecosystem_status` really returns on this box — every app,
every service, every port — because the length is the point.
"""

from __future__ import annotations

_APPS = [
    ("moude", [("web", 3010, "up"), ("worker", 3011, "up"), ("postgres", 5432, "up")], ""),
    ("clankergram", [("web", 3020, "up"), ("api", 4000, "up"), ("postgres", 5433, "up"),
                     ("redis", 6380, "up"), ("minio", 9000, "up")],
     "docker-compose.infra.yml hardcodes 5432/6379; run one stack's infra at a time"),
    ("ai212", [("dashboard", 8080, "up")], "loopback only, unauthenticated, order execution off"),
    ("leclanker", [("server", 8484, "up")], ""),
    ("jinsen", [("common", 8000, "up"), ("planning", 8005, "up"), ("planning-ui", 3000, "up"),
                ("finances", 8006, "up"), ("finances-ui", 3001, "up"),
                ("postgres", 5434, "up"), ("redis", 6379, "up")], ""),
    ("jinsen-health", [("backend", 8007, "unhealthy (HTTP 502)"), ("frontend", 5175, "up")],
     "calls jinsen-ai on 8010"),
    ("jinsen-lists", [("backend", 8008, "up"), ("frontend", 5176, "up")],
     "no real auth, DEFAULT_OWNER_ID=local"),
    ("selflix", [("web", 8100, "up"), ("qbittorrent", 8101, "up")],
     "library reads empty when the media drive is not mounted"),
    ("selfmail", [("api", 8110, "up")], "without SMTP_HOST it runs and refuses to send"),
    ("selfkey", [("api", 8120, "unhealthy (HTTP 423)")],
     "starts LOCKED after every restart; a human types the passphrase"),
    ("larecherche", [("web", 8130, "up"), ("mcp", 8131, "up")], ""),
    ("theworld", [("web", 8140, "up")], ""),
    ("theroundtable", [("web", 8150, "up")], ""),
    ("guessflix", [("web", 8160, "up")], ""),
    ("safepill", [("web", 8170, "down")], ""),
    ("tradesights", [("mcp", 8094, "up"), ("dashboard", 8095, "up")],
     "/mcp answers 406 to a plain GET"),
    ("selftrade", [("dashboard", 8180, "up")], "cannot trade without SELFTRADE_ARM_LIVE"),
    ("localflow", [("api", 7317, "up")], "host process, not compose"),
    ("cortex", [("api", 8190, "up")], ""),
]


def ecosystem_status_payload() -> str:
    lines = []
    for name, services, note in _APPS:
        rendered = ", ".join(f"{svc}:{port} {state}" for svc, port, state in services)
        suffix = f" — note: {note}" if note else ""
        lines.append(f"{name}: {rendered}{suffix}")
    return "\n".join(lines)


#: The follow-up asked after the tool result comes back. Open-ended on purpose:
#: it needs the model to read the whole payload rather than grep one line.
BUDGET_QUESTION = (
    "Which apps need my attention, and what should I do about each one? "
    "Be specific about what is actually wrong."
)
