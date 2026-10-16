"""Explicitly review or prepare embedded Python paths for local image generation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_generation.python_environment import (  # noqa: E402
    PythonEnvironmentError,
    check_python_environment,
    prepare_python_environment,
)


def main(argv: list[str] | None = None) -> int:
    """Return a path-free JSON review; mutation requires an explicit --apply."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Trusted actual interpreter executable.")
    parser.add_argument("--runtime-root", type=Path, required=True, help="Reviewed ComfyUI installation directory.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Prepare absolute roots and retain an original backup.")
    mode.add_argument("--check", action="store_true", help="Require compatibility without changing files.")
    options = parser.parse_args(argv)
    try:
        result = (check_python_environment(options.python, options.runtime_root) if options.check else
                  prepare_python_environment(options.python, options.runtime_root, apply=options.apply))
    except PythonEnvironmentError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps({"ok": True, "result": asdict(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
