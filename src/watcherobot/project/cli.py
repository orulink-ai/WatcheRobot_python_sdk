"""Offline project command-line entry points."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from watcherobot.project import (
    ProjectError,
    add_project_member,
    init_project,
    inspect_project,
)
from watcherobot.project.model import KINDS


def add_commands(commands: Any) -> None:
    parser = commands.add_parser(
        "project", help="Create and inspect offline development projects"
    )
    subcommands = parser.add_subparsers(dest="project_command", required=True)
    init = subcommands.add_parser("init")
    init.add_argument("directory", type=Path)
    init.add_argument("--kind", choices=KINDS, required=True)
    add = subcommands.add_parser("add")
    add.add_argument("kind", choices=("application", "firmware"))
    add.add_argument("directory", type=Path)
    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("directory", type=Path)
    inspect.add_argument("--jsonl", action="store_true")


def run_command(args: argparse.Namespace) -> int:
    try:
        if args.project_command == "init":
            project = init_project(args.directory, kind=args.kind).to_dict()
        elif args.project_command == "add":
            project = add_project_member(args.directory, kind=args.kind).to_dict()
        else:
            project = inspect_project(args.directory)
    except (ValueError, OSError, RuntimeError) as exc:
        raise ProjectError(str(exc)) from exc
    print(json.dumps(project, ensure_ascii=False, separators=(",", ":")))
    return 0
