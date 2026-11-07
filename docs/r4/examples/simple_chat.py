#!/usr/bin/env python3
"""Simple chat example.

This script demonstrates basic chat functionality with the r4 SDK.

Usage:
    python simple_chat.py

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

from r4 import Client


def main():
    # Initialize client
    # Credentials are loaded from environment variables
    client = Client()

    # Check health
    print("Checking service health...")
    if not client.health():
        print("Service is not available")
        return

    print("Service is healthy!\n")

    # Simple chat
    print("Sending message...")
    response = client.chat("What is Python? Answer in one sentence.")

    print(f"Response: {response.content}")
    print(f"\nModel: {response.model}")
    print(f"Tokens used: {response.usage.total_tokens}")


if __name__ == "__main__":
    main()
