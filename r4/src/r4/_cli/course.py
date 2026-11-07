"""``r4 course`` — university course-kit scaffolder (Phase E3)."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path

from ..course import materialize_course
from .base import CommandHandler
from .registry import command


def _cmd_course_create(args: Namespace) -> int:
    """Handle ``r4 course create <dir>``."""
    from ._helpers import _die

    lang = args.lang
    if lang not in ("en", "ja", "both"):
        _die(
            f"unknown --lang {lang!r} (expected en, ja, or both)",
            action="course create",
            error_id="course.lang_invalid",
        )

    course_dir = Path(args.directory).resolve()
    title = args.title or course_dir.name.replace("-", " ").replace("_", " ").title()
    title_ja = args.title_ja

    if course_dir.exists() and any(course_dir.iterdir()) and not args.force:
        _die(
            f"course dir {course_dir} is not empty; pass --force to overwrite",
            action="course create",
            error_id="course.dir_not_empty",
        )

    written = materialize_course(course_dir, title=title, title_ja=title_ja, lang=lang)
    if args.as_json:
        print(
            json.dumps(
                {
                    "course_dir": str(course_dir),
                    "title": title,
                    "title_ja": title_ja,
                    "lang": lang,
                    "files": [str(p) for p in written],
                }
            )
        )
    else:
        print(f"✓ Course kit materialized → {course_dir}")
        print(f"  Title: {title}")
        if title_ja:
            print(f"  Title (JA): {title_ja}")
        print(f"  Language: {lang}")
        print(f"  Files: {len(written)} written")
    return 0


@command
class CourseCommand(CommandHandler):
    """``r4 course`` — scaffold a university course kit."""

    name = "course"
    help = "Scaffold a university course kit on the R4 platform"

    def register(self, subparsers) -> ArgumentParser:
        p = subparsers.add_parser(self.name, help=self.help)
        sub = p.add_subparsers(dest="course_action", metavar="<action>")
        sub.required = False

        create = sub.add_parser("create", help="Scaffold a course directory")
        create.add_argument(
            "directory",
            help="Target course directory (created if missing)",
        )
        create.add_argument("--title", "-t", help="Course title (defaults from directory name)")
        create.add_argument("--title-ja", dest="title_ja", help="Japanese course title (optional)")
        create.add_argument(
            "--lang",
            choices=("en", "ja", "both"),
            default="both",
            help="Which language sections to materialize (default: both)",
        )
        create.add_argument("--force", action="store_true", help="Overwrite existing files")
        create.add_argument(
            "--json", "-j", action="store_true", dest="as_json", help="Output as JSON"
        )
        return p

    def handle(self, args: Namespace) -> int:
        action = getattr(args, "course_action", None)
        if action is None:
            # Bare `r4 course` carries none of create's options (c2-cli,
            # 2026-10-21: it died with "'Namespace' object has no attribute 'lang'").
            print(
                "Usage: r4 course create <directory> [--title T] [--lang en|ja|both]",
                file=sys.stderr,
            )
            return 2
        if action == "create":
            return _cmd_course_create(args)
        from ._helpers import _die

        _die(f"unknown course action: {action}", error_id="course.action_unknown")
        return 1


def _course_command_handlers():
    return {"create": _cmd_course_create}
