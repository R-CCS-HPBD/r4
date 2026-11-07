#!/usr/bin/env python3
"""Streaming chat example.

This script demonstrates streaming responses from the r4 SDK.

Usage:
    python streaming_chat.py

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

from r4 import Client


def main():
    client = Client()

    # Check health
    if not client.health():
        print("Service is not available")
        return

    print("Streaming response:\n")
    print("-" * 40)

    # Stream the response
    for chunk in client.chat(
        "Write a short poem about programming.",
        stream=True,
        temperature=0.8,
    ):
        print(chunk, end="", flush=True)

    print("\n" + "-" * 40)
    print("\nDone!")


if __name__ == "__main__":
    main()
