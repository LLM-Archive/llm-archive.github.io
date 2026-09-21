"""The frozen open-weights reference model used as the instrument's own control (spec.md §7,
core/instrument/). Every setting below is sent explicitly, never left at a library default —
same rule as any other client (spec.md §5) — because "the reference model never moves" only
means something if every knob that could make it move is pinned and recorded.

Pinned artifact (Phase 0 decision, does not change): Qwen/Qwen2.5-1.5B-Instruct-GGUF, revision
91cad51170dc346986eccefdc2dd33a9da36ead9, file qwen2.5-1.5b-instruct-q4_k_m.gguf, Apache-2.0,
published directly by the Qwen team (no third-party quantizer in the provenance chain).

`llama_cpp` is imported lazily inside __init__, not at module level, so that importing this file
(e.g. from something that enumerates core/plumbing/) never requires the package to be installed
-- only actually instantiating ReferenceClient does.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from core.measure.client import Response

MODEL_SHA256 = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
DEFAULT_MODEL_PATH = Path("/opt/reference-model/qwen2.5-1.5b-instruct-q4_k_m.gguf")

# Fixed, not read from the runner's own CPU count: llama.cpp's multi-threaded matrix multiply can
# reduce partial sums in a different order depending on thread count, which can change the last
# bit of a float even at temperature 0. A thread count read from the environment would make
# "byte-identical" mean nothing. Set to 2 to MATCH the actual GitHub Actions standard runner
# (measured via os.cpu_count() in the CI cost/throughput test) -- 4 was oversubscribing a 2-vCPU
# box, adding contention instead of parallelism.
N_THREADS = 2
N_CTX = 4096
MAX_TOKENS = 1024

# Fixed (same reasoning as N_THREADS -- batch size can shift float reduction order too), and NOT
# 1: n_batch controls how many prompt tokens llama.cpp processes per forward pass during prefill.
# n_batch=1 (an earlier version of this file) ingests the prompt one token at a time instead of in
# one batched pass -- found to be the dominant cost in the CI cost/throughput test, not generation
# itself (a 4-token reply still took ~9s wall time). 512 comfortably covers this project's prompts.
N_BATCH = 512

# ChatML, written out literally rather than left to a library's auto-detected template: Qwen2.5's
# own instruct format. Hashing this string is the "chat-template sha" spec.md §7 asks each
# scan to carry.
CHAT_TEMPLATE = "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"
SYSTEM_PROMPT = ""
STOP = ["<|im_end|>"]


def chat_template_sha256() -> str:
    return hashlib.sha256(CHAT_TEMPLATE.encode("utf-8")).hexdigest()


def _verify_model_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"reference model not found at {path}")
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    if digest.hexdigest() != MODEL_SHA256:
        raise ValueError(f"{path} does not match the pinned sha256 {MODEL_SHA256} -- got {digest.hexdigest()}")


def load_llama(model_path: Path | None = None, *, logits_all: bool = False):
    """Shared by ReferenceClient and runtime_fingerprint.py so the pinned load parameters exist
    in exactly one place.

    `logits_all` defaults to False: llama-cpp-python 0.3.35 refuses `logprobs` on a completion
    unless the model was loaded with `logits_all=True` (found running the CI cost/throughput
    test), but that flag makes llama.cpp compute logits for every position in the context on
    every call, not just the sampled one -- real overhead this client cannot afford across 1,350
    guard-protocol calls/day. Only runtime_fingerprint.py (12 calls/day) asks for it.
    """
    path = model_path or Path(os.environ.get("REFERENCE_MODEL_PATH", DEFAULT_MODEL_PATH))
    _verify_model_file(path)

    from llama_cpp import Llama

    return Llama(
        model_path=str(path),
        n_ctx=N_CTX,
        n_threads=N_THREADS,
        n_batch=N_BATCH,
        logits_all=logits_all,
        seed=0,
        verbose=False,
    )


class ReferenceClient:
    subject_model_id = "qwen2.5-1.5b-instruct-q4_k_m"
    model_family = "qwen2.5"
    series = "open_weights"
    explicitly_set = {
        "temperature": 0.0,
        "top_k": 1,
        "top_p": 1.0,
        "repeat_penalty": 1.0,
        "max_tokens": MAX_TOKENS,
        "n_threads": N_THREADS,
        "n_ctx": N_CTX,
        "n_batch": N_BATCH,
        "seed": 0,
        "stop": STOP,
        "system": SYSTEM_PROMPT,
        "chat_template_sha256": chat_template_sha256(),
    }

    def __init__(self, model_path: Path | None = None) -> None:
        self._llama = load_llama(model_path)

    def complete(self, prompt: str, *, meta: dict) -> Response:
        """`meta` is accepted only for Client-protocol compatibility; ignored, per client.py's own
        contract that a real client must never use it."""
        del meta
        full_prompt = CHAT_TEMPLATE.format(system=SYSTEM_PROMPT, user=prompt)
        try:
            result = self._llama(
                full_prompt,
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                top_k=1,
                top_p=1.0,
                repeat_penalty=1.0,
                stop=STOP,
                seed=0,
            )
        except Exception as e:  # transport/runtime failure, not a modeling decision
            return Response(None, None, None, error=str(e))

        choice = result["choices"][0]
        stop_reason = "end_turn" if choice["finish_reason"] == "stop" else "max_tokens"
        return Response(choice["text"], stop_reason, self.subject_model_id)
