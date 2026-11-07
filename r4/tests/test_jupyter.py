"""
Tests for Jupyter Integration (A-501, A-502, A-503).

Tests:
- Jupyter magic commands
- Chat widget
- RAG widget
- Output formatting
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_client():
    """Mock r4 Client."""
    client = MagicMock()

    # Mock chat response
    chat_response = MagicMock()
    chat_response.choices = [MagicMock(message=MagicMock(content="Test response from LLM"))]
    client.chat = AsyncMock(return_value=chat_response)

    # Mock RAG client
    rag_response = MagicMock()
    rag_response.results = [
        MagicMock(
            content="Test document content",
            score=0.95,
            metadata={"source": "test.txt"},
        )
    ]
    client.rag = MagicMock()
    client.rag.query = AsyncMock(return_value=rag_response)

    return client


@pytest.fixture
def mock_ipython():
    """Mock IPython shell."""
    ipython = MagicMock()
    ipython.user_ns = {}
    return ipython


# =============================================================================
# OutputFormatter Tests (A-503)
# =============================================================================


class TestOutputFormatter:
    """Tests for OutputFormatter."""

    def test_markdown_output(self):
        """Test Markdown display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.markdown("# Test Header")
            mock_display.assert_called_once()

    def test_html_output(self):
        """Test HTML display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.html("<div>Test</div>")
            mock_display.assert_called_once()

    def test_code_output(self):
        """Test code display with syntax highlighting."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.code("print('hello')", language="python")
            mock_display.assert_called_once()
            # Check HTML contains the code
            call_args = mock_display.call_args
            html_obj = call_args[0][0]
            assert "print" in html_obj.data

    def test_chat_message_user(self):
        """Test user chat message display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.chat_message("user", "Hello!")
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "Hello!" in html_obj.data
            assert "👤" in html_obj.data

    def test_chat_message_assistant(self):
        """Test assistant chat message display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.chat_message("assistant", "I can help!")
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "I can help!" in html_obj.data
            assert "🤖" in html_obj.data

    def test_rag_result_display(self):
        """Test RAG result display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.rag_result(
                index=1,
                content="Test document content",
                score=0.95,
                source="test.txt",
                metadata={"page": 5},
            )
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "Test document content" in html_obj.data
            assert "0.95" in html_obj.data
            assert "test.txt" in html_obj.data

    def test_sources_summary(self):
        """Test sources summary display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.sources_summary(["doc1.txt", "doc2.txt", "doc1.txt"])
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            # Should deduplicate
            assert "(2)" in html_obj.data  # 2 unique sources

    def test_error_display(self):
        """Test error message display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.error("Something went wrong")
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "Something went wrong" in html_obj.data
            assert "❌" in html_obj.data

    def test_info_display(self):
        """Test info message display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.info("Processing...")
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "Processing..." in html_obj.data
            assert "ℹ️" in html_obj.data

    def test_success_display(self):
        """Test success message display."""
        from r4.widgets import OutputFormatter

        formatter = OutputFormatter()

        with patch("r4.widgets.display") as mock_display:
            formatter.success("Done!")
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "Done!" in html_obj.data
            assert "✅" in html_obj.data


# =============================================================================
# ChatWidget Tests (A-502)
# =============================================================================


class TestChatWidget:
    """Tests for ChatWidget."""

    def test_chat_widget_init(self):
        """Test ChatWidget initialization."""
        from r4.widgets import ChatWidget

        widget = ChatWidget(
            model="test-model",
            system_prompt="You are helpful.",
        )

        assert widget.model == "test-model"
        assert widget.system_prompt == "You are helpful."
        assert len(widget._history) == 1
        assert widget._history[0]["role"] == "system"

    def test_chat_widget_no_system_prompt(self):
        """Test ChatWidget without system prompt."""
        from r4.widgets import ChatWidget

        widget = ChatWidget()

        assert widget._history == []

    def test_chat_widget_clear(self):
        """Test clearing chat history."""
        from r4.widgets import ChatWidget

        widget = ChatWidget(system_prompt="Test")
        widget._history.append({"role": "user", "content": "Hello"})
        widget._history.append({"role": "assistant", "content": "Hi"})

        with patch("r4.widgets.display"):
            widget.clear()

        # Should only have system prompt
        assert len(widget._history) == 1
        assert widget._history[0]["role"] == "system"

    def test_chat_widget_history(self):
        """Test getting chat history."""
        from r4.widgets import ChatWidget

        widget = ChatWidget()
        widget._history.append({"role": "user", "content": "Hello"})

        history = widget.history()

        # Should return a copy
        assert history == widget._history
        assert history is not widget._history

    def test_chat_widget_display(self):
        """Test display method."""
        from r4.widgets import ChatWidget

        widget = ChatWidget()

        with patch("r4.widgets.display") as mock_display:
            widget.display()
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "R4 Chat" in html_obj.data
            assert "Notebook Helper" in html_obj.data


# =============================================================================
# RAGWidget Tests (A-502)
# =============================================================================


class TestRAGWidget:
    """Tests for RAGWidget."""

    def test_rag_widget_init(self):
        """Test RAGWidget initialization."""
        from r4.widgets import RAGWidget

        widget = RAGWidget(
            collection="my-docs",
            top_k=10,
        )

        assert widget.collection == "my-docs"
        assert widget.top_k == 10

    def test_rag_widget_default_values(self):
        """Test RAGWidget default values."""
        from r4.widgets import RAGWidget

        widget = RAGWidget()

        assert widget.collection == "default"
        assert widget.top_k == 5

    def test_rag_widget_display(self):
        """Test display method."""
        from r4.widgets import RAGWidget

        widget = RAGWidget(collection="test-collection")

        with patch("r4.widgets.display") as mock_display:
            widget.display()
            mock_display.assert_called_once()
            html_obj = mock_display.call_args[0][0]
            assert "test-collection" in html_obj.data


# =============================================================================
# Jupyter Magics Tests (A-501)
# =============================================================================


class TestR4Magics:
    """Tests for Jupyter magic commands."""

    def test_magics_registration(self):
        """Test that magics can be registered."""
        from r4.jupyter import R4Magics

        # Pass None as shell - R4Magics should handle this gracefully
        magics = R4Magics(shell=None)

        assert magics.shell is None

    def test_config_magic_set_model(self):
        """Test %r4_config for setting model."""
        from r4.jupyter import R4Magics

        magics = R4Magics(shell=None)

        with patch("r4.jupyter.magics.display"):
            magics.r4_config("model=test-model")

        assert magics._config["model"] == "test-model"

    def test_config_magic_set_collection(self):
        """Test %r4_config for setting collection."""
        from r4.jupyter import R4Magics

        magics = R4Magics(shell=None)

        with patch("r4.jupyter.magics.display"):
            magics.r4_config("collection=my-docs")

        assert magics._config["collection"] == "my-docs"

    def test_config_magic_set_temperature(self):
        """Test %r4_config for setting temperature."""
        from r4.jupyter import R4Magics

        magics = R4Magics(shell=None)

        with patch("r4.jupyter.magics.display"):
            magics.r4_config("temperature=0.5")

        assert magics._config["temperature"] == 0.5

    def test_config_magic_show(self):
        """Test %r4_config with no args shows config."""
        import io
        import sys

        from r4.jupyter import R4Magics

        magics = R4Magics(shell=None)

        # Capture stdout since config uses print()
        captured = io.StringIO()
        sys.stdout = captured
        try:
            magics.r4_config("")
        finally:
            sys.stdout = sys.__stdout__

        output = captured.getvalue()
        assert "Current configuration" in output or "model" in output

    def test_config_magic_invalid(self):
        """Test %r4_config with invalid setting."""
        import io
        import sys

        from r4.jupyter import R4Magics

        magics = R4Magics(shell=None)

        # Capture stdout since config uses print()
        captured = io.StringIO()
        sys.stdout = captured
        try:
            magics.r4_config("invalid_key=value")
        finally:
            sys.stdout = sys.__stdout__

        output = captured.getvalue()
        # Should print "Unknown setting: invalid_key"
        assert "Unknown setting" in output or "invalid_key" in output


# =============================================================================
# Integration Tests
# =============================================================================


class TestJupyterIntegration:
    """Integration tests for Jupyter components."""

    def test_load_extension(self):
        """Test extension loading."""
        from r4.jupyter import load_ipython_extension

        mock_ipython = MagicMock()

        load_ipython_extension(mock_ipython)

        mock_ipython.register_magics.assert_called_once()

    def test_unload_extension(self):
        """Test extension unloading."""
        from r4.jupyter import unload_ipython_extension

        mock_ipython = MagicMock()

        # Should not raise
        unload_ipython_extension(mock_ipython)

    def test_package_exports(self):
        """Test that Jupyter functions are exported from main package."""
        import r4

        assert hasattr(r4, "load_ipython_extension")
        assert hasattr(r4, "get_chat_widget")
        assert hasattr(r4, "get_rag_widget")
        assert hasattr(r4, "get_output_formatter")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
