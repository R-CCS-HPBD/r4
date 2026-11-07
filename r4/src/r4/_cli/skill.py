"""r4 skill CLI — discover, install, search, and manage skill bundles.

Usage:
    r4 skill list                       List locally loaded skills
    r4 skill search <query>             Search UMRS registry for skills
    r4 skill install <name>             Install a skill from the registry
    r4 skill uninstall <name>           Remove a locally installed skill
    r4 skill info <name>                Show details of a skill
    r4 skill test <name>                Run a skill's tests (tests/run.sh; exit 0 = green)
    r4 skill pending                    List loop-proposed skills awaiting review (D-27)
    r4 skill approve <id|name>           Merge a proposal — only when its suite is green
    r4 skill reject <id|name>           Reject a proposal (stays disabled)
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any

from r4._cli.base import CommandHandler
from r4._cli.registry import command
from r4.skills import SkillLoader

logger = logging.getLogger(__name__)

_SKILL_DIR = Path.home() / ".config" / "r4" / "skills"


class _SuiteTimeout(Exception):
    """The skill's test suite exceeded the runner timeout."""


def _run_suite(run_sh: str, timeout: int) -> tuple[int, str, str]:
    """Run a skill's suite entry; returns (exit_code, stdout, stderr).

    Raises ``_SuiteTimeout`` when the suite exceeds *timeout* seconds —
    the D-27 runner contract shared by `r4 skill test` and the
    merge-on-green review flow.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["bash", run_sh],
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise _SuiteTimeout(str(exc)) from exc
    return result.returncode, result.stdout, result.stderr


def _skill_source_files(source: Path) -> tuple[Path, Path]:
    return source / "SKILL.md", source / "config.json"


def _is_skill_source(skill_md: Path, config_json: Path) -> bool:
    return skill_md.exists() or config_json.exists()


def _skill_name_from_config(source: Path, config_json: Path) -> str:
    if not config_json.exists():
        return source.name
    try:
        cfg = json.loads(config_json.read_text())
    except (json.JSONDecodeError, OSError):
        return source.name
    return cfg.get("name", source.name)


def _scan_skill_source(skill_md: Path, config_json: Path):
    from r4_contracts.security import SkillScanner

    cfg_data = json.loads(config_json.read_text()) if config_json.exists() else {}
    md_text = skill_md.read_text() if skill_md.exists() else ""
    return SkillScanner().scan_skill(cfg_data, md_text)


def _print_scan_errors(name: str, result) -> bool:
    if not result.has_errors:
        return False
    print(f"Security scan failed for '{name}':")
    for finding in result.findings:
        print(f"  [{finding.severity.value}] {finding.category}: {finding.message}")
    return True


def _print_scan_warnings(name: str, result) -> None:
    if result.warning_count <= 0:
        return
    print(f"Security scan warnings for '{name}':")
    for finding in result.findings:
        if finding.severity.value == "warning":
            print(f"  {finding.category}: {finding.message}")


def _skill_list_payload(skills) -> list[dict[str, object]]:
    return [
        {
            "name": skill.name,
            "source": skill.source,
            "has_mcp": skill.has_mcp,
            "description": skill.description,
        }
        for skill in skills
    ]


def _print_no_skills(as_json: bool) -> None:
    if as_json:
        print("[]")
        return
    print("No skills found.")
    print(f"  Install skills to: {_SKILL_DIR}")


def _print_skill_table(skills) -> None:
    print(f"{'Name':<30} {'Source':<10} {'MCP':<5} Description")
    print("-" * 80)
    for skill in skills:
        mcp = "yes" if skill.has_mcp else ""
        desc = (skill.description[:35] + "…") if len(skill.description) > 35 else skill.description
        print(f"{skill.name:<30} {skill.source:<10} {mcp:<5} {desc}")


@command
class SkillCommand(CommandHandler):
    name = "skill"
    help = "Manage skill bundles"

    def register(self, subparsers: Any) -> ArgumentParser:
        parser = subparsers.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="skill_action")

        # list
        p_list = sub.add_parser("list", help="List locally loaded skills")
        p_list.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )

        # test (D-27: skills as tested code — run the skill's tests/run.sh)
        p_test = sub.add_parser("test", help="Run a skill's tests (tests/run.sh; exit 0 = green)")
        p_test.add_argument("name", help="Skill name")
        p_test.add_argument(
            "--timeout",
            type=int,
            default=120,
            help="Test timeout in seconds (default 120)",
        )

        # D-27 skill-edit plane: review the loop's pending proposals.
        p_pending = sub.add_parser(
            "pending", help="List loop-proposed skills awaiting review (D-27)"
        )
        p_pending.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )
        p_approve = sub.add_parser(
            "approve",
            help="Merge a pending proposal — only when its test suite is green (merge-on-green)",
        )
        p_approve.add_argument("target", help="Proposal skill id or name")
        p_approve.add_argument(
            "--skill-dir",
            default=None,
            help=(
                "Local skill directory whose tests gate the merge "
                "(default: the installed skill dir)"
            ),
        )
        p_approve.add_argument("--note", default="", help="Review note recorded with the merge")
        p_approve.add_argument(
            "--timeout",
            type=int,
            default=120,
            help="Test timeout in seconds (default 120)",
        )
        p_reject = sub.add_parser("reject", help="Reject a pending proposal (stays disabled)")
        p_reject.add_argument("target", help="Proposal skill id or name")
        p_reject.add_argument("--note", default="", help="Review note recorded with the rejection")

        # search
        search_p = sub.add_parser("search", help="Search for skills")
        search_p.add_argument("query", help="Search query")

        # install
        install_p = sub.add_parser("install", help="Install a skill")
        install_p.add_argument("name", help="Skill name or path to skill directory")

        # uninstall
        uninstall_p = sub.add_parser("uninstall", help="Remove an installed skill")
        uninstall_p.add_argument("name", help="Skill name")

        # info
        info_p = sub.add_parser("info", help="Show skill details")
        info_p.add_argument("name", help="Skill name")

        # scan — security scan a skill directory
        scan_p = sub.add_parser(
            "scan",
            help="Scan a skill directory for security issues",
        )
        scan_p.add_argument("path", help="Path to skill directory")
        scan_p.add_argument(
            "--json",
            "-j",
            action="store_true",
            dest="as_json",
            help="Output as JSON",
        )

        # update — check for and install skill updates from source
        update_p = sub.add_parser(
            "update",
            help="Update installed skills from their sources",
        )
        update_p.add_argument(
            "name",
            nargs="?",
            help="Skill name to update (default: all)",
        )
        update_p.add_argument(
            "--check",
            action="store_true",
            help="Check for updates without installing",
        )

        # github — install from any GitHub repository
        github_p = sub.add_parser(
            "github",
            help="Install skills from any GitHub repository",
        )
        github_sub = github_p.add_subparsers(dest="github_action")

        gh_install = github_sub.add_parser(
            "install",
            help="Install a skill from a GitHub repo",
        )
        gh_install.add_argument(
            "source",
            help="GitHub source (owner/repo or owner/repo/skills/name)",
        )

        gh_list = github_sub.add_parser(
            "list",
            help="List skills in a GitHub repo",
        )
        gh_list.add_argument("repo", help="GitHub repo (owner/repo)")

        # skills-sh — Skills.sh marketplace integration
        sh_p = sub.add_parser(
            "skills-sh",
            help="Browse and install from Skills.sh marketplace",
        )
        sh_sub = sh_p.add_subparsers(dest="skills_sh_action")

        sh_list = sh_sub.add_parser("list", help="List Skills.sh marketplace")
        sh_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        sh_search = sh_sub.add_parser("search", help="Search Skills.sh")
        sh_search.add_argument("query", help="Search query")

        sh_install = sh_sub.add_parser("install", help="Install from Skills.sh")
        sh_install.add_argument("name", help="Skill name")

        # nvidia — browse and install NVIDIA Agent Skills
        nvidia_p = sub.add_parser("nvidia", help="Browse/install NVIDIA Agent Skills")
        nvidia_sub = nvidia_p.add_subparsers(dest="nvidia_action")

        nv_list = nvidia_sub.add_parser("list", help="List NVIDIA skills catalog")
        nv_list.add_argument("--json", "-j", action="store_true", dest="as_json")

        nv_install = nvidia_sub.add_parser("install", help="Install a skill from NVIDIA catalog")
        nv_install.add_argument("name", help="NVIDIA skill name (e.g. cudaq-guide)")

        nv_search = nvidia_sub.add_parser("search", help="Search NVIDIA skills catalog")
        nv_search.add_argument("query", help="Search query")

        nv_info = nvidia_sub.add_parser("info", help="Show NVIDIA skill details")
        nv_info.add_argument("name", help="NVIDIA skill name")

        return parser

    _ACTIONS = {
        "list": "_list",
        "test": "_test",
        "pending": "_pending",
        "approve": "_approve",
        "reject": "_reject",
        "search": "_search",
        "install": "_install",
        "uninstall": "_uninstall",
        "info": "_info",
        "scan": "_scan",
        "update": "_update",
        "github": "_github",
        "skills-sh": "_skills_sh",
        "nvidia": "_nvidia",
    }

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "skill_action", None)
        method = self._ACTIONS.get(action or "")
        if method is None:
            print(
                "Usage: r4 skill {list|test|pending|approve|reject|search|"
                "install|uninstall|info|nvidia}",
                file=sys.stderr,
            )
            return 1
        return getattr(self, method)(args)

    def _list(self, args: Namespace = None) -> int:
        loader = SkillLoader()
        skills = loader.scan_local()
        as_json = bool(args and getattr(args, "as_json", False))
        if not skills:
            _print_no_skills(as_json)
            return 0

        if as_json:
            print(json.dumps(_skill_list_payload(skills), indent=2, default=str))
            return 0

        _print_skill_table(skills)
        return 0

    def _test(self, args: Namespace) -> int:
        """D-27: run a skill's tests — the merge-on-green gate's runner.

        Convention: ``<skill-dir>/tests/run.sh`` is the executable entry
        (the skill author picks the framework inside). Exit 0 = green;
        anything else fails with the runner's output. A skill without a
        test entry refuses by name (never a silent pass). Packaged
        builtin skills (``companion/builtin/<name>``) are reachable by
        name after the user-skill dirs miss.
        """
        loader = SkillLoader()
        loader.scan_local()
        skill = loader.get(args.name)
        if skill is None:
            skill = loader.load_builtin(args.name)
        if skill is None:
            print(f"✗ Skill not found: {args.name}", file=sys.stderr)
            return 1
        if not skill.has_tests:
            print(
                f"✗ Skill {args.name!r} has no tests (expected "
                f"{skill.tests_dir}/run.sh) — nothing to run, never a silent pass.",
                file=sys.stderr,
            )
            return 1
        try:
            return_code, stdout, stderr = _run_suite(f"{skill.tests_dir}/run.sh", args.timeout)
        except _SuiteTimeout:
            print(
                f"✗ Skill tests timed out after {args.timeout}s: {args.name}",
                file=sys.stderr,
            )
            return 1
        if stdout:
            print(stdout, end="")
        if return_code != 0:
            if stderr:
                print(stderr, end="", file=sys.stderr)
            print(
                f"✗ Skill tests FAILED (exit {return_code}): {args.name}",
                file=sys.stderr,
            )
            return 1
        print(f"✓ Skill tests green: {args.name}")
        return 0

    # D-27 skill-edit plane: pending / approve (merge-on-green) / reject.

    def _pending(self, args: Namespace) -> int:
        """List loop-proposed skills awaiting human review."""
        proposals = self._fetch_pending()
        if proposals is None:
            return 1
        if getattr(args, "as_json", False):
            print(json.dumps(proposals, indent=2))
            return 0
        if not proposals:
            print("No pending skill proposals.")
            return 0
        print("Pending skill proposals (loop-proposed, awaiting review):")
        for item in proposals:
            manifest = item.get("manifest", {})
            name = manifest.get("name", item.get("skill_id", "?"))
            state = item.get("review_state", {}) or {}
            proposed = state.get("proposed_at", "")
            print(
                f"  {item.get('skill_id')}  {name}  "
                f"[{item.get('scope', '')} / {item.get('project_id', '')}] "
                f"proposed {proposed}"
            )
        print(
            "\nReview: r4 skill approve <id|name> --skill-dir DIR (merge-on-green) "
            "· r4 skill reject <id|name>"
        )
        return 0

    def _approve(self, args: Namespace) -> int:
        """Merge a pending proposal — only when its suite is green (D-27)."""
        proposal = self._resolve_pending(args.target)
        if proposal is None:
            return 1
        skill_dir = self._review_skill_dir(args, proposal)
        if skill_dir is None:
            return 1
        receipt = _green_receipt(Path(skill_dir), args)
        if receipt is None:
            return 1
        merged = self._post_review(proposal["skill_id"], "approved", args.note, receipt)
        if merged is None:
            return 1
        print(
            f"✓ Merged: {merged.get('manifest', {}).get('name', proposal['skill_id'])} "
            f"(reviewer-stamped, suite green — enabled={merged.get('enabled')})"
        )
        return 0

    def _reject(self, args: Namespace) -> int:
        """Reject a pending proposal (stays disabled)."""
        proposal = self._resolve_pending(args.target)
        if proposal is None:
            return 1
        decided = self._post_review(proposal["skill_id"], "rejected", args.note, None)
        if decided is None:
            return 1
        print(
            f"✓ Rejected: {decided.get('manifest', {}).get('name', proposal['skill_id'])} "
            "(stays disabled)"
        )
        return 0

    def _fetch_pending(self) -> list[dict[str, Any]] | None:
        """Pending proposals from the registry; None = registry unreachable."""
        try:
            from r4.client import Client

            _client = Client()
            data = _client.get(f"{getattr(_client, '_api_prefix', '/gw')}/skills/pending")
        except Exception as exc:  # noqa: BLE001 - CLI shows remote failure
            print(f"✗ Registry unavailable: {exc}", file=sys.stderr)
            return None
        return data if isinstance(data, list) else []

    def _resolve_pending(self, target: str) -> dict[str, Any] | None:
        """Find a pending proposal by skill id or manifest name (refuses by name)."""
        proposals = self._fetch_pending()
        if proposals is None:
            return None
        for item in proposals:
            if item.get("skill_id") == target:
                return item
            if item.get("manifest", {}).get("name") == target:
                return item
        print(
            f"✗ No pending skill proposal matches {target!r} — `r4 skill pending` "
            "lists the open proposals.",
            file=sys.stderr,
        )
        return None

    def _review_skill_dir(self, args: Namespace, proposal: dict[str, Any]) -> str | None:
        """Local directory whose tests gate the merge (attested receipt)."""
        if getattr(args, "skill_dir", None):
            path = Path(args.skill_dir)
            if path.is_dir():
                return str(path)
            print(f"✗ --skill-dir is not a directory: {args.skill_dir}", file=sys.stderr)
            return None
        name = proposal.get("manifest", {}).get("name", "")
        loader = SkillLoader()
        loader.scan_local()
        skill = loader.get(name) if name else None
        if skill is not None and skill.tests_dir:
            return str(Path(skill.tests_dir).parent)
        print(
            f"✗ Merge-on-green needs a local skill directory to run the suite: "
            f"pass --skill-dir (the reviewed local copy of "
            f"{name or proposal['skill_id']!r}).",
            file=sys.stderr,
        )
        return None

    def _post_review(
        self,
        skill_id: str,
        decision: str,
        note: str,
        receipt: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """POST the review decision; None = the server refused (printed)."""
        body: dict[str, Any] = {"decision": decision, "note": note or ""}
        if receipt is not None:
            body["test_receipt"] = receipt
        try:
            from r4.client import Client

            _client = Client()
            return _client.post(
                f"{getattr(_client, '_api_prefix', '/gw')}/skills/{skill_id}/review", json=body
            )
        except Exception as exc:  # noqa: BLE001 - CLI shows the typed refusal
            print(f"✗ Review refused: {exc}", file=sys.stderr)
            return None

    def _search(self, args: Namespace) -> int:
        query = args.query
        # Search locally first
        loader = SkillLoader()
        loader.scan_local()
        local = loader.search(query)
        if local:
            print(f"Local matches for '{query}':")
            for s in local:
                print(f"  {s.name}: {s.description}")

        print(f"\nRemote matches for '{query}':")
        try:
            from r4.client import Client

            _client = Client()
            data = _client.get(
                f"{getattr(_client, '_api_prefix', '/gw')}/skills/search", params={"q": query}
            )
            remote = data if isinstance(data, list) else data.get("items", [])
        except Exception as exc:  # noqa: BLE001 - CLI should degrade gracefully
            print(f"  Registry unavailable: {exc}")
            return 0

        if not remote:
            print("  No remote matches.")
            return 0
        for item in remote:
            manifest = item.get("manifest", {})
            name = manifest.get("name", item.get("skill_id", "?"))
            desc = manifest.get("description", "")
            scope = item.get("scope", "")
            print(f"  {name} [{scope}]: {desc}")
        return 0

    def _install(self, args: Namespace) -> int:
        name = args.name
        source = Path(name)

        # Install from local directory
        if source.is_dir():
            return self._install_from_dir(source)

        # Install from NVIDIA catalog (nvidia/<skill-name>)
        if name.startswith("nvidia/"):
            skill_name = name[len("nvidia/") :]
            return self._install_from_nvidia(skill_name)

        print(f"Installing skill '{name}' from UMRS registry…")
        try:
            from r4.client import Client

            _client = Client()
            data = _client.get(f"{getattr(_client, '_api_prefix', '/gw')}/skills/{name}")
        except Exception as exc:  # noqa: BLE001 - CLI should show remote failure
            print(f"  Registry unavailable: {exc}")
            return 1
        return self._install_from_registry(data)

    def _install_from_registry(self, data: dict[str, Any]) -> int:
        """Install a skill bundle returned by the UMRS registry."""
        manifest = data.get("manifest", {})
        name = manifest.get("name") or data.get("skill_id")
        if not name:
            print("Registry response missing skill name")
            return 1
        target = _SKILL_DIR / name
        if target.exists():
            print(f"Skill '{name}' already installed at {target}")
            return 1

        skill_md = data.get("skill_md", "")
        from r4_contracts.security import SkillScanner

        result = SkillScanner().scan_skill(manifest, skill_md)
        if result.has_errors:
            print(f"Security scan failed for '{name}':")
            for finding in result.findings:
                print(f"  [{finding.severity.value}] {finding.category}: {finding.message}")
            return 1

        _SKILL_DIR.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        (target / "config.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if skill_md:
            (target / "SKILL.md").write_text(skill_md)
        if manifest.get("mcp_server"):
            (target / "mcp.json").write_text(json.dumps(manifest["mcp_server"], indent=2) + "\n")
        print(f"Installed skill '{name}' to {target}")
        return 0

    def _install_from_dir(self, source: Path) -> int:
        """Install a skill from a local directory."""
        skill_md, config_json = _skill_source_files(source)
        if not _is_skill_source(skill_md, config_json):
            print(f"Not a valid skill directory: {source}")
            print("  Requires SKILL.md or config.json")
            return 1

        name = _skill_name_from_config(source, config_json)
        result = _scan_skill_source(skill_md, config_json)
        if _print_scan_errors(name, result):
            return 1
        _print_scan_warnings(name, result)

        target = _SKILL_DIR / name
        _SKILL_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists():
            print(f"Skill '{name}' already installed. Remove first: r4 skill uninstall {name}")
            return 1

        shutil.copytree(source, target)
        print(f"Installed skill '{name}' to {target}")
        return 0

    def _uninstall(self, args: Namespace) -> int:
        name = args.name
        target = _SKILL_DIR / name
        # A plain directory name only: "" removed every installed skill and
        # ".." the whole r4 config dir (shutil.rmtree below).
        if not name or Path(name).name != name or name in (".", ".."):
            print(f"✗ Not a skill name: {name!r}", file=sys.stderr)
            return 1
        if not target.exists():
            print(f"Skill '{name}' not found at {target}")
            return 1
        shutil.rmtree(target)
        print(f"Removed skill '{name}'")
        return 0

    def _scan(self, args: Namespace) -> int:
        """Scan a skill directory using SkillSpector (or fallback scanner)."""
        from r4.skill_scanner import scan_skill_directory_cli

        skill_path = Path(args.path)
        if not skill_path.exists():
            print(f"Path not found: {skill_path}", file=sys.stderr)
            return 2
        return scan_skill_directory_cli(
            skill_path,
            json_output=bool(getattr(args, "as_json", False)),
        )

    # -- Auto-update ---------------------------------------------------------

    def _update(self, args: Namespace) -> int:
        """Update installed skills from their sources."""
        from r4.skill_lockfile import list_installed

        installed = list_installed()
        if not installed:
            print("No installed skills to update.")
            return 0
        if args.name and args.name not in installed:
            print(f"Skill '{args.name}' not found in lockfile.")
            return 1

        targets = {args.name: installed[args.name]} if args.name else installed
        updated = 0
        for skill_name, info in targets.items():
            if self._update_one(skill_name, info):
                updated += 1
        print(f"\n{updated} skill(s) updated.")
        return 0

    @staticmethod
    def _update_one(skill_name: str, info: dict) -> bool:
        """Re-install one skill from its source; True when updated."""
        source_type = info.get("sourceType", "")
        source = info.get("source", "")
        skill_path = info.get("skillPath", "")
        if source_type not in ("github", "nvidia"):
            print(f"Skipping '{skill_name}' (source: {source_type})")
            return False
        repo = source.replace("github:", "") if source.startswith("github:") else source
        from r4.github_skills import install_from_github

        target_dir = _SKILL_DIR / skill_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        result = install_from_github(repo, skill_path, target_dir)
        if not result:
            return False
        from r4.skill_lockfile import record_install

        record_install(
            skill_name=skill_name,
            source=source,
            source_type=source_type,
            skill_path=skill_path,
            hash_val=result.get("hash", ""),
            version=result.get("version", ""),
            installed_path=str(target_dir),
        )
        print(f"Updated '{skill_name}'")
        return True

    # -- Generic GitHub install ----------------------------------------------

    def _github(self, args: Namespace) -> int:
        """Install skills from any GitHub repository."""
        action = getattr(args, "github_action", None)
        if action == "install":
            return self._github_install(args.source)
        if action == "list":
            return self._github_list(args.repo)
        print("Usage: r4 skill github {install|list}", file=sys.stderr)
        return 1

    def _github_install(self, source: str) -> int:
        """Install a skill from a GitHub source string."""
        from r4.github_skills import install_from_github, parse_source
        from r4.skill_lockfile import record_install

        try:
            parsed = parse_source(source)
        except ValueError as e:
            print(f"Invalid source: {e}", file=sys.stderr)
            return 1

        repo = parsed["repo"]
        skill_path = parsed["skill_path"]
        skill_name = parsed["skill_name"] or skill_path.split("/")[-1]

        target = _SKILL_DIR / skill_name
        if target.exists():
            print(f"Skill '{skill_name}' already installed at {target}")
            return 1

        print(f"Installing skill '{skill_name}' from {repo}…")
        info = install_from_github(repo, skill_path, target)
        if not info:
            print(f"Failed to install skill '{skill_name}'", file=sys.stderr)
            return 1

        # Run security scan
        from r4.skill_scanner import scan_skill_directory_cli

        scan_result = scan_skill_directory_cli(target, json_output=False)
        if scan_result > 0:
            print(f"Security scan found issues (exit code: {scan_result})")
            if scan_result == 1:
                print("Removing installed skill due to blocking findings.")
                shutil.rmtree(target)
                return 1

        # Record in lockfile
        record_install(
            skill_name=skill_name,
            source=f"github:{repo}",
            source_type="github",
            skill_path=skill_path,
            hash_val=info.get("hash", ""),
            version=info.get("version", ""),
            installed_path=str(target),
        )

        print(f"Installed skill '{skill_name}' to {target}")
        return 0

    def _github_list(self, repo: str) -> int:
        """List skills in a GitHub repository."""
        from r4.github_skills import list_skills

        try:
            skills = list_skills(repo)
        except Exception as e:
            print(f"Failed to list skills: {e}", file=sys.stderr)
            return 1

        if not skills:
            print(f"No skills found in {repo}")
            return 0

        print(f"Skills in {repo}:")
        for skill in skills:
            print(f"  {skill['name']}")
        return 0

    # -- Skills.sh marketplace -----------------------------------------------

    def _skills_sh(self, args: Namespace) -> int:
        """Browse and install from Skills.sh marketplace."""
        action = getattr(args, "skills_sh_action", None)
        if action == "list":
            return self._skills_sh_list(args)
        if action == "search":
            return self._skills_sh_search(args)
        if action == "install":
            return self._skills_sh_install(args)
        print("Usage: r4 skill skills-sh {list|search|install}", file=sys.stderr)
        return 1

    def _skills_sh_list(self, args: Namespace) -> int:
        """List skills from Skills.sh marketplace."""
        from r4.skills_sh import SkillsShAuthError, list_skills

        try:
            skills = list_skills()
        except SkillsShAuthError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Failed to list Skills.sh marketplace: {e}", file=sys.stderr)
            return 1

        as_json = bool(getattr(args, "as_json", False))
        if as_json:
            print(json.dumps(skills, indent=2))
            return 0

        if not skills:
            print("No skills returned by Skills.sh.")
            return 0

        print(f"{'Name':<40} Description")
        print("-" * 80)
        for skill in skills:
            name = skill.get("name", "?")
            desc = skill.get("description", "")[:35]
            print(f"{name:<40} {desc}")
        return 0

    def _skills_sh_search(self, args: Namespace) -> int:
        """Search Skills.sh marketplace."""
        from r4.skills_sh import SkillsShAuthError, search_skills

        try:
            skills = search_skills(args.query)
        except SkillsShAuthError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Failed to search Skills.sh: {e}", file=sys.stderr)
            return 1

        if not skills:
            print(f"No skills matching '{args.query}' on Skills.sh.")
            return 0

        print(f"Skills.sh results for '{args.query}':")
        for skill in skills:
            name = skill.get("name", "?")
            desc = skill.get("description", "")
            print(f"  {name}: {desc}")
        return 0

    def _skills_sh_install(self, args: Namespace) -> int:
        """Install a skill from Skills.sh marketplace."""
        from r4.skill_lockfile import record_install
        from r4.skills_sh import install_skill as install_skills_sh

        skill_name = args.name
        target = _SKILL_DIR / skill_name
        if target.exists():
            print(f"Skill '{skill_name}' already installed at {target}")
            return 1

        print(f"Installing skill '{skill_name}' from Skills.sh…")
        info = install_skills_sh(skill_name, target)
        if not info:
            print(f"Failed to install skill '{skill_name}'", file=sys.stderr)
            return 1

        # Record in lockfile
        record_install(
            skill_name=skill_name,
            source="skills.sh",
            source_type="skills-sh",
            skill_path="",
            hash_val="",
            version="",
            installed_path=str(target),
        )

        print(f"Installed skill '{skill_name}' to {target}")
        return 0

    def _info(self, args: Namespace) -> int:
        name = args.name
        loader = SkillLoader()
        loader.scan_local()
        skill = loader.get(name)
        if not skill:
            print(f"Skill '{name}' not found locally")
            return 1
        print(f"Name:        {skill.name}")
        print(f"Source:      {skill.source}")
        print(f"Path:        {skill.path}")
        print(f"Description: {skill.description}")
        print(f"MCP:         {'yes' if skill.has_mcp else 'no'}")
        if skill.config:
            print(f"Version:     {skill.config.get('version', 'n/a')}")
            print(f"Author:      {skill.config.get('author', 'n/a')}")
            tags = skill.config.get("tags", [])
            if tags:
                print(f"Tags:        {', '.join(tags)}")
        return 0

    # -- NVIDIA Agent Skills -------------------------------------------------

    def _nvidia(self, args: Namespace) -> int:
        action = getattr(args, "nvidia_action", None)
        if action == "list":
            return self._nvidia_list(args)
        if action == "install":
            return self._nvidia_install(args)
        if action == "search":
            return self._nvidia_search(args)
        if action == "info":
            return self._nvidia_info(args)
        print("Usage: r4 skill nvidia {list|install|search|info}", file=sys.stderr)
        return 1

    def _nvidia_list(self, args: Namespace) -> int:
        from r4.nvidia_skills import list_catalog

        try:
            skills = list_catalog()
        except Exception as exc:  # noqa: BLE001 - CLI surface
            print(f"Failed to fetch NVIDIA catalog: {exc}", file=sys.stderr)
            return 1

        as_json = bool(getattr(args, "as_json", False))
        if as_json:
            print(json.dumps(skills, indent=2))
            return 0

        if not skills:
            print("No NVIDIA skills found.")
            return 0

        print(f"{'Name':<40} URL")
        print("-" * 80)
        for skill in skills:
            print(f"{skill['name']:<40} {skill.get('url', '')}")
        return 0

    def _nvidia_install(self, args: Namespace) -> int:
        return self._install_from_nvidia(args.name)

    def _nvidia_search(self, args: Namespace) -> int:
        from r4.nvidia_skills import search_catalog

        try:
            skills = search_catalog(args.query)
        except Exception as exc:  # noqa: BLE001 - CLI surface
            print(f"Failed to search NVIDIA catalog: {exc}", file=sys.stderr)
            return 1

        if not skills:
            print(f"No NVIDIA skills matching '{args.query}'.")
            return 0

        print(f"NVIDIA skills matching '{args.query}':")
        for skill in skills:
            print(f"  {skill['name']}")
        return 0

    def _nvidia_info(self, args: Namespace) -> int:
        from r4.nvidia_skills import get_skill_info

        try:
            info = get_skill_info(args.name)
        except Exception as exc:  # noqa: BLE001 - CLI surface
            print(f"Failed to fetch NVIDIA skill info: {exc}", file=sys.stderr)
            return 1

        if not info:
            print(f"NVIDIA skill '{args.name}' not found.", file=sys.stderr)
            return 1

        print(f"Name:  {info['name']}")
        print(f"Files: {', '.join(info['files'].keys())}")
        return 0

    def _install_from_nvidia(self, skill_name: str) -> int:
        """Install a skill from the NVIDIA/skills GitHub repository."""
        from r4.nvidia_skills import install_skill as install_nvidia_skill

        target = _SKILL_DIR / f"nvidia-{skill_name}"
        if target.exists():
            print(f"Skill 'nvidia-{skill_name}' already installed at {target}")
            return 1

        print(f"Installing NVIDIA skill '{skill_name}'…")
        info = install_nvidia_skill(skill_name, target)
        if not info:
            print(
                f"  Failed to install NVIDIA skill '{skill_name}'",
                file=sys.stderr,
            )
            return 1

        # Run security scan on the SKILL.md
        skill_md_path = target / "SKILL.md"
        if skill_md_path.exists():
            from r4_contracts.security import SkillScanner

            md_text = skill_md_path.read_text()
            result = SkillScanner().scan_skill({"name": skill_name}, md_text)
            if result.has_errors:
                print(f"Security scan failed for 'nvidia-{skill_name}':")
                for finding in result.findings:
                    print(f"  [{finding.severity.value}] {finding.category}: {finding.message}")
                return 1
            if result.warning_count > 0:
                print(f"Security scan warnings for 'nvidia-{skill_name}':")
                for finding in result.findings:
                    if finding.severity.value == "warning":
                        print(f"  {finding.category}: {finding.message}")

        print(f"Installed NVIDIA skill '{skill_name}' to {target}")
        print(f"  Files: {', '.join(info.get('files', {}).keys())}")
        print()
        print("To verify the skill's NVIDIA signature:")
        print("  pip install model_signing")
        print(f"  model_signing verify certificate {target} \\")
        print(f"    --signature {target}/skill.oms.sig \\")
        print("    --certificate_chain nv-agent-root-cert.pem \\")
        print("    --ignore_unsigned_files")
        return 0


def _green_receipt(skill_dir: Path, args: Namespace) -> dict[str, Any] | None:
    """Run the proposal's suite; the merge-on-green receipt, or None (the
    refusal already printed: no tests / timeout / red suite)."""
    tests_dir = skill_dir / "tests"
    run_sh = tests_dir / "run.sh"
    if not run_sh.is_file():
        print(
            f"✗ Merge-on-green: {skill_dir} carries no tests "
            f"(expected {tests_dir}/run.sh) — a loop-proposed skill merges "
            "only when a suite passes; add tests and retry, or "
            "`r4 skill reject`.",
            file=sys.stderr,
        )
        return None
    try:
        return_code, stdout, stderr = _run_suite(str(run_sh), args.timeout)
    except _SuiteTimeout:
        print(f"✗ Skill tests timed out after {args.timeout}s: {args.target}", file=sys.stderr)
        return None
    if return_code != 0:
        _print_red_suite(return_code, stdout, stderr)
        return None
    from datetime import datetime, timezone

    return {
        "runner": "r4 skill test",
        "exit_code": 0,
        "tests_present": True,
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "output_tail": (stdout or stderr or "")[-500:],
    }


def _print_red_suite(return_code: int, stdout: str, stderr: str) -> None:
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    print(
        f"✗ Merge-on-green: suite FAILED (exit {return_code}) — a red "
        "suite never merges; fix the skill or `r4 skill reject`.",
        file=sys.stderr,
    )
