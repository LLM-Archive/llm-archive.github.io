"""What a subject-model client must provide, and how a raw response becomes an outcome."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .grammar import Extraction, extract


@dataclass(frozen=True)
class Response:
    text: str | None  # None when the response carried no text content at all
    stop_reason: str | None
    returned_model_id: str | None
    error: str | None = None  # set when no response came back after the client's retries


class Client(Protocol):
    subject_model_id: str
    model_family: str
    series: str
    explicitly_set: dict  # every request parameter, sent explicitly (spec.md §5)

    def complete(self, prompt: str, *, meta: dict) -> Response:
        """`meta` (version, scenario_id, rep) is for test doubles only; a real client must ignore it."""
        ...


def classify(response: Response, options: list[str]) -> Extraction:
    """Maps a raw response to one outcome of a fixed, closed set — never free text — because the
    fraction of calls that fail to produce a usable decision (the "loss rate") is itself measured
    and fed into the drop-bound check in stats.py. A response that's hard to classify has to land
    in exactly one outcome bucket, consistently, or that check becomes meaningless.

    Precedence is fixed and applied in this order, every time: transport failure, then refusal,
    then truncation, then missing text, then the grammar. This matters because more than one
    condition can be true for the same response (e.g. a truncated reply that happens to still
    contain a complete decision line) — without a fixed order, which one wins would be
    accidental, and a truncated response must never be scored as a valid decision just because
    the model got lucky and finished the important part first.
    """
    if response.error is not None:
        return Extraction("blocked_upstream", None, response.error)
    if response.stop_reason == "refusal":
        return Extraction("refused", None, "stop_reason=refusal")
    if response.stop_reason == "max_tokens":
        return Extraction("truncated", None, "stop_reason=max_tokens")
    if response.text is None:
        return Extraction("off_format", None, "no_text_content")
    return extract(response.text, options)
