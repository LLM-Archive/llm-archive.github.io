"""Which protocol version is the current one. A protocol_id ends in `__v<N>`; everything before
that suffix is its family. Only the highest admitted version of each family is current -- the
scheduler sweeps only those and the site counts only those, so a superseded series (v0 once v1 is
admitted) is never measured again and never inflates a "N of TOTAL protocols" figure."""
from __future__ import annotations

import re

_VERSION_SUFFIX = re.compile(r"^(?P<family>.+)__v(?P<n>\d+)$")


def latest_versions(admitted: list[dict]) -> list[dict]:
    """Keep only the highest `__v<N>` of each protocol family. A protocol_id without a `__v<N>`
    suffix is its own family. Callers pass only admitted protocols, so a newer candidate does not
    displace the version that is currently the admitted one."""
    best: dict[str, tuple[int, dict]] = {}
    for protocol in admitted:
        m = _VERSION_SUFFIX.match(protocol["protocol_id"])
        family, n = (m["family"], int(m["n"])) if m else (protocol["protocol_id"], 0)
        if family not in best or n > best[family][0]:
            best[family] = (n, protocol)
    return [protocol for _, protocol in best.values()]
