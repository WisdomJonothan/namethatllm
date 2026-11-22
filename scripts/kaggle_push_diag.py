#!/usr/bin/env python
"""把诊断版 notebook（repro/diag_v1.ipynb）推到 Kaggle 并以私有 kernel 形式运行。

数据来源沿用复现 notebook 的 26 个数据集 + 我们就地新增的评估集数据集；
题目数据源保留 competition。其余设置：GPU 开、无网、私有。

    python scripts/kaggle_push_diag.py --slug ntl-diag-stage1
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
RAW = Path(r"D:\NameThatLLM\repro\areebbb_pull_raw.json")
NB = Path(r"D:\NameThatLLM\repro\diag_v1.ipynb")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ntl-diag-stage1")
    ap.add_argument("--title", default="NTL diag stage1 (independent eval, replay artifacts)")
    ap.add_argument("--nb", default=str(NB))
    ap.add_argument("--raw", default=str(RAW))
    args = ap.parse_args()

    os.environ["KAGGLE_API_TOKEN"] = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from kagglesdk.kaggle_client import KaggleClient
    from kagglesdk.kernels.types.kernels_api_service import ApiSaveKernelRequest

    md = json.loads(Path(args.raw).read_text(encoding="utf-8"))["metadata"]
    datasets = [d for d in (md.get("datasetDataSources") or [])]
    for extra in ("jonothanwu/ntl-eval-v2",):
        if extra not in datasets:
            datasets.append(extra)
    kernels = [k for k in (md.get("kernelDataSources") or []) if not k.startswith("deleted/")]
    models = list(md.get("modelDataSources") or [])
    comps = list(md.get("competitionDataSources") or [COMP])

    text = Path(args.nb).read_text(encoding="utf-8")
    print(f"datasets={len(datasets)} kernels={len(kernels)} models={len(models)} comps={comps} nb_bytes={len(text)}")

    req = ApiSaveKernelRequest()
    req.slug = f"{OWNER}/{args.slug}"
    req.new_title = args.title
    req.text = text
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


if __name__ == "__main__":
    main()
