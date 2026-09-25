"""The real client for the commercial subject model (spec.md §5's frozen v0 configuration):
Sonnet-class tier, extended thinking disabled, no system instruction, no retrieval/tools -- sent
through Anthropic's Messages API.

`temperature`/`top_p`/`top_k` are not sent: the current Messages API no longer accepts them at
all (confirmed by introspecting the installed `anthropic` SDK -- zero references anywhere in the
package -- and by a live 400/TypeError when sent). This project no longer relies on a client-set
sampling parameter for response variance -- confirmed live across 4 protocol families, repeated
identical prompts come back deterministic regardless, so nothing is lost by not having the knob.

Thinking, unlike sampling, is still controllable and IS fixed here: left unset, the API defaults
to adaptive (automatic, variable-length) thinking -- confirmed via a live call that spent 231-367
thinking tokens with no `thinking` param sent at all. Explicitly passing
`thinking={"type": "disabled"}` (below) restores spec.md §5's "thinking disabled" and was
confirmed live to bring thinking_tokens to 0.

`explicitly_set` records every request-shaping knob, including the ones deliberately left at the
API's own default (`stop_sequences`/`tools`: `None`) -- spec.md §5's "every other parameter sent
explicitly" is about disclosing what was and wasn't overridden, not that every knob must be pinned
to a non-default value. `max_tokens` was originally 1024, matching `fake_client.py` and
`reference_client.py` -- raised to 4096 on 2026-09-22 after a real run
(`base_rate_neglect__anchoring__v0`, 2026-09-21) truncated one response mid-calculation
(`stop_reason=max_tokens`), which by itself produced `drop_confounded`/`asymmetric_missingness`
and took the measurement off the curve. Response-length data from the 240 real Sonnet calls made
so far (two full protocols): median ~310-390 estimated tokens, p99 ~440-570, one outlier that hit
1024 -- 4096 covers that outlier's p99 many times over at negligible extra cost ($10/1M output
tokens on Sonnet 5), and only calls that actually need the room pay for it: `max_tokens` is a
ceiling the model is not billed for merely having available, never a floor. This does not
guarantee no future call is ever truncated again -- reasoning length has no hard ceiling -- it
lowers the odds. A future truncation is not a new bug: `outcome: truncated` is already caught and
disclosed correctly by the pipeline either way (`my_help/decisions.md` §42 discusses a separate,
unrelated extraction bug found during this same investigation).

Unlike `reference_client.py`'s `ReferenceClient` (a permanently pinned model, spec.md §7),
`model_id`/`model_family` are never hardcoded here: the commercial subject changes across
generations (spec.md §4.3's bridging), so they're required constructor arguments, supplied by the
caller from `subject_models.yaml`'s active commercial generation (or, for the first real run that
proves the client works at all -- spec.md §13's "commit #1" -- passed directly, before that file
is updated to point at the real model).

`anthropic` is imported lazily inside __init__, same reasoning as reference_client.py's
`llama_cpp` import: importing this module never requires the package installed, only actually
instantiating AnthropicClient does. Install with `pip install anthropic`.

No automated `verify()` here, same as `reference_client.py`: the one thing worth checking --
whether a real call actually round-trips -- can't be exercised without spending a real call
against a real, billed API. `_extract_text` is kept as a small, separately callable function so at
least the response-parsing shape can be checked by hand against a real `Message` object.
"""

from __future__ import annotations

import math
import os
from fractions import Fraction

from core.measure.client import Response

MAX_TOKENS = 4096
TIMEOUT_S = 120.0
MAX_RETRIES = 3

# List price, USD per 1M tokens (input, output), first-party API. Used only to turn the token counts
# the API itself returns (`message.usage`) into a spend figure for the ledger -- measured usage x
# published price, not a guess about how many tokens a call "usually" takes. A model missing from
# this table has NO cost (`cost_usd()` returns None) and the caller falls back to logging the real
# bill by hand, so a new model can never be silently priced at the wrong rate. Sonnet 5: $2 / $10
# (Anthropic price list, checked 2026-09-25). The Console shows dollars and the ledger has always
# held that same number in its `amount_eur` field (see the 2026-09-25 spend.json entry's note).
PRICES_USD_PER_MTOK = {"claude-sonnet-5": (Fraction(2), Fraction(10))}

# Circuit breaker: a run that cannot get answers must stop spending, not run to the end. A
# credentials/permission/model-not-found error (401/403/404) will repeat on every remaining call,
# so the first one opens the breaker. Anything else (network, 429/5xx after the SDK's own retries)
# opens it after this many failures in a row.
BREAKER_CONSECUTIVE_ERRORS = 5


class CircuitOpen(RuntimeError):
    """Raised by AnthropicClient.complete() when the run should stop. Never raised for a single
    failed call -- that is still a `blocked_upstream` trial, scored and disclosed like any other."""


def _extract_text(content_blocks) -> str | None:
    """A Messages API response's `content` is a list of typed blocks; only `text` blocks count
    here (no `tools`/`thinking` are requested, so none should appear, but a response with zero
    text blocks -- e.g. an empty completion -- must still map to `None`, not `""`, so
    `classify()` scores it `off_format` rather than failing the grammar on empty string)."""
    texts = [block.text for block in content_blocks if getattr(block, "type", None) == "text"]
    return "\n".join(texts) if texts else None


class AnthropicClient:
    series = "commercial"

    def __init__(self, model_id: str, model_family: str, api_key: str | None = None) -> None:
        if not model_id:
            raise ValueError(
                "model_id is required -- pass it explicitly, or set subject_models.yaml's "
                "commercial generation to status: active with a real model_id first"
            )
        self.subject_model_id = model_id
        self.model_family = model_family
        self.explicitly_set = {
            "temperature": "not_client_controllable",  # removed from the API -- see module docstring
            "top_p": "not_client_controllable",
            "top_k": "not_client_controllable",
            "max_tokens": MAX_TOKENS,
            "thinking": "disabled",
            "system": None,
            "stop_sequences": None,
            "tools": None,
            "timeout_s": TIMEOUT_S,
            "max_retries": MAX_RETRIES,
        }

        import anthropic  # lazy -- see module docstring

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it in the shell before running this client -- "
                "never write it into a file in this repo (spec.md §10: secrets are a never-commit rule)."
            )
        self._anthropic = anthropic
        self.input_tokens = 0
        self.output_tokens = 0
        self._consecutive_errors = 0
        self._client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)

    def complete(self, prompt: str, *, meta: dict) -> Response:
        """`meta` is accepted only for Client-protocol compatibility; ignored, per client.py's own
        contract that a real client must never use it."""
        del meta
        try:
            message = self._client.messages.create(
                model=self.subject_model_id,
                max_tokens=MAX_TOKENS,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
            )
        except self._anthropic.APIError as e:
            # Network/transport/provider failure, not a modeling decision -- classify() maps this
            # to outcome "blocked_upstream", never scored as a valid or invalid decision. The SDK
            # itself already retried (max_retries=3) on connection errors and 429/5xx before this
            # was raised, so nothing is retried again here.
            self._consecutive_errors += 1
            status = getattr(e, "status_code", None)
            if status in (401, 403, 404) or self._consecutive_errors >= BREAKER_CONSECUTIVE_ERRORS:
                raise CircuitOpen(
                    f"stopping the run: {'HTTP ' + str(status) if status else 'connection error'}, "
                    f"{self._consecutive_errors} failed call(s) in a row -- {e}"
                ) from e
            return Response(None, None, None, error=str(e))

        self._consecutive_errors = 0
        usage = getattr(message, "usage", None)
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

        return Response(_extract_text(message.content), message.stop_reason, message.model)

    def cost_usd(self) -> Fraction | None:
        """What this client's calls have cost so far, in USD, rounded UP to the cent's tenth so the
        ledger errs a hair high, never low. None when the model has no listed price."""
        price = PRICES_USD_PER_MTOK.get(self.subject_model_id)
        if price is None:
            return None
        raw = (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1_000_000
        return Fraction(math.ceil(raw * 10_000), 10_000)
