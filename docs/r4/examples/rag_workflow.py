#!/usr/bin/env python3
"""RAG workflow example.

This script demonstrates:
1. Uploading documents for indexing
2. Retrieving passages with the retrieve verb (no model call)
3. Answering with your own model from those passages (the context pack)
4. Asking a question through the server's answer path (/rag/query)
5. Listing indexed documents

Usage:
    python rag_workflow.py [--project-id PROJECT] [--upload FILE] [--list]
                           [--query TEXT] [--answer]

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)

Updated 2026-10-10 (ADR-093): ``retrieve()`` returns the passages UMRS
judged relevant and a sufficiency status; ``render_pack`` turns them into
the prompt layout every R4 surface uses.
"""

import argparse

from r4 import Client
from r4_contracts.context_pack import render_pack


def upload_document(client: Client, file_path: str, project_id: str) -> None:
    """Upload a document for RAG indexing."""
    print(f"Uploading {file_path}...")

    result = client.rag.upload(
        file_path,
        project_id=project_id,
        metadata={
            "source": "example-upload",
        },
    )

    if result.status == "failed":
        print(f"✗ Upload failed: {result.error}")
    else:
        print(f"✓ Upload {result.status}")
        print(f"  Document ID: {result.document_id}")
        print(f"  Chunks created: {result.chunks}")


def retrieve_passages(client: Client, query: str, project_id: str) -> None:
    """Retrieve passages, then answer with your own model from the pack."""
    print(f"\nRetrieving: {query}")
    print("-" * 40)

    result = client.rag.retrieve(query, project_id=project_id)
    status = result.sufficiency.status
    print(f"Sufficiency: {status} ({result.sufficiency.basis or 'no basis'})")

    for passage in result.passages:
        preview = passage.text[:100].replace("\n", " ")
        print(f"  {passage.rank}. {passage.source_name} [{passage.source_id}]")
        print(f"     {preview}...")

    # "insufficient" means nothing relevant was found: skip the model call.
    if not result.passages or status == "insufficient":
        print("\nNo relevant passages; not asking the model.")
        return

    pack_status = "sufficient" if status == "sufficient" else "partial"
    prompt, labels = render_pack(query, result.passages, status=pack_status)
    reply = client.chat(prompt)
    print(f"\nAnswer: {reply.content}")
    print(f"Citations map: {labels}")


def query_documents(client: Client, query: str, project_id: str) -> None:
    """Ask through the server's answer path (/rag/query)."""
    print(f"\nQuerying: {query}")
    print("-" * 40)

    # No threshold: the server's relevance cut decides which sources are used.
    response = client.rag.query(
        query,
        project_id=project_id,
        top_k=5,
    )

    print(f"\nAnswer: {response.answer}")

    if response.sources:
        print(f"\nSources ({len(response.sources)}):")
        for i, src in enumerate(response.sources, 1):
            print(f"  {i}. Score: {src.score:.3f}")
            print(f"     ID: {src.document_id}")
            # Show first 100 chars of content
            preview = src.content[:100].replace("\n", " ")
            print(f"     Preview: {preview}...")


def list_documents(client: Client, project_id: str) -> None:
    """List all documents in the project."""
    print(f"\nDocuments in project '{project_id}':")
    print("-" * 40)

    docs = client.rag.list_documents(project_id=project_id)

    if not docs:
        print("No documents found")
        return

    # Each entry is a dict as the server returns it.
    for doc in docs:
        print(f"  - {doc['document_id']} ({doc.get('filename', '')})")
        if doc.get("metadata"):
            print(f"    Metadata: {doc['metadata']}")


def main():
    parser = argparse.ArgumentParser(description="RAG workflow example")
    parser.add_argument("--project-id", default="rag-demo", help="Project ID for RAG operations")
    parser.add_argument("--upload", help="File to upload for indexing")
    parser.add_argument(
        "--query", default="What is this document about?", help="Query to run against the documents"
    )
    parser.add_argument("--list", action="store_true", help="List all documents")
    parser.add_argument(
        "--answer",
        action="store_true",
        help="Use the server's answer path instead of retrieve + your own model",
    )

    args = parser.parse_args()

    client = Client()

    # Check health
    if not client.health():
        print("Service is not available")
        return

    print(f"Project: {args.project_id}")
    print("=" * 50)

    if args.upload:
        upload_document(client, args.upload, args.project_id)

    if args.list:
        list_documents(client, args.project_id)

    if not args.upload and not args.list:
        if args.answer:
            query_documents(client, args.query, args.project_id)
        else:
            retrieve_passages(client, args.query, args.project_id)


if __name__ == "__main__":
    main()
