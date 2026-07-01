"""Command-line installation helper."""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from sagasmith_core.nanobot import NanobotSystemBundle

from sagasmith_dnd.system import DND5E


def bundle() -> NanobotSystemBundle:
    root = files("sagasmith_dnd").joinpath("resources")
    return NanobotSystemBundle(
        definition=DND5E,
        skills_dir=Path(str(root.joinpath("skills"))),
        templates_dir=Path(str(root.joinpath("templates"))),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sagasmith-dnd")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install = subparsers.add_parser("install", help="Install D&D assets into a nanobot workspace")
    install.add_argument("--workspace", required=True)
    install.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "install":
        installed = bundle().install(args.workspace, overwrite=args.overwrite)
        for path in installed:
            print(path)
        return 0
    return 1

