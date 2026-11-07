"""
Unit tests for r4 Errors module.

Tests:
- Exception hierarchy
- Error attributes
- Message and details handling
"""

import pytest

# =============================================================================
# Base R4Error Tests
# =============================================================================


class TestR4Error:
    """Tests for R4Error base class."""

    def test_basic_creation(self):
        """Test creating basic R4Error."""
        from r4.errors import R4Error

        error = R4Error("Something went wrong")

        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.details == {}

    def test_with_details(self):
        """Test R4Error with details."""
        from r4.errors import R4Error

        error = R4Error("Error occurred", {"context": "test", "code": 42})

        assert error.message == "Error occurred"
        assert error.details["context"] == "test"
        assert error.details["code"] == 42

    def test_can_be_raised(self):
        """Test R4Error can be raised and caught."""
        from r4.errors import R4Error

        with pytest.raises(R4Error) as exc_info:
            raise R4Error("Test error")

        assert "Test error" in str(exc_info.value)


# =============================================================================
# AuthenticationError Tests
# =============================================================================


class TestAuthenticationError:
    """Tests for AuthenticationError."""

    def test_default_message(self):
        """Test default authentication error message."""
        from r4.errors import AuthenticationError

        error = AuthenticationError()

        assert error.message == "Authentication failed"

    def test_custom_message(self):
        """Test custom authentication error message."""
        from r4.errors import AuthenticationError

        error = AuthenticationError("Invalid API key")

        assert error.message == "Invalid API key"

    def test_is_r4_error(self):
        """Test AuthenticationError inherits from R4Error."""
        from r4.errors import AuthenticationError, R4Error

        error = AuthenticationError()

        assert isinstance(error, R4Error)


# =============================================================================
# RateLimitError Tests
# =============================================================================


class TestRateLimitError:
    """Tests for RateLimitError."""

    def test_default_message(self):
        """Test default rate limit error message."""
        from r4.errors import RateLimitError

        error = RateLimitError()

        assert error.message == "Rate limit exceeded"
        assert error.retry_after is None

    def test_with_retry_after(self):
        """Test rate limit error with retry_after."""
        from r4.errors import RateLimitError

        error = RateLimitError(retry_after=60)

        assert error.retry_after == 60
        assert error.details["retry_after"] == 60

    def test_custom_message_and_retry(self):
        """Test custom message with retry."""
        from r4.errors import RateLimitError

        error = RateLimitError("Too many requests", retry_after=30)

        assert error.message == "Too many requests"
        assert error.retry_after == 30


# =============================================================================
# APIError Tests
# =============================================================================


class TestAPIError:
    """Tests for APIError."""

    def test_basic_creation(self):
        """Test creating basic APIError."""
        from r4.errors import APIError

        error = APIError("Request failed")

        assert error.message == "Request failed"
        assert error.status_code is None
        assert error.response_body is None

    def test_with_status_code(self):
        """Test APIError with status code."""
        from r4.errors import APIError

        error = APIError("Server error", status_code=500)

        assert error.status_code == 500
        assert error.details["status_code"] == 500

    def test_with_response_body(self):
        """Test APIError with response body."""
        from r4.errors import APIError

        error = APIError(
            "Bad request",
            status_code=400,
            response_body='{"error": "invalid format"}',
        )

        assert error.status_code == 400
        assert error.response_body == '{"error": "invalid format"}'

    def test_is_r4_error(self):
        """Test APIError inherits from R4Error."""
        from r4.errors import APIError, R4Error

        error = APIError("Test")

        assert isinstance(error, R4Error)


# =============================================================================
# R4ConnectionError Tests
# =============================================================================


class TestR4ConnectionError:
    """Tests for R4ConnectionError."""

    def test_default_message(self):
        """Test default connection error message."""
        from r4.errors import R4ConnectionError

        error = R4ConnectionError()

        assert error.message == "Failed to connect to API"

    def test_custom_message(self):
        """Test custom connection error message."""
        from r4.errors import R4ConnectionError

        error = R4ConnectionError("Network unreachable")

        assert error.message == "Network unreachable"


# =============================================================================
# R4TimeoutError Tests
# =============================================================================


class TestR4TimeoutError:
    """Tests for R4TimeoutError."""

    def test_default_message(self):
        """Test default timeout error message."""
        from r4.errors import R4TimeoutError

        error = R4TimeoutError()

        assert error.message == "Request timed out"

    def test_custom_message(self):
        """Test custom timeout error message."""
        from r4.errors import R4TimeoutError

        error = R4TimeoutError("Model inference timed out after 5m")

        assert error.message == "Model inference timed out after 5m"


# =============================================================================
# ValidationError Tests
# =============================================================================


class TestValidationError:
    """Tests for ValidationError."""

    def test_basic_creation(self):
        """Test creating basic ValidationError."""
        from r4.errors import ValidationError

        error = ValidationError("Invalid input")

        assert error.message == "Invalid input"
        assert error.field is None

    def test_with_field(self):
        """Test ValidationError with field name."""
        from r4.errors import ValidationError

        error = ValidationError("Value must be positive", field="temperature")

        assert error.field == "temperature"
        assert error.details["field"] == "temperature"

    def test_is_r4_error(self):
        """Test ValidationError inherits from R4Error."""
        from r4.errors import R4Error, ValidationError

        error = ValidationError("Test")

        assert isinstance(error, R4Error)


# =============================================================================
# Exception Hierarchy Tests
# =============================================================================


class TestExceptionHierarchy:
    """Tests for exception hierarchy."""

    def test_all_errors_inherit_from_r4_error(self):
        """Test all custom errors inherit from R4Error."""
        from r4.errors import (
            APIError,
            AuthenticationError,
            R4ConnectionError,
            R4Error,
            R4TimeoutError,
            RateLimitError,
            ValidationError,
        )

        errors = [
            AuthenticationError(),
            RateLimitError(),
            APIError("test"),
            R4ConnectionError(),
            R4TimeoutError(),
            ValidationError("test"),
        ]

        for error in errors:
            assert isinstance(error, R4Error)
            assert isinstance(error, Exception)

    def test_catch_all_with_r4_error(self):
        """Test catching all r4 errors with base class."""
        from r4.errors import AuthenticationError, R4Error, RateLimitError

        # Should be able to catch any r4 error with base class
        try:
            raise AuthenticationError()
        except R4Error:
            pass  # Expected

        try:
            raise RateLimitError()
        except R4Error:
            pass  # Expected
