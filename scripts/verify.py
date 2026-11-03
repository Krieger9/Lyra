"""Run the Studio suites, Lyra PostgreSQL tests, editor tests and production build."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--studio-python", default=sys.executable,
                        help="Python environment with pytest and the two editable Studio packages")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    if not os.environ.get("LYRA_TEST_DATABASE_URL", "").strip():
        parser.exit(2, "Set LYRA_TEST_DATABASE_URL to a PostgreSQL test database before running verification.\n")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        parser.exit(2, "Install npm to run the editor build.\n")
    commands = (
        ("Studio packages", [args.studio_python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "../Infrastructure/PromptStudio/tests", "../Infrastructure/AgentStudio/tests"]),
        ("Lyra with PostgreSQL", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"]),
        ("Editor tests", ["node", "--test", *sorted((root / "tests").glob("*.test.mjs"))]),
        ("TypeScript and production build", [npm, "run", "build"]),
    )
    for label, command in commands:
        print(f"\n{label}", flush=True)
        result = subprocess.run(command, cwd=root)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
