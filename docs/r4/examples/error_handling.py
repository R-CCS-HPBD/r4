#!/usr/bin/env python3
"""Error handling example.

This script demonstrates robust error handling patterns
for production applications.

Usage:
    python error_handling.py

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

import time
from functools import wraps
from typing import TypeVar, Callable

from r4 import (
    Client,
    R4Error,
    AuthenticationError,
    RateLimitError,
    APIError,
    R4TimeoutError,
)

T = TypeVar("T")


def with_retry(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    max_wait: float = 60.0,
) -> Callable:
    """Decorator for automatic retry with exponential backoff."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)

                except RateLimitError as e:
                    # Use server-provided retry-after if available
                    wait = min(e.retry_after or (backoff_factor**attempt), max_wait)
                    print(f"Rate limited, waiting {wait:.1f}s...")
                    time.sleep(wait)
                    last_error = e

                except R4TimeoutError as e:
                    # Exponential backoff
                    wait = min(backoff_factor**attempt, max_wait)
                    print(f"Timeout, retrying in {wait:.1f}s...")
                    time.sleep(wait)
                    last_error = e

                except APIError as e:
                    # Only retry on server errors (5xx)
                    if e.status_code >= 500:
                        wait = min(backoff_factor**attempt, max_wait)
                        print(f"Server error {e.status_code}, retrying...")
                        time.sleep(wait)
                        last_error = e
                    else:
                        # Client errors should not be retried
                        raise

                except AuthenticationError:
                    # Auth errors should not be retried
                    raise

            # All retries exhausted
            raise last_error

        return wrapper

    return decorator


class RobustClient:
    """Wrapper around Client with robust error handling."""

    def __init__(self):
        self._client = None
        self._last_health_check = 0
        self._health_check_interval = 30  # seconds

    @property
    def client(self) -> Client:
        """Get or create client instance."""
        if self._client is None:
            self._client = Client()
        return self._client

    def is_healthy(self, force_check: bool = False) -> bool:
        """Check service health with caching."""
        now = time.time()

        if not force_check and (now - self._last_health_check) < self._health_check_interval:
            return True  # Assume healthy if checked recently

        try:
            healthy = self.client.health()
            self._last_health_check = now
            return healthy
        except Exception:
            return False

    def wait_for_service(self, timeout: float = 300, interval: float = 10) -> bool:
        """Wait for service to become available."""
        start = time.time()

        while (time.time() - start) < timeout:
            if self.is_healthy(force_check=True):
                return True
            print(f"Service not available, checking again in {interval}s...")
            time.sleep(interval)

        return False

    @with_retry(max_retries=3)
    def chat(self, message: str, **kwargs) -> str:
        """Chat with automatic retry."""
        response = self.client.chat(message, **kwargs)
        return response.content

    def chat_with_fallback(
        self,
        message: str,
        fallback: str = "Service unavailable",
        **kwargs,
    ) -> str:
        """Chat with fallback on any error."""
        try:
            return self.chat(message, **kwargs)
        except R4Error as e:
            print(f"Using fallback due to: {e}")
            return fallback


def demonstrate_error_types():
    """Demonstrate handling different error types."""

    client = Client()

    # 1. Authentication Error
    print("1. AuthenticationError handling:")
    try:
        # This would fail if API key is invalid
        response = client.chat("Hello")
        print(f"   Success: {response.content[:50]}...")
    except AuthenticationError:
        print("   Caught AuthenticationError - check your API key")
    except R4Error:
        print("   Caught other error")

    # 2. Rate Limit Error
    print("\n2. RateLimitError handling:")
    try:
        response = client.chat("Hello")
        print(f"   Success: {response.content[:50]}...")
    except RateLimitError as e:
        print(f"   Caught RateLimitError - retry after {e.retry_after}s")
    except R4Error:
        print("   Caught other error")

    # 3. API Error
    print("\n3. APIError handling:")
    try:
        response = client.chat("Hello")
        print(f"   Success: {response.content[:50]}...")
    except APIError as e:
        print(f"   Caught APIError - status {e.status_code}: {e.message}")
    except R4Error:
        print("   Caught other error")

    # 4. Timeout Error
    print("\n4. TimeoutError handling:")
    try:
        # Very short timeout to potentially trigger error
        response = client.chat("Hello")
        print(f"   Success: {response.content[:50]}...")
    except R4TimeoutError:
        print("   Caught TimeoutError - request took too long")
    except R4Error:
        print("   Caught other error")


def demonstrate_robust_client():
    """Demonstrate the RobustClient wrapper."""

    print("RobustClient Demo")
    print("=" * 50)

    client = RobustClient()

    # Wait for service
    print("\n1. Checking service availability...")
    if not client.wait_for_service(timeout=30, interval=5):
        print("   Service not available after 30s")
        return
    print("   Service is available!")

    # Chat with retry
    print("\n2. Chat with automatic retry...")
    try:
        response = client.chat("What is 2+2?")
        print(f"   Response: {response}")
    except R4Error as e:
        print(f"   Failed after retries: {e}")

    # Chat with fallback
    print("\n3. Chat with fallback...")
    response = client.chat_with_fallback(
        "What is the meaning of life?",
        fallback="I don't know right now, please try again later.",
    )
    print(f"   Response: {response}")


def main():
    print("Error Handling Examples")
    print("=" * 50)

    # Show different error types
    demonstrate_error_types()

    print("\n")

    # Show robust client pattern
    demonstrate_robust_client()


if __name__ == "__main__":
    main()
