"""把本机公钥装到服务器（只需一次），之后就能用普通 ssh。

密码从环境变量读，不落盘：
    NTSSH_PW='...' python scripts/ssh_install_key.py wujiaheng 10.13.71.195

装完还会顺手打印服务器硬件信息，确认 A 阶段能不能开跑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

KEY_PATH = Path("D:/Cadence/SPB_Data/.ssh/id_ed25519")

REMOTE = r"""
mkdir -p ~/.ssh && chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
grep -qF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys
echo "=== whoami ===";  whoami; echo \$HOME
echo "=== keys ==="; ssh-keygen -lf ~/.ssh/authorized_keys 2>&1
echo "=== disk ==="; df -h /tmp /homeC 2>/dev/null | tail -2
echo "=== gpu ==="; nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo "=== python ==="; which python3 python 2>/dev/null | head -2
python3 -c "import torch,transformers;print('torch',torch.__version__,torch.version.cuda,torch.cuda.is_available());print('transformers',transformers.__version__)" 2>&1 | tail -3
"""


def main() -> None:
    user, host = sys.argv[1], sys.argv[2]
    pw = os.environ.get("NTSSH_PW")
    if not pw:
        raise SystemExit("NTSSH_PW not set")

    pub = KEY_PATH.with_suffix(".pub").read_text(encoding="utf-8").strip()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(host, username=user, password=pw, timeout=15, auth_timeout=15,
                look_for_keys=False, allow_agent=False)
    print(f"connected to {user}@{host}")

    _, out, err = cli.exec_command(REMOTE.format(pub=pub), timeout=120)
    print(out.read().decode("utf-8", "replace"))
    e = err.read().decode("utf-8", "replace").strip()
    if e:
        print("stderr:", e[:500])
    cli.close()


if __name__ == "__main__":
    main()
