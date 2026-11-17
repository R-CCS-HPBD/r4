"""Alias: this module IS umrs_protocol.security (ADR-117 D-2, U2.0). The code lives in umrs/protocol; add nothing here."""

import sys

from umrs_protocol import security as _impl

sys.modules[__name__] = _impl
