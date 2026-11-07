"""Focused tests for Phase 1 trust-building changes.

F-020: Auth errors include r4 init hint.
F-024: 503 errors include endpoint provisioning hint.
F-006: r4 artifact commands detect A2L absence.
"""

import pytest

# =============================================================================
# F-020: Auth error → r4 init hint
# =============================================================================


class TestAuthErrorHints:
    """Auth errors (401/403) carry actionable recovery hints."""

    def test_401_includes_r4_init_hint(self):
        from r4.errors import AuthenticationError

        error = AuthenticationError(status_code=401)

        assert error.hint is not None
        assert "r4 auth login" in error.hint

    def test_401_default_message(self):
        from r4.errors import AuthenticationError

        error = AuthenticationError(status_code=401)

        assert "Authentication failed" in error.message

    def test_403_includes_project_hint(self):
        from r4.errors import AuthenticationError

        error = AuthenticationError("Access denied (403)", status_code=403)

        assert error.hint is not None
        assert "project" in error.hint.lower()

    def test_legacy_auth_error_still_has_hint(self):
        """AuthenticationError() without status_code still provides a hint."""
        from r4.errors import AuthenticationError

        error = AuthenticationError()

        assert error.hint is not None
        assert "r4 auth login" in error.hint

    def test_raise_for_status_401(self):
        """raise_for_status maps 401 to AuthenticationError with hint."""
        from r4._http import raise_for_status
        from r4.errors import AuthenticationError

        class FakeResponse:
            status_code = 401
            headers = {}

        with pytest.raises(AuthenticationError) as exc_info:
            raise_for_status(FakeResponse())

        assert exc_info.value.hint is not None
        assert "r4 auth login" in exc_info.value.hint

    def test_raise_for_status_403(self):
        """raise_for_status maps 403 to AuthenticationError with project hint."""
        from r4._http import raise_for_status
        from r4.errors import AuthenticationError

        class FakeResponse:
            status_code = 403
            headers = {}

        with pytest.raises(AuthenticationError) as exc_info:
            raise_for_status(FakeResponse())

        assert "403" in exc_info.value.message
        assert exc_info.value.hint is not None
        assert "project" in exc_info.value.hint.lower()

    def test_client_endpoint_list_preserves_auth_hint(self):
        """Endpoint-list SDK wrapper keeps auth recovery details."""
        from r4.client import Client
        from r4.config import Config
        from r4.errors import AuthenticationError, R4Error

        class FakeHTTP:
            def get(self, *_args, **_kwargs):
                raise AuthenticationError(status_code=401)

        client = Client(config=Config(gateway_url="http://gw:8080", api_key="bad-token"))
        client._http_core = FakeHTTP()

        with pytest.raises(R4Error) as exc_info:
            client.list_endpoints()

        assert "Failed to list endpoints" in str(exc_info.value)
        assert "r4 auth login" in exc_info.value.hint


# =============================================================================
# F-024: Actionable chat 503
# =============================================================================


class TestServiceUnavailableHint:
    """503 errors carry endpoint provisioning hints."""

    def test_api_error_503_has_hint(self):
        from r4.errors import APIError

        error = APIError("API error: 503", status_code=503)

        assert error.hint is not None
        assert "r4 endpoint up" in error.hint
        assert "r4 endpoint list" in error.hint

    def test_api_error_500_has_no_hint(self):
        """Non-503 API errors do not carry the endpoint hint."""
        from r4.errors import APIError

        error = APIError("API error: 500", status_code=500)

        assert error.hint is None

    def test_raise_for_status_503(self):
        """raise_for_status maps 503 to APIError with endpoint hint."""
        from r4._http import raise_for_status
        from r4.errors import APIError

        class FakeResponse:
            status_code = 503
            headers = {}
            text = '{"detail": "no active endpoint"}'

        with pytest.raises(APIError) as exc_info:
            raise_for_status(FakeResponse())

        assert exc_info.value.status_code == 503
        assert exc_info.value.hint is not None
        assert "r4 endpoint up" in exc_info.value.hint


class TestArtifactA2LUnavailable:
    """Artifact commands identify Gateway-side A2L outages."""

    def test_artifact_preflight_treats_503_as_a2l_unavailable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace

        import r4.client as client_module
        from r4._cli.artifact import cmd_artifact
        from r4.errors import APIError

        class FakeHTTP:
            def get(self, *_args, **_kwargs):
                raise APIError("API error: 503", status_code=503)

        class FakeClient:
            http = FakeHTTP()

            class config:
                gateway_url = "http://gateway.test"
                project_id = "test_project"

        monkeypatch.setattr(client_module, "Client", FakeClient)

        args = Namespace(
            artifact_command="list",
            offset=0,
            limit=50,
            type=None,
            status=None,
            project="test_project",
            user=None,
            as_json=False,
        )
        with pytest.raises(SystemExit) as exc_info:
            cmd_artifact(args)

        assert exc_info.value.code == 1
        stderr = capsys.readouterr().err
        assert "A2L artifact service is not available" in stderr
        assert "r4 endpoint up" not in stderr


# =============================================================================
# F-020/F-024: Top-level CLI hint rendering
# =============================================================================


class TestCLIHintRendering:
    """The top-level CLI error handler prints hints when available."""

    def test_r4_error_hint_property_default_none(self):
        """Base R4Error.hint is None when no hint detail is present."""
        from r4.errors import R4Error

        error = R4Error("generic error")

        assert error.hint is None

    def test_r4_error_hint_property_from_details(self):
        """R4Error.hint reads from details dict."""
        from r4.errors import R4Error

        error = R4Error("some error", {"hint": "try this"})

        assert error.hint == "try this"
