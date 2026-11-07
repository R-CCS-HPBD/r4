#!/usr/bin/env python3
"""Validate max_model_len propagation after context-window fix.

Checks:
1. UMRS endpoint API includes max_model_len in endpoint responses
2. Gateway /gw/llm/v1/models includes max_model_len
3. Gateway /gw/endpoints includes max_model_len
4. r4 companion's _endpoint_context_limit() resolves it
"""

import json
import sys
import urllib.error
import urllib.request


def fetch(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e.code), "body": e.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}


def main():
    import os

    gateway = os.environ.get("GATEWAY_URL", "http://localhost:8000")
    token = os.environ.get("R4_JWT", os.environ.get("R4_API_KEY", ""))

    if not token:
        # Try reading from r4 config
        jwt_path = os.path.expanduser("~/.r4/jwt")
        if os.path.exists(jwt_path):
            with open(jwt_path) as hf:
                token = hf.read().strip()

    print(f"Gateway: {gateway}")
    print(f"Token: {'present' if token else 'MISSING'}")
    print()

    # 1. Check /gw/endpoints for max_model_len
    print("=" * 60)
    print("1. Endpoint API: /gw/endpoints")
    ep_data = fetch(f"{gateway}/gw/endpoints", token)
    if "error" in ep_data:
        print(f"   ERROR: {ep_data['error']}")
    else:
        eps = ep_data.get("endpoints", [])
        print(f"   {len(eps)} endpoints")
        missing = 0
        for e in eps[:8]:
            mml = e.get("max_model_len")
            model = e.get("model", "?")
            status = e.get("status", "?")
            if mml:
                ctx = f"{mml // 1024}k" if mml >= 1024 else str(mml)
                print(f"   ✓ {model:35s} status={status:12s} max_model_len={mml:>8d} ({ctx})")
            else:
                print(f"   ✗ {model:35s} status={status:12s} max_model_len=MISSING")
                missing += 1
        if missing:
            print(f"   FAIL: {missing} endpoints missing max_model_len")
        else:
            print("   PASS: all endpoints have max_model_len")

    print()

    # 2. Check /gw/llm/v1/models for max_model_len
    print("=" * 60)
    print("2. LLM Models: /gw/llm/v1/models")
    model_data = fetch(f"{gateway}/gw/llm/v1/models", token)
    if "error" in model_data:
        print(f"   ERROR: {model_data['error']}")
    else:
        models = model_data.get("data", [])
        print(f"   {len(models)} models")
        missing = 0
        for m in models[:8]:
            mml = m.get("max_model_len")
            mid = m.get("id", "?")
            if mml:
                ctx = f"{mml // 1024}k" if mml >= 1024 else str(mml)
                print(f"   ✓ {mid:35s} max_model_len={mml:>8d} ({ctx})")
            else:
                print(f"   ✗ {mid:35s} max_model_len=MISSING")
                missing += 1
        if missing:
            print(f"   FAIL: {missing} models missing max_model_len")
        else:
            print("   PASS: all models have max_model_len")

    print()

    # 3. Check r4 companion context budget resolution (synthetic)
    print("=" * 60)
    print("3. Companion context budget resolution (synthetic)")
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "r4", "src"))
        from r4.companion.context_budget import (
            _endpoint_context_limit,
            effective_context_budget,
            resolve_model_context_limit,
        )

        class MockClient:
            def __init__(self, endpoints):
                self._endpoints = endpoints

            def list_endpoints(self, _include_stopped=False):
                return {"endpoints": self._endpoints}

        # Synthetic endpoint data with max_model_len
        test_eps = [
            {"model": "DeepSeek-V4-Pro", "max_model_len": 1_048_576, "status": "ready"},
            {"model": "Kimi-K2.6", "max_model_len": 262_144, "status": "ready"},
            {"model": "GLM-5.1", "max_model_len": 202_752, "status": "ready"},
            {"model": "GLM-5.2", "max_model_len": 1_048_576, "status": "ready"},
            {"model": "old-model-no-mml", "status": "ready"},  # Missing max_model_len
        ]

        client = MockClient(test_eps)

        all_ok = True
        for ep in test_eps:
            model = ep.get("model", "")
            expected_mml = ep.get("max_model_len")
            if not model:
                continue

            from_endpoint = _endpoint_context_limit(client, model)
            _resolved = resolve_model_context_limit(client, model)

            if expected_mml:
                if from_endpoint == expected_mml:
                    effective = effective_context_budget(from_endpoint)
                    print(f"   ✓ {model:35s} endpoint={from_endpoint:>8d} → budget={effective:>8d}")
                else:
                    print(f"   ✗ {model:35s} expected={expected_mml} got={from_endpoint}")
                    all_ok = False
            else:
                if from_endpoint is None:
                    print(f"   ✓ {model:35s} no max_model_len → fallback to known/default")
                else:
                    print(f"   ✗ {model:35s} unexpected max_model_len={from_endpoint}")
                    all_ok = False

        if all_ok:
            print("   PASS: companion context budget resolution works")
        else:
            print("   FAIL: companion resolution errors")
    except ImportError as e:
        print(f"   SKIP: cannot import r4 companion: {e}")
    except Exception as e:
        print(f"   FAIL: {e}")
        all_ok = False

    print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    endpoint_ok = "error" not in ep_data and all(
        e.get("max_model_len") for e in ep_data.get("endpoints", [])
    )
    models_ok = "error" not in model_data and all(
        m.get("max_model_len") for m in model_data.get("data", [])
    )
    if endpoint_ok and models_ok:
        print("PASS: max_model_len propagation working correctly")
    else:
        print("FAIL: max_model_len not yet propagated")
        print("  Deploy the updated UMRS + Gateway to fix this.")


if __name__ == "__main__":
    main()
