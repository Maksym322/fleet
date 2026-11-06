#!/usr/bin/env python3
"""Copy Fleet skills into any agent's skills directory (for tools without a plugin system).

Each skill is a folder with a SKILL.md. This copies them to DEST and replaces the two
relative references that only work inside the plugin (`<PLUGIN_ROOT>` and `../../`) with
absolute paths to this checkout, so the skills work from any location.

    python scripts/install_skills.py --dest ~/.cursor/skills          # example
    python scripts/install_skills.py --dest ./.agents/skills --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def rewrite(text: str) -> str:
    root = ROOT.as_posix()
    return text.replace("<PLUGIN_ROOT>", root).replace("](../../", "](" + root + "/")


def plan(dest: Path):
    return [(skill, dest / skill.name) for skill in sorted(SKILLS.iterdir()) if (skill / "SKILL.md").is_file()]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", required=True, type=Path, help="the agent's skills directory")
    parser.add_argument("--dry-run", action="store_true", help="show what would be written, change nothing")
    parser.add_argument("--force", action="store_true", help="replace skills that already exist in DEST")
    args = parser.parse_args(argv)
    dest = args.dest.expanduser().resolve()
    items = plan(dest)
    if not items:
        print("error: no skills found next to this script", file=sys.stderr)
        return 2
    clash = [target for _, target in items if target.exists()]
    if clash and not args.force:
        print("error: already exists (use --force to replace): " + ", ".join(t.name for t in clash), file=sys.stderr)
        return 1
    for source, target in items:
        print(("would install " if args.dry_run else "install ") + f"{source.name} -> {target}")
        if args.dry_run:
            continue
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for path in source.rglob("*"):
            out = target / path.relative_to(source)
            if path.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            elif path.suffix.lower() in {".md", ".txt"}:
                out.write_text(rewrite(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
            else:
                shutil.copy2(path, out)
    print("done" if not args.dry_run else "dry run: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
