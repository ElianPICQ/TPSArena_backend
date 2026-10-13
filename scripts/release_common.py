"""Release tpsarena-common: python scripts/release_common.py X.Y.Z [--no-push] [--dry-run]

1. Checks the working tree is clean and the tag doesn't exist yet.
2. Runs the libs/common tests.
3. Sets the version in libs/common/pyproject.toml and turns the CHANGELOG's "Unreleased"
   section into "X.Y.Z".
4. Commits, tags `common-vX.Y.Z`, pushes the commit and the tag.

Services then upgrade one at a time: change `tag` in their pyproject.toml and run `uv lock`.
Runs the same on Debian and Windows (plain Python, git and uv on the PATH).
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "libs" / "common"
PYPROJECT = COMMON / "pyproject.toml"
CHANGELOG = COMMON / "CHANGELOG.md"


def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="e.g. 0.2.0")
    parser.add_argument("--no-push", action="store_true", help="commit and tag locally only")
    parser.add_argument("--dry-run", action="store_true", help="check and test, change nothing")
    args = parser.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("Version must look like 1.2.3")
    tag = f"common-v{args.version}"

    if run("git", "status", "--porcelain", capture=True):
        sys.exit("The working tree has uncommitted changes: commit or stash them first.")
    if run("git", "tag", "--list", tag, capture=True):
        sys.exit(f"Tag {tag} already exists.")

    print("Running libs/common tests...")
    run("uv", "run", "pytest", "-q", cwd=COMMON)

    pyproject = PYPROJECT.read_text()
    current = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    changelog = CHANGELOG.read_text()
    if f"## {args.version}" in changelog and current == args.version:
        new_changelog = changelog  # first release: version and changelog already written
    else:
        if "## Unreleased" not in changelog:
            sys.exit("CHANGELOG.md has no '## Unreleased' section.")
        new_changelog = changelog.replace("## Unreleased", f"## Unreleased\n\n## {args.version} ({date.today()})", 1)

    if args.dry_run:
        print(f"Dry run OK: would release {tag} (current version {current}).")
        return

    PYPROJECT.write_text(re.sub(r'^version = "[^"]+"', f'version = "{args.version}"', pyproject, count=1, flags=re.M))
    CHANGELOG.write_text(new_changelog)
    run("git", "add", str(PYPROJECT), str(CHANGELOG))
    if run("git", "status", "--porcelain", capture=True):
        run("git", "commit", "-m", f"Release tpsarena-common {args.version}")
    run("git", "tag", "-a", tag, "-m", f"tpsarena-common {args.version}")
    print(f"Tagged {tag}.")

    if not args.no_push:
        run("git", "push", "origin", "HEAD")
        run("git", "push", "origin", tag)
        print(f"Pushed {tag}.")


if __name__ == "__main__":
    main()
