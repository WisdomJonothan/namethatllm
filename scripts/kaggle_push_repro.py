"""Push a private verbatim copy of the reference notebook (areebbb v372211652) to our account.

    python scripts/kaggle_push_repro.py --raw repro/areebbb_pull_raw.json --slug ntl-repro-0716

Keeps source bytes unchanged; only slug/title/private flag are ours. All data sources
are copied from the reference kernel's own metadata (datasets + kernel outputs + models).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

TOKEN_FILE = Path(r"C:\Users\Lenovo\.kaggle\access_token")
OWNER = "jonothanwu"
COMP = "name-that-llm"


def do_push(args) -> None:
    os.environ["KAGGLE_API_TOKEN"] = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from kagglesdk.kaggle_client import KaggleClient
    from kagglesdk.kernels.types.kernels_api_service import ApiSaveKernelRequest

    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    md = raw["metadata"]
    nb_text = raw["blob"]["source"]

    datasets = list(md.get("datasetDataSources") or [])
    kernels = [k for k in (md.get("kernelDataSources") or []) if not k.startswith("deleted/")]
    models = list(md.get("modelDataSources") or [])
    comps = list(md.get("competitionDataSources") or [COMP])

    print(f"datasets={len(datasets)} kernels={len(kernels)} models={len(models)} comps={comps}")
    if args.drop_deleted:
        dropped = [k for k in (md.get("kernelDataSources") or []) if k.startswith("deleted/")]
        if dropped:
            print("dropped deleted kernels:", dropped)

    req = ApiSaveKernelRequest()
    req.slug = f"{OWNER}/{args.slug}"
    req.new_title = args.title
    req.text = nb_text
    req.language = md.get("language") or "python"
    req.kernel_type = "notebook"
    req.dataset_data_sources = datasets
    req.kernel_data_sources = kernels
    req.competition_data_sources = comps
    req.model_data_sources = models
    req.enable_gpu = True
    req.enable_internet = False
    req.is_private = True

    resp = KaggleClient().kernels.kernels_api_client.save_kernel(req)
    print("url:", getattr(resp, "url", ""))
    print("error:", getattr(resp, "error", ""))
    print("version:", getattr(resp, "version_number", ""))
    if getattr(resp, "error", ""):
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="repro/areebbb_pull_raw.json")
    ap.add_argument("--slug", default="ntl-repro-0716")
    ap.add_argument("--title", default="NTL repro 0.716 (areebbb v372211652)")
    ap.add_argument("--drop-deleted", action="store_true", default=True)
    do_push(ap.parse_args())


if __name__ == "__main__":
    main()
