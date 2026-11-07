"""Signal a child's whole process group: one helper for every spawn site.

The children r4 starts lead their own session (``start_new_session=True``),
so their pid is also their process group id. Under the local tool sandbox
(ADR-101) the child is the outer wrapper and the group reaches everything
inside it. A group that is gone (or not ours) is tolerated; where the
platform has no ``killpg`` the child alone is signalled.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from typing import Any

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def signal_group(proc: subprocess.Popen[Any], sig: int = _SIGKILL) -> None:
    """Send ``sig`` to ``proc``'s process group; ``proc`` alone without killpg."""
    killpg = getattr(os, "killpg", None)
    with contextlib.suppress(OSError):
        if killpg is not None:
            killpg(proc.pid, sig)
        elif sig == _SIGKILL:
            proc.kill()
        else:
            proc.terminate()
