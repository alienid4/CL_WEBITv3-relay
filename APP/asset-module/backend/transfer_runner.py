"""檔案傳送（Q94）的「遠端執行層」：所有會碰到目標主機的動作都藏在這個介面後面。

## 為什麼要這一層

  · 開發機（Windows）跑不了 Ansible、也連不到任何主機，測試**絕對不可以碰網路**：
    測試用 FakeRunner（記憶體裡的假檔案系統），正式用 AnsibleRunner。
  · 上層（transfer_service）只認這個介面，不知道底下是 Ansible 還是假的，換掉不影響規則與流程。

## AnsibleRunner 的做法（221 的 Ansible core 2.14）

  · 專用設定檔（WEBIT3 自己的 ansible.cfg）＋每次呼叫臨時產生的 inventory／playbook，用完整個資料夾刪掉。
    **不碰** /etc/ansible/hosts、也不用巡檢系統的帳號或金鑰。
  · SSH 主機金鑰檢查維持開啟：StrictHostKeyChecking=accept-new ＋ WEBIT3 自己的 known_hosts
    （就是 onboard_engine.SSH_KNOWN_HOSTS）。絕不關閉主機金鑰檢查。
  · Windows **不走 Ansible**（審查 BLOCKER 1）：Ansible 的 Windows 模組會編碼 payload，違反專案 SOC 規則。
    改走 transfer_winrm（跟 winrm_collector 同一條 PSRP：NTLM／5985／訊息加密，純文字腳本，檔案用位元組陣列參數）。
  · Linux／AIX：Ansible 的 raw 模組跑腳本（目標機不需要 python——AIX 通常沒有）；
    檔案本體用 scp 搬（可限速 -l，ansible 的 copy 模組做不到，也需要目標機有 python）。
  · 結果由 playbook 的最後一個 play 寫成 JSON 檔再讀回來，不依賴任何 stdout callback 外掛。

⚠️ 以上的 Ansible（Linux raw）與 PSRP 檔案傳輸行為在開發機無法驗證，
見 docs/ui-v2/Q94設計_2026-10-29.md 的「未驗證清單」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import queue
import shlex
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import transfer_policy as pol
import transfer_scripts as sc


class RunnerError(Exception):
    """遠端動作失敗（訊息直接給人看，不含密碼）。"""


class GateStopped(RunnerError):
    """傳到一半被閘門叫停：呼叫端把這台標「已停止」（不是失敗）。"""


class VmwareRefused(RunnerError):
    """目標是 VMware 設備（ESXi／vCenter）：天條，一律拒絕。"""


VMWARE_MESSAGE = "VMware 設備（ESXi／vCenter）不可傳檔、不可下指令，請改由 vCenter API／RVTools 取得"


@dataclass
class Target:
    ip: str
    platform: str                      # windows／linux／aix
    hostname: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    key_path: str = ""
    cred_name: str = ""                # Windows：用的是哪一組收集憑證（只用來寫稽核，不含密碼）

    @property
    def is_windows(self) -> bool:
        return self.platform == "windows"

    @property
    def box(self) -> str:
        return "windows" if self.is_windows else "linux"


def part_name(job_id: int, seq: int, token: str | None = None) -> str:
    """傳輸中的暫存檔名：自己產的安全名稱（不含使用者檔名），驗過雜湊才改成正式名稱。"""
    tok = token or hashlib.sha1(os.urandom(8)).hexdigest()[:8]
    return f"w3_{job_id}_{seq}_{tok}{pol.PART_SUFFIX}"


class TransferRunner:
    """介面。回傳的 dict 欄位見各方法；失敗一律 raise RunnerError（VMware 是 VmwareRefused）。"""

    def probe(self, t: Target, folder: str, name: str | None = None) -> dict:
        """{dir_exists, free_bytes|None, exists, size|None, mtime|None, symlink}

        Linux／AIX 的每一支腳本最前面都有 VMware 守門句，所以任何動作碰到 ESXi／vCenter 都會 raise VmwareRefused
        （fail-closed）；FakeRunner 也是每個動作都這樣。因此不另設「先檢查再做」的步驟（那會多一次連線、也有空窗）。"""
        raise NotImplementedError

    def part_free(self, t: Target, folder: str, part: str) -> bool:
        raise NotImplementedError

    def upload(self, t: Target, local_path: Path, folder: str, part: str, rate_mbps: int | None, check=None) -> int:
        """把本機檔案送成 <folder>/<part>，回送了幾位元組。"""
        raise NotImplementedError

    def finalize(self, t: Target, folder: str, part: str, final: str, expected_sha256: str, overwrite: bool) -> dict:
        """在目標機驗雜湊；相符才改名。回 {result: ok|mismatch|exists|symlink|missing|mvfail, sha256}"""
        raise NotImplementedError

    def remove_part(self, t: Target, folder: str, part: str) -> None:
        raise NotImplementedError

    def sha256(self, t: Target, folder: str, name: str) -> str:
        raise NotImplementedError

    def list_dir(self, t: Target, folder: str) -> list[dict]:
        """[{name, size, mtime}]（不含子資料夾、不含符號連結）"""
        raise NotImplementedError

    def download(self, t: Target, folder: str, name: str, local_path: Path, rate_mbps: int | None, max_bytes: int,
                 check=None) -> int:
        raise NotImplementedError

    def run_command(self, t: Target, folder: str, command: str, timeout: int = 120) -> dict:
        """{rc, output}。從 folder 開始跑。"""
        raise NotImplementedError


# ====================================================================== 假的（測試用）
class FakeRunner(TransferRunner):
    """記憶體裡的假主機。不碰網路、不碰磁碟（除了讀 local_path／寫 local_path）。

    可調的旋鈕（全是公開屬性，測試直接改）：
      vmware_ips      這些 IP 的 guard 會 raise VmwareRefused
      down_ips        這些 IP 連不上（所有動作 raise RunnerError）
      no_folder       {(ip, folder)}：資料夾不存在
      free_bytes      {ip: 可用空間}；沒指定＝很大
      corrupt_ips     這些 IP 收到的檔案內容會被改一個位元組（雜湊不符）
      command_fail    {ip: 例外訊息}：run_command 對這台 raise
      on_upload       上傳前的 callback(ip)——測試用來在傳送途中改時鐘／按停止
    """

    def __init__(self):
        self.fs: dict[str, dict[str, dict[str, bytes]]] = {}
        self.vmware_ips: set[str] = set()
        self.down_ips: set[str] = set()
        self.no_folder: set[tuple[str, str]] = set()
        self.free_bytes: dict[str, int] = {}
        self.corrupt_ips: set[str] = set()
        self.command_fail: dict[str, str] = {}
        self.on_upload = None
        self.calls: list[tuple] = []
        self.rates: list[tuple[str, int | None]] = []
        self._lock = threading.Lock()

    # -- helpers
    def put(self, ip: str, folder: str, name: str, data: bytes) -> None:
        self.fs.setdefault(ip, {}).setdefault(folder, {})[name] = data

    def files(self, ip: str, folder: str) -> dict[str, bytes]:
        return self.fs.get(ip, {}).get(folder, {})

    def _up(self, t: Target, what: str) -> None:
        with self._lock:
            self.calls.append((what, t.ip))
        if t.ip in self.down_ips:
            raise RunnerError("連不上（逾時）")
        if t.ip in self.vmware_ips:
            raise VmwareRefused(VMWARE_MESSAGE)

    # -- interface
    def probe(self, t, folder, name=None):
        self._up(t, "probe")
        if (t.ip, folder) in self.no_folder:
            return {"dir_exists": False, "free_bytes": None, "exists": False, "size": None, "mtime": None, "symlink": False}
        out = {"dir_exists": True, "free_bytes": self.free_bytes.get(t.ip, 10 ** 12), "exists": False,
               "size": None, "mtime": None, "symlink": False}
        if name and name in self.files(t.ip, folder):
            out.update(exists=True, size=len(self.files(t.ip, folder)[name]), mtime="2026-10-29 10:00:00")
        return out

    def part_free(self, t, folder, part):
        self._up(t, "part_free")
        return part not in self.files(t.ip, folder)

    def upload(self, t, local_path, folder, part, rate_mbps, check=None):
        self._up(t, "upload")
        if self.on_upload:
            self.on_upload(t.ip)
        if check is not None:
            why = check()
            if why:
                raise GateStopped(f"已停止：{why}")
        data = Path(local_path).read_bytes()
        if t.ip in self.corrupt_ips and data:
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        self.put(t.ip, folder, part, data)
        self.rates.append((t.ip, rate_mbps))
        return len(data)

    def finalize(self, t, folder, part, final, expected_sha256, overwrite):
        self._up(t, "finalize")
        files = self.files(t.ip, folder)
        if part not in files:
            return {"result": "missing", "sha256": None}
        got = hashlib.sha256(files[part]).hexdigest()
        if got != expected_sha256:
            del files[part]
            return {"result": "mismatch", "sha256": got}
        if final in files and not overwrite:
            del files[part]
            return {"result": "exists", "sha256": got}
        files[final] = files.pop(part)
        return {"result": "ok", "sha256": got}

    def remove_part(self, t, folder, part):
        self._up(t, "remove_part")
        if not part.endswith(pol.PART_SUFFIX):
            raise RunnerError("只能清掉我們自己的暫存檔")
        self.files(t.ip, folder).pop(part, None)

    def sha256(self, t, folder, name):
        self._up(t, "sha256")
        f = self.files(t.ip, folder)
        if name not in f:
            raise RunnerError("檔案不存在")
        return hashlib.sha256(f[name]).hexdigest()

    def list_dir(self, t, folder):
        self._up(t, "list_dir")
        if (t.ip, folder) in self.no_folder:
            raise RunnerError(f"沒有 {folder} 資料夾")
        return [{"name": n, "size": len(d), "mtime": "2026-10-29 10:00:00"} for n, d in sorted(self.files(t.ip, folder).items())
                if not n.endswith(pol.PART_SUFFIX)]

    def download(self, t, folder, name, local_path, rate_mbps, max_bytes, check=None):
        self._up(t, "download")
        f = self.files(t.ip, folder)
        if name not in f:
            raise RunnerError("檔案不存在")
        data = f[name]
        if t.ip in self.corrupt_ips and data:
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        if len(data) > max_bytes:
            raise RunnerError("檔案超過允許的大小，已停止接收")
        Path(local_path).write_bytes(data)
        return len(data)

    def run_command(self, t, folder, command, timeout=120):
        self._up(t, "run_command")
        if t.ip in self.command_fail:
            raise RunnerError(self.command_fail[t.ip])
        c = command.strip()
        if c.startswith("fail"):
            return {"rc": 1, "output": "fail: 指令失敗（示範）"}
        if c in ("pwd", "cd"):
            return {"rc": 0, "output": folder}
        if c == "hostname":
            return {"rc": 0, "output": t.hostname or t.ip}
        if c.split()[0] in ("ls", "dir"):
            return {"rc": 0, "output": "\n".join(sorted(self.files(t.ip, folder)))}
        return {"rc": 0, "output": f"ran: {c}"}


# ====================================================================== Ansible（正式用）
ANSIBLE_BIN_DEFAULT = "ansible-playbook"


def _default_base() -> Path:
    env = os.environ.get("WEBIT3_ANSIBLE_DIR", "").strip()
    return Path(env) if env else Path("/opt/webit3/data/ansible")


def ssh_hostkey_opts() -> list[str]:
    """SSH 主機金鑰選項：跟全專案同一份（accept-new＋明確的 known_hosts）。"""
    import onboard_engine
    return list(onboard_engine.SSH_HOSTKEY_OPTS)


def scp_safe_name(name: str) -> bool:
    """scp 會把遠端路徑交給遠端 shell／SFTP 解讀；為了不依賴跳脫，只對「英數、點、底線、連字號」的檔名走 scp。"""
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name or ""))


class AnsibleRunner(TransferRunner):
    """Linux／AIX 走 Ansible（raw）＋scp；Windows 走 transfer_winrm（PSRP，純文字腳本＋位元組陣列參數）。"""

    def __init__(self, base_dir: Path | None = None, ansible_bin: str | None = None, scp_bin: str = "scp",
                 run=subprocess.run, win=None, popen=subprocess.Popen, ssh_bin: str = "ssh", poll_s: float = 1.0):
        self.base = Path(base_dir) if base_dir else _default_base()
        self.ansible_bin = ansible_bin or os.environ.get("WEBIT3_ANSIBLE_BIN", ANSIBLE_BIN_DEFAULT)
        self.scp_bin = scp_bin
        self._run = run                      # 測試可換成假的 subprocess.run
        self._win = win                      # 測試可換成假的 Windows 傳輸
        self._popen = popen                  # scp／ssh 檔案本體走 Popen（可輪詢閘門、可中途終止）；測試可換成假的
        self.ssh_bin = ssh_bin
        self._poll_s = poll_s                # 傳輸中問閘門的間隔（秒）

    @property
    def win(self):
        if self._win is None:
            import transfer_winrm
            self._win = transfer_winrm.PsrpTransport()
        return self._win

    # ---- 設定檔
    def config_text(self) -> str:
        import onboard_engine
        kh = onboard_engine.SSH_KNOWN_HOSTS
        ssh_args = " ".join(ssh_hostkey_opts() + ["-o", "ControlMaster=no", "-o", "BatchMode=yes",
                                                  "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20"])
        return (
            "[defaults]\n"
            "host_key_checking = True\n"
            "retry_files_enabled = False\n"
            f"local_tmp = {self.base / 'tmp'}\n"
            "timeout = 30\n"
            "forks = 1\n"
            "nocolor = True\n"
            "deprecation_warnings = False\n"
            "interpreter_python = auto_silent\n"
            "[ssh_connection]\n"
            f"ssh_args = {ssh_args}\n"
            "pipelining = False\n"
            f"# known_hosts: {kh}\n"
        )

    def ensure_config(self) -> Path:
        for d in ("", "tmp", "run", "home"):
            (self.base / d).mkdir(parents=True, exist_ok=True)
        cfg = self.base / "ansible.cfg"
        text = self.config_text()
        if not cfg.exists() or cfg.read_text(encoding="utf-8") != text:
            cfg.write_text(text, encoding="utf-8", newline="\n")
        return cfg

    # ---- inventory／playbook（只有 Linux／AIX）
    def inventory(self, t: Target) -> dict:
        if t.is_windows:
            raise RunnerError("Windows 不走 Ansible")
        return {"all": {"hosts": {"target": {"ansible_host": t.ip, "ansible_connection": "ssh",
                                             "ansible_user": t.username, "ansible_ssh_private_key_file": t.key_path}}}}

    @staticmethod
    def playbook(task: dict) -> list:
        """第一個 play 對目標機跑一個任務（失敗與連不上都不中止，結果留在 r）；
        第二個 play 在本機把結果寫成 JSON 檔。"""
        task = {**task, "register": "r", "failed_when": False, "changed_when": False, "ignore_unreachable": True}
        return [
            {"hosts": "target", "gather_facts": False, "tasks": [task]},
            {"hosts": "localhost", "connection": "local", "gather_facts": False,
             "tasks": [{"ansible.builtin.copy": {
                 "content": "{{ hostvars['target']['r'] | default({}) | to_json }}", "dest": "{{ out_file }}"}}]},
        ]

    def _scrub(self, text: str, t: Target, extra: tuple = ()) -> str:
        """遮掉密碼，以及這次送出去的腳本／指令文字（Ansible 的錯誤尾巴會整段回顯任務參數）。"""
        out = text or ""
        if t.password:
            out = out.replace(t.password, "●●●●●●")
        for s in sorted((x for x in extra if x and len(x) >= 3), key=len, reverse=True):
            out = out.replace(s, "[指令已遮蔽]")
            for line in s.splitlines():                      # 回顯時換行常被改成字面值或拆行：逐行也遮
                if len(line.strip()) >= 6:
                    out = out.replace(line.strip(), "[指令已遮蔽]")
        return out

    def _exec(self, t: Target, task: dict, extra: dict, timeout_s: int) -> dict:
        cfg = self.ensure_config()
        work = Path(tempfile.mkdtemp(prefix="run_", dir=self.base / "run"))
        secrets = tuple(v for v in extra.values() if isinstance(v, str))
        try:
            try:
                os.chmod(work, 0o700)
            except OSError:
                pass
            out_file = work / "out.json"
            (work / "inventory.json").write_text(json.dumps(self.inventory(t)), encoding="utf-8")
            (work / "play.json").write_text(json.dumps(self.playbook(task)), encoding="utf-8")
            vars_ = {k: ({"__ansible_unsafe": v} if isinstance(v, str) else v) for k, v in extra.items()}
            vars_["out_file"] = str(out_file)
            (work / "vars.json").write_text(json.dumps(vars_), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")}
            env.update({"ANSIBLE_CONFIG": str(cfg), "HOME": str(self.base / "home"), "ANSIBLE_NOCOLOR": "1",
                        "ANSIBLE_HOST_KEY_CHECKING": "True"})
            cmd = [self.ansible_bin, "-i", str(work / "inventory.json"), str(work / "play.json"),
                   "-e", "@" + str(work / "vars.json")]
            try:
                proc = self._run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=timeout_s + 60, env=env, cwd=str(work))
            except FileNotFoundError as exc:
                raise RunnerError("這台伺服器找不到 ansible-playbook，請確認 Ansible 已安裝") from exc
            except subprocess.TimeoutExpired as exc:
                raise RunnerError(f"執行逾時（{timeout_s} 秒）") from exc
            if not out_file.exists():
                tail = self._scrub((proc.stdout or "") + "\n" + (proc.stderr or ""), t, secrets).strip()[-600:]
                raise RunnerError(f"Ansible 沒有產生結果（離開碼 {proc.returncode}）：{tail}")
            try:
                r = json.loads(out_file.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise RunnerError("Ansible 結果檔不是 JSON") from exc
            if r.get("unreachable"):
                raise RunnerError("連不上：" + self._scrub(str(r.get("msg") or ""), t, secrets)[:300])
            r["_secrets"] = secrets
            return r
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ---- 跑腳本
    def _win_text(self, t: Target, script: str, timeout_s: int = 120) -> tuple[str, list[str]]:
        import transfer_winrm as wt
        try:
            return wt.run_text(self.win, t, script, timeout_s, timeout_s)
        except wt.WinTransportError as exc:
            raise RunnerError(self._scrub(str(exc), t, (script,))) from exc

    def _script(self, t: Target, script: str, timeout_s: int = 120) -> dict:
        """Linux／AIX：Ansible raw。"""
        task = {"name": "script", "ansible.builtin.raw": "{{ script }}", "args": {"executable": "/bin/sh"}}
        r = self._exec(t, task, {"script": script}, timeout_s)
        out = (r.get("stdout") or "")
        if sc.VMWARE_MARK in out:
            raise VmwareRefused(VMWARE_MESSAGE)
        if r.get("failed") and not out.strip():
            raise RunnerError(self._scrub(str(r.get("msg") or r.get("stderr") or "執行失敗"), t, r["_secrets"])[:300])
        return {"rc": r.get("rc"), "stdout": out, "stderr": r.get("stderr") or ""}

    def _kv(self, t: Target, script: str, timeout_s: int = 120) -> dict:
        if t.is_windows:
            text, errors = self._win_text(t, script, timeout_s)
            r = {"stdout": text, "stderr": "；".join(errors)}
        else:
            r = self._script(t, script, timeout_s)
        kv = sc.parse_kv(r["stdout"])
        if kv["vmware"]:
            raise VmwareRefused(VMWARE_MESSAGE)
        if not kv["complete"]:
            raise RunnerError("遠端腳本沒有跑完（沒收到結束標記）：" + self._scrub((r["stdout"] + r["stderr"]).strip(), t, (script,))[-300:])
        return kv

    # ---- 介面實作
    def probe(self, t, folder, name=None):
        kv = self._kv(t, sc.ps_probe(folder, name) if t.is_windows else sc.sh_probe(folder, name), 90)
        free = kv.get("FREE_KB") if not t.is_windows else kv.get("FREE")
        fb = None
        if free and free.isdigit():
            fb = int(free) * (1024 if not t.is_windows else 1)
        return {"dir_exists": kv.get("DIR") == "1", "free_bytes": fb, "exists": kv.get("EXIST") == "1",
                "size": int(kv["SIZE"]) if (kv.get("SIZE") or "").isdigit() else None,
                "mtime": kv.get("MTIME") or None, "symlink": kv.get("SYMLINK") == "1"}

    def part_free(self, t, folder, part):
        kv = self._kv(t, sc.ps_part_free(folder, part) if t.is_windows else sc.sh_part_free(folder, part), 60)
        return kv.get("PARTBUSY") == "0"

    def upload(self, t, local_path, folder, part, rate_mbps, check=None):
        size = Path(local_path).stat().st_size
        timeout_s = int(size / (1 * pol.MB)) + 600          # 保守：至少 1 MB/s 也要跑得完
        if t.is_windows:
            import transfer_winrm as wt
            try:
                return wt.upload_file(self.win, t, local_path, f"{folder}\\{part}", None, check)
            except wt.GateStopped as exc:
                raise GateStopped(str(exc)) from exc
            except wt.WinTransportError as exc:
                raise RunnerError(self._scrub(str(exc), t)) from exc
        if check is not None:                                # 開始前問一次；傳輸中由 _scp 每約 1 秒再問，傳完（改名前）呼叫端還會再問
            why = check()
            if why:
                raise GateStopped(f"已停止：{why}")
        return self._scp(t, ["-q", str(local_path), f"{t.username}@{t.ip}:{folder}/{part}"], rate_mbps, timeout_s, size, check)

    def finalize(self, t, folder, part, final, expected_sha256, overwrite):
        script = (sc.ps_finalize if t.is_windows else sc.sh_finalize)(folder, part, final, expected_sha256, overwrite)
        kv = self._kv(t, script, 1800)
        return {"result": kv.get("RESULT") or "unknown", "sha256": sc.hash_of(kv.get("SHA256"))}

    def remove_part(self, t, folder, part):
        self._kv(t, sc.ps_remove_part(folder, part) if t.is_windows else sc.sh_remove_part(folder, part), 60)

    def sha256(self, t, folder, name):
        kv = self._kv(t, sc.ps_sha256(folder, name) if t.is_windows else sc.sh_sha256(folder, name), 1800)
        if kv.get("MISSING") == "1":
            raise RunnerError("檔案不存在")
        if kv.get("SYMLINK") == "1":
            raise RunnerError("這是符號連結，不處理")
        h = sc.hash_of(kv.get("SHA256"))
        if not h:
            raise RunnerError("目標機算不出 SHA256（沒有 sha256sum／csum／openssl）")
        return h

    def list_dir(self, t, folder):
        kv = self._kv(t, sc.ps_list(folder) if t.is_windows else sc.sh_list(folder), 90)
        if kv.get("DIR") != "1":
            raise RunnerError(f"沒有 {folder} 資料夾")
        return [f for f in kv["files"] if not f["name"].endswith(pol.PART_SUFFIX)]

    def download(self, t, folder, name, local_path, rate_mbps, max_bytes, check=None):
        if t.is_windows:
            import transfer_winrm as wt
            try:
                return wt.download_file(self.win, t, f"{folder}\\{name}", local_path, max_bytes)
            except wt.WinTransportError as exc:
                Path(local_path).unlink(missing_ok=True)
                raise RunnerError(self._scrub(str(exc), t)) from exc
        if not scp_safe_name(name):
            raise RunnerError("檔名含空白或特殊字元，抓回只支援英數、點、底線、連字號的檔名")
        got = self._capped_get(t, folder, name, Path(local_path), rate_mbps, max_bytes, 3600, check)
        if got > max_bytes:                                  # 保險：接收途中已硬性截斷，這裡是第二道
            Path(local_path).unlink(missing_ok=True)
            raise RunnerError("抓回的檔案超過允許的大小，已刪除")
        return got

    def run_command(self, t, folder, command, timeout=120):
        if t.is_windows:
            # 工作資料夾不存在（例如沒有 D 槽）就退回 C:\Windows\Temp，不要讓每道指令都多一行「找不到路徑」
            text, errors = self._win_text(
                t, f"if (Test-Path -LiteralPath '{folder}') {{ Set-Location -LiteralPath '{folder}' }} "
                   f"else {{ Set-Location -LiteralPath 'C:\\Windows\\Temp' }}\n{command}\n", timeout)
            out = (text + (("\n" + "\n".join(errors)) if errors else "")).strip("\n")
            return {"rc": 1 if errors else 0, "output": self._scrub(out, t, (command,))}
        r = self._script(t, sc.sh_command(folder, command), timeout)
        return {"rc": r["rc"], "output": self._scrub((r["stdout"] + (("\n" + r["stderr"]) if r["stderr"] else "")).strip("\n"),
                                                     t, (command,))}

    # ---- scp（Linux／AIX 檔案本體）
    def scp_command(self, t: Target, args: list[str], rate_mbps: int | None) -> list[str]:
        cmd = [self.scp_bin, "-B", "-P", "22", "-i", t.key_path, *ssh_hostkey_opts(),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20"]
        if rate_mbps:
            cmd += ["-l", str(int(rate_mbps) * 8 * 1024)]           # scp 的 -l 單位是 Kbit/s
        return cmd + args

    def _proc_env(self) -> dict:
        return {**{k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")},
                "HOME": str(self.base / "home")}

    @staticmethod
    def _stop_proc(proc) -> None:
        """先 terminate，寬限幾秒還不走就 kill。"""
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - 行程已經結束或收不掉：不蓋過原本要回報的停止原因
            pass

    def _scp(self, t: Target, args: list[str], rate_mbps, timeout_s: int, size: int, check=None) -> int:
        """scp 是我們自己開的行程：用 Popen 開，每 _poll_s 秒問一次閘門，閘門關了就終止行程（審查 F2）。"""
        cmd = self.scp_command(t, args, rate_mbps)
        try:
            with tempfile.TemporaryFile() as errf:             # stderr 寫暫存檔，不用管線，免得輸出塞滿卡住行程
                try:
                    proc = self._popen(cmd, stdout=subprocess.DEVNULL, stderr=errf, env=self._proc_env())
                except FileNotFoundError as exc:
                    raise RunnerError("這台伺服器找不到 scp") from exc
                deadline = time.monotonic() + timeout_s
                try:
                    while True:
                        try:
                            rc = proc.wait(timeout=self._poll_s)
                            break
                        except subprocess.TimeoutExpired:
                            pass
                        if time.monotonic() > deadline:
                            self._stop_proc(proc)
                            raise RunnerError(f"傳送逾時（{timeout_s} 秒）")
                        why = check() if check is not None else None
                        if why:
                            self._stop_proc(proc)
                            raise GateStopped(f"已停止：{why}")
                except BaseException:
                    if proc.poll() is None:                      # 問閘門本身出錯等：行程不能留著繼續跑
                        self._stop_proc(proc)
                    raise
                errf.seek(0)
                err = errf.read().decode("utf-8", errors="replace")
        except OSError as exc:
            raise RunnerError(f"scp 無法執行：{type(exc).__name__}") from exc
        if rc != 0:
            raise RunnerError("scp 失敗：" + self._scrub(err.strip(), t)[-300:])
        return size

    #: 抓回前置檢查的離開碼 → 白話原因（遠端指令用這幾個固定碼回報，不靠解析 stderr）
    _GET_REFUSE = {10: "主機上找不到這個檔案", 11: "這是符號連結，不處理", 12: "這不是一般檔案（可能是資料夾、FIFO 或裝置檔），不處理",
                   13: "沒有讀取這個檔案的權限"}

    def _capped_get(self, t: Target, folder: str, name: str, local_path: Path, rate_mbps, cap: int, timeout_s: int,
                    check=None) -> int:
        """Linux／AIX 抓回：用 ssh 跑 dd 讀「最多 cap+1 位元組」串流進本機檔，累計超過 cap 就中止並刪檔（審查 F1）。
        遠端檔在探測後被撐大也寫不爆本機磁碟。dd（不是 head -c）是因為 AIX 的 head 沒有 -c。
        讀取在另一條執行緒做、這條執行緒每 _poll_s 秒看一次逾時與閘門（M1）：遠端卡住（FIFO、掛住的 NFS）也叫得停。"""
        remote = shlex.quote(f"{folder.rstrip('/')}/{name}")
        blocks = cap // 65536 + 2                              # 讀到的位元組數 > cap+1，多出來的在本機計數處就中止
        rcmd = (f"test -e {remote} || exit 10; test -L {remote} && exit 11; test -f {remote} || exit 12; "
                f"test -r {remote} || exit 13; dd if={remote} bs=65536 count={blocks} 2>/dev/null")
        cmd = [self.ssh_bin, "-T", "-p", "22", "-i", t.key_path, *ssh_hostkey_opts(),
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", f"{t.username}@{t.ip}", rcmd]
        total = 0
        q: queue.Queue = queue.Queue(maxsize=4)
        try:
            with tempfile.TemporaryFile() as errf:
                try:
                    proc = self._popen(cmd, stdout=subprocess.PIPE, stderr=errf, env=self._proc_env())
                except FileNotFoundError as exc:
                    raise RunnerError("這台伺服器找不到 ssh") from exc

                def pump():
                    try:
                        while True:
                            chunk = proc.stdout.read(65536)
                            q.put(chunk)
                            if not chunk:
                                return
                    except Exception:  # noqa: BLE001 - 管線被關掉／行程被終止：當成串流結束，離開碼另外判斷
                        try:
                            q.put(b"", timeout=1)
                        except queue.Full:
                            pass
                reader = threading.Thread(target=pump, daemon=True, name="ft-get-reader")
                reader.start()
                started = time.monotonic()
                try:
                    with open(local_path, "wb") as out:
                        while True:
                            try:
                                chunk = q.get(timeout=self._poll_s)
                            except queue.Empty:
                                chunk = None
                            if time.monotonic() - started > timeout_s:
                                raise RunnerError(f"傳送逾時（{timeout_s} 秒），已中止並刪除")
                            why = check() if check is not None else None
                            if why:
                                raise GateStopped(f"已停止：{why}")
                            if chunk is None:
                                continue                     # 還沒有資料：回頭再看逾時與閘門
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > cap:
                                raise RunnerError("抓回的檔案超過允許的大小（接收途中已中止），已刪除")
                            out.write(chunk)
                            if rate_mbps:                      # 本機端限速：讀得比設定快就睡一下
                                ahead = total / (int(rate_mbps) * pol.MB) - (time.monotonic() - started)
                                if ahead > 0:
                                    time.sleep(min(ahead, 2.0))
                    try:
                        rc = proc.wait(timeout=30)
                    except subprocess.TimeoutExpired as exc:
                        raise RunnerError("ssh 讀完資料後沒有結束") from exc
                except BaseException:
                    self._stop_proc(proc)
                    Path(local_path).unlink(missing_ok=True)
                    raise
                finally:
                    try:
                        proc.stdout.close()
                    except Exception:  # noqa: BLE001 - 已關閉或假物件沒有 close：不影響結果
                        pass
                    reader.join(timeout=2)
                errf.seek(0)
                err = errf.read().decode("utf-8", errors="replace")
        except OSError as exc:
            Path(local_path).unlink(missing_ok=True)
            raise RunnerError(f"ssh 無法執行：{type(exc).__name__}") from exc
        if rc != 0:
            Path(local_path).unlink(missing_ok=True)
            reason = self._GET_REFUSE.get(rc)
            if reason:
                raise RunnerError("抓回被拒絕：" + reason)
            tail = self._scrub(err.strip(), t)[-200:]
            raise RunnerError(f"抓回失敗（離開碼 {rc}）" + (f"：{tail}" if tail else "：遠端沒有提供原因，可能是連線中斷或讀取時出錯"))
        return total


# ---------------------------------------------------------------------- 全域取用
_runner: TransferRunner | None = None


def get_runner() -> TransferRunner:
    global _runner
    if _runner is None:
        _runner = AnsibleRunner()
    return _runner


def set_runner(r: TransferRunner | None) -> None:
    """測試用：換成 FakeRunner。傳 None 還原成預設（Ansible）。"""
    global _runner
    _runner = r
