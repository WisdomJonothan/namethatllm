"""Convert a `# %%`-marked Python file into a Kaggle-ready .ipynb.

Keeping the notebook source as plain Python makes it reviewable and diffable;
this is the only place that knows about notebook JSON.

    python scripts/py2ipynb.py notebooks/a_stage_probe.py notebooks/a_stage_probe.ipynb
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARKDOWN = "# %% [markdown]"
CODE = "# %%"


def parse(src: str) -> list[dict]:
    cells: list[dict] = []
    kind, buf = None, []

    def flush() -> None:
        if kind is None:
            return
        text = "\n".join(buf).strip("\n")
        if not text.strip():
            return
        lines = text.splitlines(keepends=True)
        if kind == "markdown":
            body = [ln[2:] if ln.startswith("# ") else ln.lstrip("#") for ln in lines]
            cells.append({"cell_type": "markdown", "metadata": {}, "source": body})
        else:
            cells.append(
                {
                    "cell_type": "code",
                    "execution_count": None,
                    "metadata": {},
                    "outputs": [],
                    "source": lines,
                }
            )

    for line in src.splitlines():
        if line.startswith(MARKDOWN):
            flush()
            kind, buf = "markdown", []
        elif line.startswith(CODE):
            flush()
            kind, buf = "code", []
        else:
            if kind is not None:
                buf.append(line)
    flush()
    return cells


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src_path, dst_path = Path(sys.argv[1]), Path(sys.argv[2])
    nb = {
        "cells": parse(src_path.read_text(encoding="utf-8")),
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{src_path} -> {dst_path} ({len(nb['cells'])} cells)")


if __name__ == "__main__":
    main()
