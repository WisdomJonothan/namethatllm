#!/usr/bin/env python
"""把 areebbb v24 复现 notebook 改成"诊断版"：用独立验证集跑完整推理，落盘可重放的中间产物。

改动点（全部可在 ntb 里用字符串定位）：
  c9  在 FAST_COMMIT 打印之前插入 DIAG 数据覆盖：test.csv 换成 eval_v2，
      并强制 FAST_COMMIT=False（跑真流程，不走 7 秒短路）
  c37 OUT -> /kaggle/working/diag_pred.parquet；追加 --save_lp /kaggle/working/diag_lp.npy
      （reader 成员加权后的原始 log-prob，未做 comp 混合与 sinkhorn）
  c41 V1_ENABLED=False（省下约 2.1h 的 GLM 似然阶段；本阶段只调 comp/校准/残差）
  末尾  新增一格：把 /kaggle/temp/final-logits.npz、comp_meta.json 复制到 /kaggle/working，
        并打印形状与 argmax 分布，便于事后离线重放

产物：repro/diag_v1.ipynb
"""
from __future__ import annotations

import json
from pathlib import Path

SRC = Path(r"D:\NameThatLLM\repro\areebbb_v24.ipynb")
DST = Path(r"D:\NameThatLLM\repro\diag_v1.ipynb")

DIAG_BLOCK = '''
# ---- ours (DIAG stage 1): run the full pipeline on an INDEPENDENT eval set ----
DIAG_DATA = '/kaggle/temp/ntl-diag-data'
os.makedirs(DIAG_DATA, exist_ok=True)
for _f in os.listdir(DATA):
    if _f != 'test.csv' and not os.path.exists(os.path.join(DIAG_DATA, _f)):
        os.symlink(os.path.join(DATA, _f), os.path.join(DIAG_DATA, _f))
_ev_path = first('/kaggle/input/**/eval_v2.csv')
_ev = pd.read_csv(_ev_path)
_ev[['id', 'text']].to_csv(os.path.join(DIAG_DATA, 'test.csv'), index=False)
print('DIAG eval', _ev_path, 'rows', len(_ev), 'prompts', _ev.prompt_id.nunique(), flush=True)
print('DIAG class counts', _ev.model.value_counts().to_dict(), flush=True)
DATA = DIAG_DATA
os.environ['NTL_DATA'] = DATA
FAST_COMMIT = False
'''


def main() -> None:
    nb = json.loads(SRC.read_text(encoding="utf-8"))
    cells = nb["cells"]
    edited = []

    # ---- cell 9: 插入 DIAG 块 ----
    src9 = "".join(cells[9]["source"])
    anchor = "print('FAST_COMMIT', FAST_COMMIT)"
    assert anchor in src9, "anchor not found in cell 9"
    src9 = src9.replace(anchor, DIAG_BLOCK.strip("\n") + "\n" + anchor, 1)
    cells[9]["source"] = src9.splitlines(keepends=True)
    edited.append("c9")

    # ---- cell 37: OUT / save_lp ----
    src37 = "".join(cells[37]["source"])
    old_out = "OUT = '/kaggle/working/submission.parquet'"
    assert old_out in src37, "OUT line not found"
    src37 = src37.replace(old_out, "OUT = '/kaggle/working/diag_pred.parquet'", 1)
    old_tail = "'--out', OUT, '--save_final_logits', FINAL_LOGITS]"
    assert old_tail in src37, "cmd tail not found"
    src37 = src37.replace(
        old_tail,
        "'--out', OUT, '--save_lp', '/kaggle/working/diag_lp.npy', "
        "'--save_final_logits', FINAL_LOGITS]",
        1,
    )
    cells[37]["source"] = src37.splitlines(keepends=True)
    edited.append("c37")

    # ---- cell 41: V1_ENABLED = False ----
    src41 = "".join(cells[41]["source"])
    assert "V1_ENABLED    = True" in src41, "V1_ENABLED not found"
    src41 = src41.replace("V1_ENABLED    = True", "V1_ENABLED    = False", 1)
    cells[41]["source"] = src41.splitlines(keepends=True)
    edited.append("c41")

    # ---- 末尾新增：产物落盘 ----
    tail = '''# ---- ours (DIAG stage 1): collect replay artifacts ----
import shutil
_art = {}
for src, dst in [('/kaggle/temp/final-logits.npz', '/kaggle/working/final-logits.npz'),
                 ('/kaggle/working/comp_logit.npy', '/kaggle/working/comp_logit.npy'),
                 ('/kaggle/working/comp_meta.json', '/kaggle/working/comp_meta.json'),
                 ('/kaggle/working/diag_lp.npy', '/kaggle/working/diag_lp.npy'),
                 ('/kaggle/working/diag_pred.parquet', '/kaggle/working/diag_pred.parquet')]:
    if os.path.exists(src):
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copyfile(src, dst)
        _art[os.path.basename(dst)] = os.path.getsize(dst)
print('DIAG artifacts', _art, flush=True)
if os.path.exists('/kaggle/working/diag_pred.parquet'):
    _p = pd.read_parquet('/kaggle/working/diag_pred.parquet')
    print('DIAG pred', _p.shape, _p.model.value_counts().to_dict(), flush=True)
if os.path.exists('/kaggle/working/diag_lp.npy'):
    _lp = np.load('/kaggle/working/diag_lp.npy')
    print('DIAG lp', _lp.shape, float(np.isfinite(_lp).mean()), flush=True)
'''
    cells.append({"cell_type": "code", "execution_count": None, "metadata": {},
                  "outputs": [], "source": tail.splitlines(keepends=True)})
    edited.append("c+tail")

    DST.write_text(json.dumps(nb, ensure_ascii=False), encoding="utf-8")
    print("edited", edited, "->", DST, "cells", len(cells))


if __name__ == "__main__":
    main()
