"""Tests for provider JSON parsing helpers."""

from __future__ import annotations

import pytest

from r4.errors import APIError
from r4.providers._json import read_json_response


class _FakeResponse:
    def __init__(self, payload: bytes, *, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload


def test_read_json_response_parses_object():
    data = read_json_response(_FakeResponse(b'{"choices": []}'), label="gateway")
    assert data == {"choices": []}


def test_read_json_response_empty_body_raises_api_error():
    with pytest.raises(APIError, match="Empty response"):
        read_json_response(_FakeResponse(b""), label="gateway")


def test_read_json_response_invalid_json_raises_api_error():
    with pytest.raises(APIError, match="Invalid JSON"):
        read_json_response(_FakeResponse(b"not json"), label="gateway")
