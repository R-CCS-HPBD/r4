from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.e2e
def test_r4_bootstrap_demo_requests_mounted_endpoint(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "r4-commands.jsonl"
    state_dir = tmp_path / "state"

    _write_executable(
        fake_bin / "r4",
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log_path = Path(os.environ["R4_FAKE_LOG"])
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

if args[:2] == ["config", "show"]:
    print("Project: demo-project")
    print("Model: demo-model")
elif args[:2] == ["auth", "status"]:
    print("authenticated")
elif args[:2] == ["project", "switch"]:
    pass
elif args[:2] == ["project", "create"]:
    pass
elif args[:2] == ["config", "context"]:
    pass
elif args[:2] == ["model", "list"]:
    if "--json" in args:
        print(json.dumps([{"name": "demo-model", "type": "llm", "status": "available"}]))
    else:
        print("demo-model  llm  available")
elif args[:2] == ["endpoint", "list"]:
    print(json.dumps({"endpoints": []}))
elif args[:2] == ["endpoint", "status"]:
    if "--json" in args:
        print(json.dumps({"status": "ready", "instance_id": args[2]}))
    else:
        print(f"Endpoint {args[2]}: ready")
elif args[:2] == ["endpoint", "up"]:
    print(json.dumps({"instance_id": "ep-demo", "status": "ready"}))
elif args[:1] == ["health"]:
    print("healthy")
else:
    print(f"unhandled fake r4 command: {args}", file=sys.stderr)
    sys.exit(2)
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "R4_FAKE_LOG": str(command_log),
            "R4_DEMO_STATE_DIR": str(state_dir),
        }
    )

    result = subprocess.run(
        ["bash", "demos/00_onboarding/run_demo.sh", "--auto"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    commands = [json.loads(line) for line in command_log.read_text(encoding="utf-8").splitlines()]
    endpoint_up = next(command for command in commands if command[:2] == ["endpoint", "up"])
    assert endpoint_up[:4] == ["endpoint", "up", "--model", "demo-model"]
    assert "--tier" in endpoint_up
    assert endpoint_up[endpoint_up.index("--tier") + 1] == "project"
    assert "--mount-mcp" in endpoint_up
    assert endpoint_up[endpoint_up.index("--mount-mcp") + 1].endswith(":/mcp-servers/demo-tools")
    assert "--mount-data" in endpoint_up
    assert endpoint_up[endpoint_up.index("--mount-data") + 1].endswith(":/project")
    assert "--mount-rag" in endpoint_up
    assert endpoint_up[endpoint_up.index("--mount-rag") + 1].endswith(":/rag-index")
    assert "--egress-domains" in endpoint_up
    assert endpoint_up[endpoint_up.index("--egress-domains") + 1] == "api.example.com"

    mcp_source = state_dir / "mounts" / "mcp" / "demo-tools"
    validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "r4_mcp.runners.custom_mcp",
            "--config-dir",
            str(mcp_source),
            "--validate-only",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validate.returncode == 0, validate.stderr + validate.stdout


@pytest.mark.e2e
def test_r4_demo_model_resolution_handles_gateway_failure(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"

    _write_executable(
        fake_bin / "r4",
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

args = sys.argv[1:]

if args[:2] == ["config", "show"]:
    print("Project: demo-project")
    print("Model: demo-model")
elif args[:2] == ["project", "switch"]:
    pass
elif args[:2] == ["config", "context"]:
    pass
elif args[:2] == ["endpoint", "list"]:
    print(json.dumps({"endpoints": []}))
elif args[:2] == ["model", "list"] and "--json" in args:
    print("Error (model): Gateway unreachable", file=sys.stderr)
    sys.exit(1)
else:
    print(f"unhandled fake r4 command: {args}", file=sys.stderr)
    sys.exit(2)
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "R4_DEMO_STATE_DIR": str(state_dir),
        }
    )

    result = subprocess.run(
        ["bash", "demos/01_chat/run_demo.sh", "--auto"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "Unable to resolve a demo model" in combined
    assert "JSONDecodeError" not in combined


@pytest.mark.e2e
def test_umrs_endpoint_demo_prints_mount_payload(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "umrs",
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

args = sys.argv[1:]
if "profile" in args and "show" in args:
    print(json.dumps({"connection": {"server_host": "127.0.0.1", "control_port": 5555}}))
elif "config" in args and "use" in args:
    pass
elif args[-1:] == ["ping"]:
    pass
else:
    print("fake umrs")
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env python3
from __future__ import annotations

import json

print(json.dumps({"endpoints": []}))
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "MINI_RIVAULT_MOUNT_DEMO_ROOT": str(tmp_path / "mounts"),
            "DEMO25_PROVISION": "0",
        }
    )

    result = subprocess.run(
        ["bash", "umrs/demos/25_endpoint_provisioning/run_demo.sh"],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    output = result.stdout
    assert '"mounts"' in output
    assert '"target": "/mcp-servers/demo-tools"' in output
    assert '"target": "/project"' in output
    assert '"target": "/rag-index"' in output
    assert '"egress_domains"' in output
    assert "RIVAULT_EXTRA_PROFILES=custom-mcp egress rag" in output
    assert "Set DEMO25_PROVISION=1" in output
