"""
Unit tests for r4 RAG module (A-201, A-202, A-203).

Tests:
- RAGQuery creation and serialization
- RAGSource and RAGResponse parsing
- UploadResult handling
"""

from unittest.mock import MagicMock, patch

import pytest


def _mock_http_client_response(payload, status_code: int = 200, text: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.text = text
    response.json.return_value = payload
    return response


# =============================================================================
# RAGQuery Tests
# =============================================================================


class TestRAGQuery:
    """Tests for RAGQuery dataclass."""

    def test_default_values(self):
        """Test RAGQuery default values."""
        from r4.rag import RAGQuery

        query = RAGQuery(query="test query", project_id="test-project")

        assert query.query == "test query"
        assert query.project_id == "test-project"
        assert query.top_k == 5
        # ADR-093 D-10: no implicit 0.7 cut; the server decides relevance.
        assert query.threshold is None
        assert query.include_sources is True
        assert query.filters is None

    def test_custom_values(self):
        """Test RAGQuery with custom values."""
        from r4.rag import RAGQuery

        query = RAGQuery(
            query="custom query",
            project_id="project-x",
            top_k=10,
            threshold=0.5,
            include_sources=False,
            filters={"category": "science"},
        )

        assert query.top_k == 10
        assert query.threshold == 0.5
        assert query.include_sources is False
        assert query.filters == {"category": "science"}

    def test_to_dict_basic(self):
        """Test converting query to dict format."""
        from r4.rag import RAGQuery

        query = RAGQuery(query="test", project_id="proj")
        result = query.to_dict()

        assert result["query"] == "test"
        assert result["project_id"] == "proj"
        assert result["top_k"] == 5
        assert "threshold" not in result  # None is omitted, never sent as null
        assert result["include_sources"] is True
        assert "filters" not in result

    def test_to_dict_keeps_an_explicit_threshold(self):
        """A float threshold is the legacy absolute cut and is sent as given."""
        from r4.rag import RAGQuery

        result = RAGQuery(query="test", project_id="proj", threshold=0.55).to_dict()

        assert result["threshold"] == 0.55

    def test_to_dict_with_filters(self):
        """Test converting query with filters to dict."""
        from r4.rag import RAGQuery

        query = RAGQuery(
            query="test",
            project_id="proj",
            filters={"type": "paper"},
        )
        result = query.to_dict()

        assert result["filters"] == {"type": "paper"}


# =============================================================================
# RAGSource Tests
# =============================================================================


class TestRAGSource:
    """Tests for RAGSource dataclass."""

    def test_basic_creation(self):
        """Test creating a RAGSource."""
        from r4.rag import RAGSource

        source = RAGSource(
            document_id="doc-123",
            content="This is the content",
            score=0.95,
        )

        assert source.document_id == "doc-123"
        assert source.content == "This is the content"
        assert source.score == 0.95
        assert source.metadata == {}

    def test_with_metadata(self):
        """Test RAGSource with metadata."""
        from r4.rag import RAGSource

        source = RAGSource(
            document_id="doc-456",
            content="Content with metadata",
            score=0.85,
            metadata={"filename": "test.pdf", "page": 5},
        )

        assert source.metadata["filename"] == "test.pdf"
        assert source.metadata["page"] == 5


# =============================================================================
# RAGResponse Tests
# =============================================================================


class TestRAGResponse:
    """Tests for RAGResponse dataclass."""

    def test_from_api_response_basic(self):
        """Test creating RAGResponse from API response."""
        from r4.rag import RAGResponse

        api_data = {
            "answer": "The answer is 42.",
            "sources": [
                {
                    "document_id": "doc-1",
                    "content": "Source content",
                    "score": 0.92,
                    "metadata": {"file": "guide.pdf"},
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        }

        response = RAGResponse.from_api_response(api_data)

        assert response.answer == "The answer is 42."
        assert len(response.sources) == 1
        assert response.sources[0].document_id == "doc-1"
        assert response.sources[0].score == 0.92
        assert response.usage["prompt_tokens"] == 50

    def test_from_api_response_empty_sources(self):
        """Test handling empty sources array."""
        from r4.rag import RAGResponse

        api_data = {"answer": "No sources found", "sources": []}

        response = RAGResponse.from_api_response(api_data)

        assert response.answer == "No sources found"
        assert response.sources == []

    def test_from_api_response_missing_fields(self):
        """Test handling missing optional fields."""
        from r4.rag import RAGResponse

        api_data = {}

        response = RAGResponse.from_api_response(api_data)

        assert response.answer == ""
        assert response.sources == []
        assert response.usage is None

    def test_from_api_response_multiple_sources(self):
        """Test parsing multiple sources."""
        from r4.rag import RAGResponse

        api_data = {
            "answer": "Multiple sources",
            "sources": [
                {"document_id": "doc-1", "content": "First", "score": 0.9},
                {"document_id": "doc-2", "content": "Second", "score": 0.8},
                {"document_id": "doc-3", "content": "Third", "score": 0.7},
            ],
        }

        response = RAGResponse.from_api_response(api_data)

        assert len(response.sources) == 3
        assert response.sources[0].score == 0.9
        assert response.sources[1].score == 0.8
        assert response.sources[2].score == 0.7


# =============================================================================
# UploadResult Tests
# =============================================================================


class TestUploadResult:
    """Tests for UploadResult dataclass."""

    def test_successful_upload(self):
        """Test successful upload result."""
        from r4.rag import UploadResult

        result = UploadResult(
            document_id="doc-new",
            filename="paper.pdf",
            status="completed",
            chunks=15,
        )

        assert result.document_id == "doc-new"
        assert result.filename == "paper.pdf"
        assert result.status == "completed"
        assert result.chunks == 15
        assert result.error is None

    def test_failed_upload(self):
        """Test failed upload result."""
        from r4.rag import UploadResult

        result = UploadResult(
            document_id="",
            filename="bad.pdf",
            status="failed",
            chunks=0,
            error="File too large",
        )

        assert result.status == "failed"
        assert result.error == "File too large"
        assert result.chunks == 0

    def test_default_chunks(self):
        """Test default chunks value."""
        from r4.rag import UploadResult

        result = UploadResult(
            document_id="doc-1",
            filename="test.txt",
            status="completed",
        )

        assert result.chunks == 0


class TestRAGClientQueryThreshold:
    """ADR-093 D-10: query()/search() no longer send an implicit 0.7."""

    def _post_body(self, call_rag):
        from r4.client import Client
        from r4.config import Config

        response = _mock_http_client_response({"answer": "a", "sources": []})
        with patch("httpx.Client") as mock_http_client:
            http_client = mock_http_client.return_value.__enter__.return_value
            http_client.post.return_value = response
            client = Client(
                config=Config(
                    gateway_url="http://gateway.test", api_key="secret", project_id="project-a"
                )
            )
            call_rag(client)
        return http_client.post.call_args.kwargs["json"]

    def test_query_omits_threshold_by_default(self):
        body = self._post_body(lambda client: client.rag.query("q"))
        assert "threshold" not in body

    def test_search_alias_omits_threshold_by_default(self):
        body = self._post_body(lambda client: client.rag.search("q"))
        assert "threshold" not in body

    def test_explicit_threshold_is_sent(self):
        body = self._post_body(lambda client: client.rag.query("q", threshold=0.4))
        assert body["threshold"] == 0.4


class TestRAGClientUpload:
    """Tests for Gateway-backed document upload."""

    def test_upload_posts_multipart_to_gateway(self, tmp_path):
        from r4.client import Client
        from r4.config import Config

        path = tmp_path / "paper.txt"
        path.write_text("RAG upload test document", encoding="utf-8")
        response = _mock_http_client_response(
            {
                "document_id": "doc-123",
                "filename": "paper.txt",
                "status": "indexed",
                "chunks": 2,
            }
        )

        with patch("httpx.Client") as mock_http_client:
            http_client = mock_http_client.return_value.__enter__.return_value
            http_client.post.return_value = response

            client = Client(
                config=Config(
                    gateway_url="http://gateway.test",
                    api_key="secret",
                    project_id="project-a",
                )
            )
            result = client.rag.upload(str(path), metadata={"kind": "note"})

        assert result.document_id == "doc-123"
        assert result.filename == "paper.txt"
        assert result.status == "indexed"
        assert result.chunks == 2
        call = http_client.post.call_args
        assert call.args[0] == "http://gateway.test/gw/rag/upload"
        assert call.kwargs["data"]["project_id"] == "project-a"
        assert call.kwargs["data"]["metadata"] == '{"kind": "note"}'
        assert call.kwargs["files"]["file"][0] == "paper.txt"
        assert "Content-Type" not in call.kwargs["headers"]

    def test_upload_requires_project_id(self, tmp_path):
        from r4.client import Client
        from r4.config import Config
        from r4.errors import R4Error

        path = tmp_path / "paper.txt"
        path.write_text("RAG upload test document", encoding="utf-8")
        client = Client(config=Config(gateway_url="http://gateway.test"))

        with pytest.raises(R4Error, match="project_id is required"):
            client.rag.upload(str(path))

    def test_upload_requires_existing_file(self):
        from r4.client import Client
        from r4.config import Config
        from r4.errors import R4Error

        client = Client(
            config=Config(
                gateway_url="http://gateway.test",
                project_id="project-a",
            )
        )

        with pytest.raises(R4Error, match="File not found"):
            client.rag.upload("missing.txt")


class TestRAGClientDocuments:
    """Tests for document listing response shapes."""

    def test_list_documents_accepts_bare_list_payload(self):
        from r4.client import Client
        from r4.config import Config

        response = _mock_http_client_response([{"document_id": "doc-1", "filename": "paper.txt"}])

        with patch("httpx.Client") as mock_http_client:
            http_client = mock_http_client.return_value.__enter__.return_value
            http_client.get.return_value = response

            client = Client(
                config=Config(
                    gateway_url="http://gateway.test",
                    api_key="secret",
                    project_id="project-a",
                )
            )
            documents = client.rag.list_documents()

        assert documents == [{"document_id": "doc-1", "filename": "paper.txt"}]
