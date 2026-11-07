#!/usr/bin/env python3
"""Multi-turn conversation example.

This script demonstrates maintaining conversation history
for multi-turn interactions.

Usage:
    python multi_turn_conversation.py

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

from r4 import Client, ChatMessage


def main():
    client = Client()

    # Check health
    if not client.health():
        print("Service is not available")
        return

    # Initialize conversation with system prompt
    messages = [
        ChatMessage.system(
            "You are a helpful Python programming tutor. Keep explanations concise but informative."
        )
    ]

    # Define conversation turns
    questions = [
        "What is a list comprehension?",
        "Can you give me an example?",
        "How is that different from a for loop?",
    ]

    print("Multi-turn Conversation Demo")
    print("=" * 50)

    for question in questions:
        print(f"\nUser: {question}")

        # Add user message
        messages.append(ChatMessage.user(question))

        # Get response
        response = client.chat_completion(
            messages,
            temperature=0.3,
        )

        print(f"\nAssistant: {response.content}")

        # Add assistant response to history
        messages.append(ChatMessage.assistant(response.content))

        print("-" * 50)

    # Show conversation stats
    print(f"\nConversation had {len(messages)} messages")
    print(f"Total tokens used: {response.usage.total_tokens}")


if __name__ == "__main__":
    main()
