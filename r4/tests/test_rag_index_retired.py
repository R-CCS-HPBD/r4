"""ADR-027 P0: client.rag.index() retired (POST /gw/rag/index has no route)."""

from unittest.mock import MagicMock

import pytest


def test_index_raises_retirement_error():
    from r4.errors import R4Error
    from r4.rag import RAGClient

    client = RAGClient(MagicMock())
    with pytest.raises(R4Error, match="retired"):
        client.index(["file-1"], project_id="p")


def test_index_error_points_at_upload():
    from r4.errors import R4Error
    from r4.rag import RAGClient

    client = RAGClient(MagicMock())
    with pytest.raises(R4Error, match="rag.upload"):
        client.index(["file-1"], project_id="p")
