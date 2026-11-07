#!/usr/bin/env python3
"""Batch processing example.

This script demonstrates efficient batch processing of many items
with concurrent execution and progress tracking.

Usage:
    python batch_processing.py [--input FILE] [--output FILE] [--workers N]

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Any

from r4 import Client, R4Error


@dataclass
class ProcessingResult:
    """Result of processing a single item."""

    item_id: int
    input_data: Any
    output: str
    success: bool
    error: str = None


def process_item(client: Client, item_id: int, item: dict) -> ProcessingResult:
    """Process a single item."""
    try:
        # Build prompt from item
        prompt = item.get("prompt", f"Analyze this data: {json.dumps(item)}")

        # Call the API
        response = client.chat(
            prompt,
            temperature=0.3,
            max_tokens=500,
        )

        return ProcessingResult(
            item_id=item_id,
            input_data=item,
            output=response.content,
            success=True,
        )

    except R4Error as e:
        return ProcessingResult(
            item_id=item_id,
            input_data=item,
            output="",
            success=False,
            error=str(e),
        )


def batch_process(
    items: List[dict],
    max_workers: int = 4,
    progress: bool = True,
) -> List[ProcessingResult]:
    """Process items in parallel with progress tracking."""

    client = Client()

    # Check health first
    if not client.health():
        raise RuntimeError("Service is not available")

    results = []
    total = len(items)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(process_item, client, i, item): i for i, item in enumerate(items)
        }

        # Collect results as they complete
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

            completed += 1
            if progress:
                status = "✓" if result.success else "✗"
                print(
                    f"\r[{completed}/{total}] {status} Item {result.item_id}",
                    end="",
                    file=sys.stderr,
                )

    if progress:
        print(file=sys.stderr)  # Newline after progress

    # Sort by original order
    results.sort(key=lambda r: r.item_id)

    return results


def main():
    parser = argparse.ArgumentParser(description="Batch processing example")
    parser.add_argument("--input", "-i", default="-", help="Input JSON file (default: stdin)")
    parser.add_argument("--output", "-o", default="-", help="Output JSON file (default: stdout)")
    parser.add_argument(
        "--workers", "-w", type=int, default=4, help="Number of concurrent workers (default: 4)"
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress progress output")

    args = parser.parse_args()

    # Read input
    if args.input == "-":
        items = json.load(sys.stdin)
    else:
        with open(args.input) as f:
            items = json.load(f)

    if not isinstance(items, list):
        items = [items]

    print(f"Processing {len(items)} items with {args.workers} workers...", file=sys.stderr)

    # Process
    results = batch_process(
        items,
        max_workers=args.workers,
        progress=not args.quiet,
    )

    # Summarize
    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    print(f"\nDone: {successful} successful, {failed} failed", file=sys.stderr)

    # Convert results to JSON-serializable format
    output = [
        {
            "item_id": r.item_id,
            "input": r.input_data,
            "output": r.output,
            "success": r.success,
            "error": r.error,
        }
        for r in results
    ]

    # Write output
    if args.output == "-":
        json.dump(output, sys.stdout, indent=2)
        print()
    else:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Results written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
