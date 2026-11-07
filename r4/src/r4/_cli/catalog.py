"""``r4 catalog`` command handler (gap row D-39, Phase 1).

Thin wrapper over the gateway's ``/gw/scientific-catalogs*`` routes (or
``/umrs/scientific-catalogs*`` in direct-UMRS mode): the Scientific
Catalog as a named, versioned object — member graph/skill/MCP/embedding
ids plus contributors, licence and provenance. One row per
(project, name, version); versions are immutable in membership; retire
is a tombstone, never a delete.
"""

from __future__ import annotations

import json
from argparse import ArgumentParser, Namespace

from .base import CommandHandler
from .registry import command


@command
class CatalogCommand(CommandHandler):
    """``r4 catalog`` — Scientific Catalog registry."""

    name = "catalog"
    help = "Scientific Catalogs: named, versioned knowledge objects (D-39)"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(
            self.name,
            help=self.help,
            description=(
                "Scientific Catalog registry (gap row D-39). A catalog bundles "
                "the ids of its members (knowledge graphs, skills, MCP servers, "
                "embedding models) plus contributors, licence and provenance. "
                "This is NOT the data catalog — that lives under `r4 data catalog`."
            ),
        )
        sub = p.add_subparsers(dest="catalog_command", metavar="<catalog-command>")
        sub.required = True

        create = sub.add_parser("create", help="Create a catalog version (idempotent)")
        create.add_argument("name")
        create.add_argument("--project", required=True, help="Owning project id")
        create.add_argument("--version", default="0.1.0")
        create.add_argument("--description", default="")
        create.add_argument("--ontology", default="")
        create.add_argument("--graph", action="append", default=[], dest="graphs")
        create.add_argument("--skill", action="append", default=[], dest="skills")
        create.add_argument("--mcp", action="append", default=[], dest="mcps")
        create.add_argument("--embedding-model", action="append", default=[], dest="embeds")
        create.add_argument("--contributor", action="append", default=[], dest="contributors")
        create.add_argument("--licence", default="")
        create.add_argument("--change-note", default="", dest="change_note")
        create.add_argument(
            "--redact",
            action="append",
            default=[],
            dest="redact",
            metavar="FIELD:ACTION",
            help="Export redaction rule (D-46), e.g. contributors:drop, provenance:hash",
        )
        create.add_argument("--json", "-j", action="store_true", dest="as_json")

        list_cmd = sub.add_parser("list", help="List active catalogs")
        list_cmd.add_argument("--project", default=None, help="Project id filter")
        list_cmd.add_argument("--json", "-j", action="store_true", dest="as_json")

        show = sub.add_parser("show", help="One catalog row")
        show.add_argument("catalog_id")
        show.add_argument("--json", "-j", action="store_true", dest="as_json")

        patch = sub.add_parser("patch", help="Metadata-only update")
        patch.add_argument("catalog_id")
        patch.add_argument("--description", default=None)
        patch.add_argument("--ontology", default=None)
        patch.add_argument("--licence", default=None)
        patch.add_argument("--contributor", action="append", default=None, dest="contributors")
        patch.add_argument(
            "--redact",
            action="append",
            default=None,
            dest="redact",
            metavar="FIELD:ACTION",
            help="Replace redaction rules (D-46), e.g. contributors:drop",
        )
        patch.add_argument("--json", "-j", action="store_true", dest="as_json")

        retire = sub.add_parser("retire", help="Tombstone a catalog version")
        retire.add_argument("catalog_id")
        retire.add_argument("--json", "-j", action="store_true", dest="as_json")

        versions = sub.add_parser("versions", help="Version chain (changelog view)")
        versions.add_argument("name")
        versions.add_argument("--project", required=True)
        versions.add_argument("--json", "-j", action="store_true", dest="as_json")

        pin = sub.add_parser("pin", help="Pin a job/result/finding ref to a version")
        pin.add_argument("catalog_id")
        pin.add_argument("kind", help="job | result | finding | paper")
        pin.add_argument("ref", help="job id / artifact id / doi / url")
        pin.add_argument("--note", default="")
        pin.add_argument("--json", "-j", action="store_true", dest="as_json")
        pb = sub.add_parser("pinned-by", help="Which catalog versions pinned this ref (D-45 join)")
        pb.add_argument("ref", help="job id / artifact id / publication id / doi / url")
        pb.add_argument("--project-id", default=None, help="scope to one project")
        pb.add_argument("--json", "-j", action="store_true", dest="as_json")

        access = sub.add_parser(
            "access",
            help="Show or set who may read a catalog (user/project/group/system)",
        )
        access.add_argument("catalog_id")
        access.add_argument(
            "--set",
            dest="access_set",
            action="append",
            metavar="SCOPE",
            help="Replace list (repeatable): user:<id>, project:<id>, circle:<proj>/<id>, system",
        )
        access.add_argument(
            "--private",
            action="store_true",
            help="Clear the list: only the owning project may read the catalog",
        )
        return p

    def handle(self, args: Namespace) -> int:
        from ._helpers import _preflight_check

        _preflight_check(action="catalog")
        return cmd_catalog(args)


def _emit(payload: dict, as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(human)


def cmd_catalog(args: Namespace) -> int:
    from ..client import Client

    client = Client()
    verb = getattr(args, "catalog_command", None) or ""
    handler = _VERBS.get(verb)
    if handler is None:
        print("Usage: r4 catalog <create|list|show|patch|retire|access>")
        return 1
    return handler(client, args)


def _redact_rules(raw: list | None) -> list:
    """FIELD:ACTION spellings → rule dicts; bad spellings are named, not silent."""
    rules = []
    for item in raw or []:
        field, _, action = item.partition(":")
        if not field or action not in ("drop", "hash"):
            raise ValueError(f"bad --redact spelling {item!r} (expected FIELD:drop|FIELD:hash)")
        rules.append({"target": field.strip(), "action": action})
    return rules


def _create(client, args) -> int:
    row = client.create_scientific_catalog(
        project_id=args.project,
        name=args.name,
        version=args.version,
        description=args.description,
        ontology=args.ontology,
        member_graph_ids=args.graphs,
        skill_ids=args.skills,
        mcp_server_ids=args.mcps,
        embedding_model_ids=args.embeds,
        contributors=args.contributors,
        licence=args.licence,
        change_note=args.change_note,
        redaction_rules=_redact_rules(getattr(args, "redact", None)),
    )
    _emit(
        row,
        args.as_json,
        f"Catalog {row.get('catalog_id')} ({row.get('name')} v{row.get('version')}) ready.",
    )
    return 0


def _list(client, args) -> int:
    result = client.list_scientific_catalogs(project_id=args.project)
    catalogs = result.get("catalogs", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0
    if not catalogs:
        print("No scientific catalogs.")
        return 0
    for row in catalogs:
        members = (
            f"graphs={len(row.get('member_graph_ids', []))} "
            f"skills={len(row.get('skill_ids', []))} "
            f"mcp={len(row.get('mcp_server_ids', []))}"
        )
        print(
            f"{row.get('catalog_id')}  {row.get('project_id')}/"
            f"{row.get('name')} v{row.get('version')}  {members}  "
            f"licence={row.get('licence') or '—'}"
        )
    return 0


def _show(client, args) -> int:
    row = client.get_scientific_catalog(args.catalog_id)
    _emit(
        row,
        args.as_json,
        f"{row.get('catalog_id')}  {row.get('project_id')}/{row.get('name')} "
        f"v{row.get('version')}  status={row.get('status')}",
    )
    return 0


def _patch(client, args) -> int:
    redact = getattr(args, "redact", None)
    patch = {
        k: v
        for k, v in {
            "description": args.description,
            "ontology": args.ontology,
            "licence": args.licence,
            "contributors": args.contributors,
            "redaction_rules": _redact_rules(redact) if redact is not None else None,
        }.items()
        if v is not None
    }
    if not patch:
        print("Nothing to patch (pass --description/--ontology/--licence/--contributor/--redact).")
        return 1
    row = client.patch_scientific_catalog(args.catalog_id, patch=patch)
    _emit(row, args.as_json, f"Catalog {row.get('catalog_id')} updated.")
    return 0


def _retire(client, args) -> int:
    row = client.retire_scientific_catalog(args.catalog_id)
    _emit(row, args.as_json, f"Catalog {row.get('catalog_id')} retired (tombstoned).")
    return 0


def _versions(client, args) -> int:
    result = client.list_catalog_versions(project_id=args.project, name=args.name)
    versions = result.get("versions", [])
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0
    if not versions:
        print(f"No versions of {args.name!r} in {args.project}.")
        return 0
    for row in versions:
        pins = len((row.get("provenance") or {}).get("pins", []))
        note = row.get("change_note") or "—"
        print(
            f"{row.get('catalog_id')}  v{row.get('version')}  "
            f"status={row.get('status')}  pins={pins}  note={note}"
        )
    return 0


def _pin(client, args) -> int:
    row = client.add_catalog_pin(args.catalog_id, kind=args.kind, ref=args.ref, note=args.note)
    pins = (row.get("provenance") or {}).get("pins", [])
    _emit(
        row,
        args.as_json,
        f"Pinned {args.kind}:{args.ref} to {args.catalog_id} ({len(pins)} pins total).",
    )
    return 0


def _pinned_by(client, args) -> int:
    """D-45 join: which catalog versions pinned this ref (never invented)."""
    data = client.catalog_pins_by_ref(args.ref, project_id=args.project_id)
    pins = data.get("pins", [])
    if not pins:
        _emit(
            data,
            args.as_json,
            f"No catalog pins {args.ref!r} — the ref joins no provenance graph.",
        )
        return 0
    human = "\n".join(
        f"{p.get('catalog_id')}  kind={p.get('kind')}  note={p.get('note') or '—'}" for p in pins
    )
    _emit(data, args.as_json, human)
    return 0


def _access(client, args) -> int:
    """Show or replace a catalog's ADR-080 access list."""
    if args.private or args.access_set:
        row = client.set_catalog_access(args.catalog_id, [] if args.private else args.access_set)
    else:
        row = client.get_catalog_access(args.catalog_id)
    scopes = row.get("access") or []
    owner = row.get("project_id") or "?"
    print(f"{args.catalog_id}  owning project: {owner}")
    print("  access: " + (", ".join(scopes) if scopes else "owning project only"))
    return 0


_VERBS = {
    "create": _create,
    "list": _list,
    "show": _show,
    "patch": _patch,
    "retire": _retire,
    "versions": _versions,
    "pin": _pin,
    "pinned-by": _pinned_by,
    "access": _access,
}
