"""R4 drift remediation (2026-08-22): dead /gw/data/* paths now raise R4Error.

The UMRS Gen1 HTTP data API never had a mounted server route — these
fallbacks used to produce opaque 404 trails (or silently no-op). The
retirement error names the working alternatives (ZMQ transport / catalog).
"""

from unittest.mock import MagicMock

import pytest

from r4.data import DataClient
from r4.errors import R4Error


def _client():
    return DataClient(MagicMock())


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c._upload_via_http(MagicMock(), "proj", None),
        lambda c: c._download_via_http("file-1", "/tmp/x", "proj"),
        lambda c: c.list_files(project_id="proj"),
    ],
)
def test_retired_http_paths_raise_descriptive_error(call):
    client = _client()
    with pytest.raises(R4Error, match="no server route"):
        call(client)


def test_retired_message_names_alternatives():
    client = _client()
    with pytest.raises(R4Error, match="ZeroMQ"):
        client.list_files(project_id="proj")


def test_upload_never_falls_back_to_retired_route() -> None:
    """Round-5 task 2: the upload path must raise a typed zmq_unavailable error
    when the transport fails, never the retired HTTP path."""
    import tempfile as _t
    from pathlib import Path as _P
    from unittest.mock import MagicMock

    from r4.data import DataClient
    from r4.errors import R4Error

    fake_config = MagicMock(
        project_id="probe", use_umrs_transport=True, gateway_url="https://data0"
    )
    client = DataClient(MagicMock(config=fake_config))
    # transport constructible but connect() -> False so upload fails
    transport = MagicMock(is_connected=False)
    transport.connect.return_value = False
    transport.last_connect_error = RuntimeError("HTTP Error 401: Unauthorized")
    client._umrs_transport = transport

    fd, p = _t.mkstemp()
    try:
        try:
            client.upload(p)
        except R4Error as e:
            assert e.message.startswith("[zmq_unavailable:auth_failed]")
            assert e.__cause__ is not None and "Cannot connect to UMRS server" in str(e.__cause__)
            assert "HTTP Error 401" in str(e.__cause__)
            return
        raise AssertionError("expected R4Error")
    finally:
        _P(p).unlink(missing_ok=True)
