from __future__ import annotations
import json
from pathlib import Path

SRC = Path(r"D:\NameThatLLM\repro\diag_v1.ipynb")
DST = Path(r"D:\NameThatLLM\repro\diag_v3.ipynb")

MEMBER_DUMP = """tot = sum(LP.values())
if a.save_lp:
    np.save(a.save_lp, tot)
    try:
        _members = {k: np.asarray(v, dtype=np.float32) for k, v in LP.items()}
        np.savez(a.save_lp.replace('.npy', '_members.npz'), **(_members or {'none': np.zeros((1, 18), np.float32)}))
        log('member LPs saved', sorted(_members))
    except Exception as _e:
        log('member LP dump failed:', repr(_e))
"""

TAIL = '''# ---- ours (DIAG stage 3): collect the artifacts this run has to leave behind ----
import glob as _glob, json as _json, os as _os, shutil as _sh
_w = '/kaggle/working'
_report = {}
for _p in ('/kaggle/working/v1/report.json', '/kaggle/temp/v1/v1/report.json'):
    if _os.path.exists(_p):
        _report = _json.load(open(_p))
        break
print('v1 report:', _json.dumps(_report, indent=1))
if _os.path.exists('/kaggle/temp/final-logits.npz') and not _os.path.exists(f'{_w}/final-logits.npz'):
    _sh.copy('/kaggle/temp/final-logits.npz', f'{_w}/final-logits.npz')
for _p in _glob.glob('/kaggle/temp/v1/**/*.npz', recursive=True) + _glob.glob('/kaggle/working/v1/*.npz'):
    _t = f'{_w}/v1_' + _os.path.basename(_p)
    if not _os.path.exists(_t):
        _sh.copy(_p, _t)
print('working files:', sorted(_os.path.basename(p) for p in _glob.glob(f'{_w}/*')), flush=True)
'''


def main() -> None:
    nb = json.loads(SRC.read_text(encoding="utf-8"))
    cells = nb["cells"]

    s = "".join(cells[32]["source"])
    old = "tot = sum(LP.values())\nif a.save_lp:\n    np.save(a.save_lp, tot)\n"
    assert old in s, "c32 anchor missing"
    cells[32]["source"] = s.replace(old, MEMBER_DUMP, 1).splitlines(keepends=True)

    s = "".join(cells[41]["source"])
    assert "V1_ENABLED    = False" in s and "V1_DEADLINE_H = 8.8" in s
    s = s.replace("V1_ENABLED    = False", "V1_ENABLED    = True", 1)
    s = s.replace("V1_DEADLINE_H = 8.8", "V1_DEADLINE_H = 7.5", 1)
    cells[41]["source"] = s.splitlines(keepends=True)

    s = "".join(cells[45]["source"])
    anchor = "    _code = subprocess.call(_cmd, env=V1_ENV)"
    assert anchor in s, "c45 anchor missing"
    s = s.replace(anchor, "    _cmd += ['--max-changed', '1.0']  # diag: eval_v2 is not OOF\n" + anchor, 1)
    cells[45]["source"] = s.splitlines(keepends=True)

    s = "".join(cells[48]["source"])
    anchor = "if not FAST_COMMIT and os.path.exists(OUT):\n    try:\n"
    assert anchor in s, "c48 anchor missing"
    s = s.replace(anchor, anchor + "        shutil.copy(OUT, '/kaggle/working/pred_pre_apex.parquet')\n", 1)
    if not s.startswith("import shutil"):
        s = "import shutil\n" + s
    cells[48]["source"] = s.splitlines(keepends=True)

    cells.append({"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                  "source": TAIL.splitlines(keepends=True)})
    DST.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {DST} cells={len(cells)}")


if __name__ == "__main__":
    main()
