#!/usr/bin/env python3
"""
Quick test for the mock Slurm environment.
This test can run without the full Docker Compose stack.
"""

import json
import os
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def test_mock_litellm_direct():
    """Test mock LiteLLM server directly (if running)."""
    import pytest

    print("\n=== Testing Mock LiteLLM Server ===\n")

    base_url = os.getenv("LITELLM_URL", "http://localhost:11434")
    api_key = os.getenv("LITELLM_MASTER_KEY", "sk-test-e2e")

    # Health check
    print(f"1. Health check: {base_url}/health")
    try:
        with urlopen(f"{base_url}/health", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"   ✓ Response: {data}")
    except URLError as e:
        print(f"   ✗ Failed: {e}")
        pytest.skip(f"Mock LiteLLM not running: {e}")

    # List models
    print(f"\n2. List models: {base_url}/v1/models")
    try:
        req = Request(f"{base_url}/v1/models")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"   ✓ Models: {[m['id'] for m in data.get('data', [])]}")
    except URLError as e:
        pytest.fail(f"List models failed: {e}")

    # Chat completion
    print(f"\n3. Chat completion: {base_url}/v1/chat/completions")
    try:
        req = Request(
            f"{base_url}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "rivault-gpt-oss-static",
                    "messages": [{"role": "user", "content": "Hello, this is a test!"}],
                    "stream": False,
                }
            ).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"   ✓ Response: {content[:100]}...")
            assert content, "Expected non-empty response content"
    except URLError as e:
        pytest.fail(f"Chat completion failed: {e}")

    print("\n✅ All mock LiteLLM tests passed!")


def test_mock_slurm_scripts():
    """Test mock Slurm scripts (without Docker)."""
    print("\n=== Testing Mock Slurm Scripts ===\n")

    script_dir = Path(__file__).parent / "docker" / "mock_slurm"

    assert script_dir.exists(), f"Script directory not found: {script_dir}"

    scripts = ["mock_sinfo", "mock_sbatch", "mock_squeue", "mock_scancel", "mock_sacct"]
    missing = []

    for script in scripts:
        script_path = script_dir / script
        if script_path.exists():
            print(f"   ✓ {script} exists")
        else:
            print(f"   ✗ {script} NOT found")
            missing.append(script)

    assert not missing, f"Missing mock scripts: {missing}"
    print("\n✅ All mock Slurm scripts present!")


def main():
    print("=" * 60)
    print("E2E Mock Environment Quick Test")
    print("=" * 60)

    # Test 1: Check mock scripts exist
    test_mock_slurm_scripts()

    # Test 2: Test mock LiteLLM if running
    print("\n" + "-" * 60)
    print("To test the full environment, run:")
    print("  make e2e-start")
    print("Then run this script again with:")
    print("  python tests/test_mock_env.py --full")
    print("-" * 60)

    if "--full" in sys.argv and not test_mock_litellm_direct():
        print("\n⚠️  Mock LiteLLM test failed. Is the environment running?")
        print("   Run: make e2e-start")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
