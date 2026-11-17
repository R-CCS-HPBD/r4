"""Alias: this module IS umrs_protocol.hpc_gate (ADR-117 D-2, U2.0). The code lives in umrs/protocol; add nothing here."""

import sys

from umrs_protocol import hpc_gate as _impl

sys.modules[__name__] = _impl
