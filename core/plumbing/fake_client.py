"""A synthetic subject model: exercises the whole pipeline offline, at zero cost.

It says nothing about any real model. Its behaviour is a fixed table keyed on the trial's
version — information a real client never receives.
"""

from __future__ import annotations

import hashlib

from core.measure.client import Response
from core.measure.rng import SplitMix64, seed_from

P_OPTION_B = {"A": 0.30, "A_prime": 0.31, "B": 0.52, "C": 0.93}
P_REFUSED = 0.002
P_TRUNCATED = 0.002
P_UNPARSEABLE = 0.003

VALID_STYLES = (
    "DECISION: {t}",
    "**DECISION:** {t}",
    "Decision: {t}.",
    "DECISION：{t}",
    "  decision :  `{t}`  ",
)
UNPARSEABLE_STYLES = (
    "On balance I lean towards Plan {t}.",
    "DECISION: {t}\nDECISION: {o}",
    "DECISION: Plan {t}",
)


def _scenario_offset(scenario_id: str) -> float:
    return (int(hashlib.sha256(scenario_id.encode()).hexdigest(), 16) % 21 - 10) / 100


class FakeClient:
    subject_model_id = "fake-subject-v1"
    model_family = "fake"
    series = "commercial"
    # Mirrors AnthropicClient's explicitly_set shape (core/plumbing/anthropic_client.py):
    # temperature isn't a client-controllable parameter on the real commercial API anymore.
    explicitly_set = {"temperature": "not_client_controllable", "max_tokens": 1024, "thinking": "disabled", "system": None}

    def __init__(self, seed: str = "fake-v1") -> None:
        self.seed = seed

    def complete(self, prompt: str, *, meta: dict) -> Response:
        rng = SplitMix64(seed_from(self.seed, meta["version"], meta["scenario_id"], str(meta["rep"])))

        def uniform() -> float:
            return rng.next() / 2**64

        r = uniform()
        if r < P_REFUSED:
            return Response("I'd rather not choose between these.", "refusal", self.subject_model_id)
        if r < P_REFUSED + P_TRUNCATED:
            return Response("Let me weigh the two plans carefully. Plan A offers", "max_tokens", self.subject_model_id)

        p = min(max(P_OPTION_B[meta["version"]] + _scenario_offset(meta["scenario_id"]), 0.0), 1.0)
        token, other = ("B", "A") if uniform() < p else ("A", "B")
        reasoning = "Plan A is tempting, but Plan B deserves a look. Weighing both:\n\n"

        if r < P_REFUSED + P_TRUNCATED + P_UNPARSEABLE:
            style = UNPARSEABLE_STYLES[rng.below(len(UNPARSEABLE_STYLES))]
        else:
            style = VALID_STYLES[rng.below(len(VALID_STYLES))]
        return Response(reasoning + style.format(t=token, o=other), "end_turn", self.subject_model_id)
