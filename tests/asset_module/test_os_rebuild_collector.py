"""os_rebuild_collector：OS 重建資料收集。

全部用假主機（fake runner）注入假的指令輸出，**不連任何真機**。重點：
  * 各類都解析得出
  * 祕密欄位被遮／不收
  * 單檔超大被截斷
  * 指令不存在標「未收集（沒有指令）」、沒跑過標 not_run，不當成空白
  * sudo 被拒標「權限不足」，**不是**「此機未使用」
  * 靜態：sudo 那段不得出現 sh -c／bash -c／| ／; ／> 等 shell 語法
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db as D                    # noqa: E402
import os_rebuild_collector as R  # noqa: E402

DENIED_MSG = "sudo: a password is required"
ALL_TOOLS = R._PROBE_TOOLS.split()
ALL_BINS = R._PROBE_BINS.split()
HELPER = "/usr/local/libexec/webit3/account-facts"


class FakeHost:
    """模擬一台 Linux：非 root 指令依 NONROOT 鍵給輸出；sudo 指令依完整字串給輸出。

    `sudo -n -l` 回放行清單：預設就是 `sudo` 字典裡那幾條（照字面），外加 `rules`
    （例如萬用字元規則）。`list_denied=True` 模擬帳號完全沒有 sudo 權限。
    清單有列、但真的執行時不在 `sudo` 字典的 → 回 `a password is required`，
    跟真機 `sudo -n` 被拒一樣。每次呼叫都設 `.last = {rc, err}`（同 SSHRunner）。
    """

    def __init__(self, nonroot=None, sudo=None, tools=ALL_TOOLS, bins=ALL_BINS, dirs=(),
                 with_last=True, rules=(), list_denied=False):
        self.nonroot = dict(nonroot or {})
        self.sudo = dict(sudo or {})
        probe = [f"TOOL {t}" for t in tools] + [f"BIN {b}" for b in bins] + [f"DIR {d}" for d in dirs]
        self.nonroot.setdefault("probe", "\n".join(probe))
        self.rev = {R.NONROOT[k]: v for k, v in self.nonroot.items()}
        self.rules = [c[len("sudo -n "):] for c in self.sudo] + list(rules)
        self.list_denied = list_denied
        self.sudo_calls: list[str] = []
        self.with_last = with_last
        self.last = None

    def _listing(self):
        # 真的 sudo -l 會把參數裡的逗號印成 `\,`（sudoers 裡也必須這樣寫）
        # 2026-10-18 起照真機印法：`"` `,` `=` 都加反斜線（`""` 印成 \"\"）。以前只跳脫逗號，
        # 所以「`""` 規則被判成沒放行」的 bug 測試抓不到。
        body = "\n".join(f"    (root) NOPASSWD: {_sudo_l_escape(r)}" for r in self.rules)
        return ("Matching Defaults entries for webit3scan on h:\n    !visiblepw\n\n"
                f"User webit3scan may run the following commands on h:\n{body}\n")

    def __call__(self, host, cmd):
        if cmd == R.SUDO_LIST_CMD:
            self.sudo_calls.append(cmd)
            o, e, rc = ("", DENIED_MSG, 1) if self.list_denied else (self._listing(), "", 0)
        elif cmd.startswith("sudo "):
            self.sudo_calls.append(cmd)
            if cmd in self.sudo:
                o, e, rc = self.sudo[cmd], "", 0
            else:
                o, e, rc = "", DENIED_MSG, 1
        else:
            o, e, rc = self.rev.get(cmd, ""), "", 0
        if self.with_last:
            self.last = {"rc": rc, "err": e}
        return o


def _sudo_l_escape(rule: str) -> str:
    """sudoers 規則 → sudo -l 印出來的樣子（真機實測：`"` `,` `=` 前加反斜線）。"""
    return re.sub(r'([",=])', lambda m: chr(92) + m.group(1), rule)


def S(*argv):
    return "sudo -n " + " ".join(argv)


def _items(fake):
    res = R.collect_rebuild(fake, "10.99.0.5", "linux")
    return {it["id"]: it for g in res["data"]["groups"] for it in g["items"]}, res


LVM_OK = {
    S("/usr/sbin/pvs", *R.SUDO_FIXED_ARGS["pvs"]): "  /dev/sda2 rootvg lvm2 a-- 199.00g 0 pvuuid-1",
    S("/usr/sbin/vgs", *R.SUDO_FIXED_ARGS["vgs"]): "  rootvg 1 3 wz--n- 199.00g 0 4.00m vguuid-1",
    S("/usr/sbin/lvs", *R.SUDO_FIXED_ARGS["lvs"]): "  root rootvg -wi-ao---- 50.00g 1 linear 1 /dev/sda2(0) lvuuid-1",
    S("/usr/bin/cat", "/etc/lvm/backup/rootvg"): "rootvg {\n  id = \"abc\"\n  extent_size = 8192\n}",
}


# ---- 各類都解析得出 ----
def test_lvm_and_metadata_via_fixed_sudo():
    items, _ = _items(FakeHost(sudo=LVM_OK))
    it = items["storage.pv_vg_lv"]
    assert it["state"] == "collected"
    assert it["vgs"][0]["vg"] == "rootvg" and it["vgs"][0]["ext_size"] == "4.00m"
    assert it["lvs"][0]["lv"] == "root" and it["pvs"][0]["pv"] == "/dev/sda2"
    assert items["storage.vg_pp_mirror"]["state"] == "collected"
    md = items["storage.lvm_metadata"]
    assert md["state"] == "collected" and md["files"][0]["path"] == "/etc/lvm/backup/rootvg"


def test_fstab_blkid_findmnt():
    fake = FakeHost(nonroot={"fstab": "@@FILE /etc/fstab\nUUID=abc / ext4 defaults 0 1\n@@END",
                             "findmnt": "/ /dev/sda1 ext4 rw,relatime"},
                    sudo={S("/usr/sbin/blkid"): '/dev/sda1: UUID="abc" TYPE="ext4"'})
    items, _ = _items(fake)
    assert items["storage.fstab"]["files"][0]["path"] == "/etc/fstab"
    assert items["storage.blkid"]["state"] == "collected" and "abc" in items["storage.blkid"]["text"]
    assert "ext4" in items["storage.filesystems"]["text"]


def test_blkid_falls_back_to_nonroot_cache_with_note():
    items, _ = _items(FakeHost(nonroot={"blkid_user": '/dev/sda1: UUID="abc"'}))
    it = items["storage.blkid"]
    assert it["state"] == "collected" and "權限不足" in it["note"]


def test_groups_and_gid_dup():
    items, _ = _items(FakeHost(nonroot={"groups": "root:x:0:\nwheel:x:10:alice,bob\ndup1:x:10:carol"}))
    assert {x["name"] for x in items["acct.groups"]["groups"]} == {"root", "wheel", "dup1"}
    dups = items["acct.gid_dups"]["gid_dups"]
    assert dups[0]["gid"] == 10 and set(dups[0]["names"]) == {"wheel", "dup1"}


def test_enabled_and_running_not_enabled_cross():
    items, _ = _items(FakeHost(nonroot={
        "units_enabled": "sshd.service enabled\nchronyd.service enabled",
        "units_running": "sshd.service loaded active running SSH\ndocker.service loaded active running D\n"}))
    assert items["boot.running_not_enabled"]["running_not_enabled"] == ["docker.service"]


def test_authkeys_fingerprint_only_for_users_with_keys():
    helper_out = ("=== SHADOW\nACCT root :: root P :: x\n=== SUDOERS\nroot ALL=(ALL) ALL\n"
                  "=== KEYS\nKEYS root 2\nKEYS alice 0\n=== END\n")
    fake = FakeHost(
        nonroot={"passwd": "root:x:0:0::/root:/bin/bash\nalice:x:1000:1000::/home/alice:/bin/bash\n"
                           "daemon:x:2:2::/sbin:/sbin/nologin"},
        sudo={S(HELPER): helper_out,
              S("/usr/bin/ssh-keygen", "-lf", "/root/.ssh/authorized_keys"):
                  "2048 SHA256:AbCd1234efgh ops@jump (RSA)\n256 SHA256:ZzZz9999 backup@nas (ED25519)"})
    items, _ = _items(fake)
    keys = items["acct.authkeys"]["keys"]
    assert len(keys) == 2 and keys[0]["fingerprint"] == "SHA256:AbCd1234efgh"
    assert all("AAAA" not in str(k) for k in keys)
    # alice 輔助程式說 0 把 key → 不去問；nologin 的 daemon 也不問
    assert not any("alice" in c or "/sbin/.ssh" in c for c in fake.sudo_calls)


def test_timezone_and_ntp_sources():
    items, _ = _items(FakeHost(nonroot={
        "timezone": "Timezone=Asia/Taipei\n@@TZ\n@@LOCALTIME\n/usr/share/zoneinfo/Asia/Taipei",
        "ntp": ("@@SOURCES\n^* 10.99.0.1  2  6  377  10  +1us\n@@CONF\n"
                "@@FILE /etc/chrony.conf\nserver 10.99.0.1 iburst\n@@END")}))
    assert items["time.timezone"]["timezone"] == "Asia/Taipei"
    assert "10.99.0.1" in items["time.ntp"]["chrony_live_sources"]


def test_swap_and_routes():
    items, _ = _items(FakeHost(nonroot={
        "swapon": "NAME TYPE SIZE\n/dev/dm-1 partition 8G\n@@PROC\nFilename Type Size Used Priority\n/dev/dm-1 partition 8388604 0 -2",
        "routes": "default via 10.99.0.1 dev eth0\n@@PERSIST\n@@FILE /etc/sysconfig/network-scripts/route-eth0\n10.99.1.0/24 via 10.99.0.254\n@@END"}))
    assert items["storage.swap"]["entries"][0]["name"] == "/dev/dm-1"
    assert items["net.routes"]["persist_files"][0]["path"].endswith("route-eth0")


def test_root_only_dir_listed_then_read_per_file():
    """使用者 crontab：先 sudo ls 固定目錄，再逐檔 sudo cat（每條都沒有萬用字元）。"""
    fake = FakeHost(dirs=("/var/spool/cron",), sudo={
        S("/usr/bin/ls", "-1", "/var/spool/cron"): "root\noracle\n.hidden\nbad name",
        S("/usr/bin/cat", "/var/spool/cron/root"): "0 1 * * * /opt/backup.sh",
        S("/usr/bin/cat", "/var/spool/cron/oracle"): "30 2 * * * /u01/rman.sh"})
    items, _ = _items(fake)
    cu = items["cron.user"]
    assert cu["state"] == "collected" and {f["path"] for f in cu["files"]} == {
        "/var/spool/cron/root", "/var/spool/cron/oracle"}
    # 不合規的檔名（隱藏檔、含空白）不可以被組進 sudo 指令
    assert not any(".hidden" in c or "bad" in c for c in fake.sudo_calls)


# ---- 祕密遮蔽 ----
def test_secret_masking_sudoers_nm_snmp():
    helper_out = ("=== SHADOW\nACCT root :: x\n=== SUDOERS\napp ALL=(ALL) NOPASSWD: /bin/systemctl\n"
                  "Defaults passwd=SuperSecret1\n=== KEYS\n=== END\n")
    fake = FakeHost(
        nonroot={"syslog": "@@FILE /etc/rsyslog.d/snmp.conf\nrocommunity SeCrEtCommunity 10.99.0.0/16\n@@END"},
        dirs=("/etc/NetworkManager/system-connections",),
        sudo={S(HELPER): helper_out,
              S("/usr/bin/ls", "-1", "/etc/NetworkManager/system-connections"): "wifi.nmconnection",
              S("/usr/bin/cat", "/etc/NetworkManager/system-connections/wifi.nmconnection"):
                  "[wifi-security]\npsk=mywifipass\n"})
    items, res = _items(fake)
    sud = items["acct.sudoers"]["text"]
    assert "SuperSecret1" not in sud and "[已遮蔽]" in sud and "NOPASSWD" in sud
    assert "ACCT root" not in sud                         # SHADOW 段不留
    nm = [f for f in items["net.ifcfg"]["files"] if "wifi" in f["path"]][0]["content"]
    assert "mywifipass" not in nm and "[已遮蔽]" in nm
    assert "SeCrEtCommunity" not in items["time.syslog"]["files"][0]["content"]
    blob = json.dumps(res["raw"], ensure_ascii=False)
    assert "SuperSecret1" not in blob and "mywifipass" not in blob and "ACCT root" not in blob


def test_nsswitch_not_over_masked():
    items, _ = _items(FakeHost(nonroot={"nsswitch": "passwd: files sss\nshadow: files\nhosts: files dns"}))
    txt = items["net.nsswitch"]["text"]
    assert "passwd: files sss" in txt and "[已遮蔽]" not in txt


def test_private_key_block_removed():
    # 拼接避免原始碼出現完整私鑰標頭字面值（會被祕密掃描器誤判）
    beg, end = "-----BEGIN RSA " + "PRIVATE KEY-----", "-----END RSA " + "PRIVATE KEY-----"
    items, _ = _items(FakeHost(nonroot={
        "ifcfg": f"@@FILE /etc/network/interfaces\n{beg}\nMIIabc...\n{end}\n@@END"}))
    txt = items["net.ifcfg"]["files"][0]["content"]
    assert "MIIabc" not in txt and "私鑰本體已移除" in txt


# ---- 截斷 ----
def test_oversized_file_truncated_nonroot_and_sudo():
    huge = "x" * (R._FILE_CAP + 5000)
    fake = FakeHost(nonroot={"fstab": f"@@FILE /etc/fstab\n{huge}\n@@END"},
                    sudo={**LVM_OK, S("/usr/bin/cat", "/etc/lvm/backup/rootvg"): huge})
    items, _ = _items(fake)
    f = items["storage.fstab"]["files"][0]
    assert f["truncated"] is True and "已截斷" in f["content"]
    md = items["storage.lvm_metadata"]["files"][0]
    assert md["truncated"] is True and len(md["content"].encode()) < R._FILE_CAP + 200


# ---- 權限不足 ≠ 此機未使用 ----
def test_not_whitelisted_is_permission_denied_and_never_sent():
    """白名單沒放行 → 權限不足（不可以標成此機未使用），而且**根本不送**：
    除了 sudo -l 之外零次 sudo 呼叫，目標機不會留下一串「command not allowed」紀錄。"""
    fake = FakeHost()                      # sudo -l 有回，但一條都沒放行
    items, res = _items(fake)
    for iid in ("storage.pv_vg_lv", "storage.lvm_metadata", "acct.sudoers", "storage.blkid"):
        assert items[iid]["state"] == "denied", iid
        assert items[iid]["label"] == "權限不足（sudo 被拒）"
    assert "sudo vgs" in items["storage.lvm_metadata"]["denied_paths"]
    # 每一個權限不足的項目都要講得出「是哪一條被擋」
    for iid in ("storage.pv_vg_lv", "storage.lvm_metadata", "acct.sudoers", "storage.blkid"):
        assert items[iid].get("denied_paths"), iid
    assert fake.sudo_calls == [R.SUDO_LIST_CMD]
    assert res["data"]["summary"]["sudo_list"] == "ok"


def test_sudo_list_itself_denied_means_no_sudo_at_all():
    fake = FakeHost(list_denied=True)
    items, res = _items(fake)
    assert items["acct.sudoers"]["state"] == "denied"
    assert fake.sudo_calls == [R.SUDO_LIST_CMD]
    assert res["data"]["summary"]["sudo_list"] == "denied"


def test_listed_but_execution_denied_detected_from_stderr():
    """sudo -l 有列、實際執行卻被拒（例如規則剛被收回）→ 從 stderr 認出來，標權限不足。"""
    fake = FakeHost(rules=[f'{HELPER} ""'])
    items, _ = _items(fake)
    assert S(HELPER) in fake.sudo_calls
    assert items["acct.sudoers"]["state"] == "denied"


def test_wildcard_rule_matching_follows_sudoers_semantics():
    specs = ["/usr/sbin/vgs *", '/usr/local/libexec/webit3/account-facts ""',
             "/usr/bin/cat /var/spool/cron/*", "/usr/sbin/dmidecode -t *"]
    assert R.sudo_allows(specs, ["/usr/sbin/vgs", "--noheadings", "-o", "vg_name"])
    assert R.sudo_allows(specs, [HELPER])
    assert not R.sudo_allows(specs, [HELPER, "x"])                 # "" ＝不准任何參數
    assert R.sudo_allows(specs, ["/usr/bin/cat", "/var/spool/cron/root"])
    assert not R.sudo_allows(specs, ["/usr/bin/cat", "/etc/fstab"])
    assert not R.sudo_allows(specs, ["/usr/sbin/pvs", "-o", "pv_name"])
    assert not R.sudo_allows(["ALL", "!/usr/bin/cat *"], ["/usr/bin/cat", "/etc/fstab"])


def test_parse_sudo_list_handles_escaped_comma_and_runas():
    text = ("User webit3scan may run the following commands on h:\n"
            "    (root) NOPASSWD: /usr/bin/dmesg --level=err\\,crit, /usr/sbin/ss -tlnp\n"
            "    (oracle) NOPASSWD: /usr/bin/cat *\n")
    assert R.parse_sudo_list(text) == ["/usr/bin/dmesg --level=err,crit", "/usr/sbin/ss -tlnp"]
    assert R.parse_sudo_list("sudo: a password is required") is None


def test_unreadable_file_without_cat_rule_is_file_permission_not_sudo():
    """非 root 讀不到（0600 netplan）、白名單沒有讀檔規則所以沒送 sudo → 權限不足，
    但原因講「檔案只給 root 讀」，不可說成 sudo 被拒（2026-10-18 公司機 A11 systemd unit 誤導）。"""
    fake = FakeHost(nonroot={"ifcfg": "@@NOREAD /etc/netplan/01-net.yaml"})
    items, _ = _items(fake)
    it = items["net.ifcfg"]
    assert it["state"] == "denied" and it["unreadable_paths"] == ["/etc/netplan/01-net.yaml"]
    assert not it.get("denied_paths")
    assert it["deny_kind"] == "file" and it["label"] == R.STATE_LABEL_FILE_DENIED
    assert "sudo" not in it["label"]
    assert not any("cat" in c for c in fake.sudo_calls)          # 沒有讀檔規則就不送


def test_unreadable_file_cat_sent_but_denied_is_sudo_denied():
    """有讀檔規則、真的送了 sudo cat 卻被擋 → 才是「sudo 被拒」。"""
    fake = FakeHost(nonroot={"ifcfg": "@@NOREAD /etc/netplan/01-net.yaml"},
                    rules=["/usr/bin/cat /etc/netplan/*"])
    it = _items(fake)[0]["net.ifcfg"]
    assert it["state"] == "denied" and it["denied_paths"] == ["/etc/netplan/01-net.yaml"]
    assert it.get("deny_kind") != "file" and it["label"] == "權限不足（sudo 被拒）"


def test_systemd_custom_unreadable_units_not_blamed_on_sudo():
    """A11 自訂 systemd unit：0600 的 unit 讀不到 → 列在「檔案只給 root 讀」，不列在 sudo 被拒。"""
    t = ("@@FILE /etc/systemd/system/app.service\n[Unit]\n@@END\n"
         "@@NOREAD /etc/systemd/system/app-secret.service\n@@LINKS\n")
    it = _items(FakeHost(nonroot={"systemd_custom": t}))[0]["boot.systemd_custom"]
    assert it["state"] == "collected"
    assert it["unreadable_paths"] == ["/etc/systemd/system/app-secret.service"]
    assert not it.get("denied_paths") and "sudo 被拒" not in it["note"]


# ---- v1.463：Docker／containerd unit 不收（2026-10-18 使用者：「Docker 可以整個不要」）----
_DOCKER_A11 = ("@@FILE /etc/systemd/system/app.service\n[Unit]\n@@END\n"
               "@@NOREAD /etc/systemd/system/docker.socket\n"
               "@@NOREAD /etc/systemd/system/docker.service\n"
               "@@NOREAD /etc/systemd/system/containerd.service\n"
               "@@NOREAD /etc/systemd/system/docker.service.d/override.conf\n"
               "@@LINKS\n/etc/systemd/system/multi-user.target.wants/docker.service -> /x\n"
               "/etc/systemd/system/multi-user.target.wants/sshd.service -> /y\n")


def test_docker_units_not_counted_as_denied():
    """舊版（v1.462.1）：公司機 A11 列 docker.socket／containerd.service／docker.service「只給 root 讀」。
    新版：這些不算讀到、也不算權限不足；其他 unit 讀得到 → 已收集、沒有權限不足清單。"""
    it = _items(FakeHost(nonroot={"systemd_custom": _DOCKER_A11}))[0]["boot.systemd_custom"]
    assert it["state"] == "collected"
    assert not it.get("unreadable_paths") and not it.get("denied_paths") and not it.get("note")
    assert [f["path"] for f in it["files"]] == ["/etc/systemd/system/app.service"]
    assert it["links"] == ["/etc/systemd/system/multi-user.target.wants/sshd.service -> /y"]


def test_docker_only_host_is_empty_not_denied():
    t = ("@@NOREAD /etc/systemd/system/docker.socket\n@@NOREAD /etc/systemd/system/containerd.service\n"
         "@@LINKS\n/etc/systemd/system/sockets.target.wants/docker.socket -> /x\n")
    it = _items(FakeHost(nonroot={"systemd_custom": t}))[0]["boot.systemd_custom"]
    assert it["state"] == "empty" and it["label"] == R.STATE_LABEL["empty"]


def test_docker_exclusion_single_source_in_collect_command():
    """收集指令（find）由同一份 SYSTEMD_UNIT_EXCLUDE 產生，目標機上根本不讀 docker/containerd unit。"""
    cmd = R.NONROOT["systemd_custom"]
    for g in R.SYSTEMD_UNIT_EXCLUDE:
        assert cmd.count(f"! -path '*/{g}'") == 2, g     # 檔案迴圈＋連結清單各一次
    assert R.systemd_excluded("/etc/systemd/system/docker.socket")
    assert not R.systemd_excluded("/etc/systemd/system/sshd.service")


# ---- v1.463：repo 設定由唯讀輔助程式 yum-repos 收（redhat.repo 只給 root 讀）----
_REPO_NR = ("@@FILE /etc/yum.repos.d/rocky.repo\n[baseos]\nbaseurl=http://r/x\n@@END\n"
            "@@NOREAD /etc/yum.repos.d/redhat.repo\n")


def test_yum_repos_helper_collects_root_only_repo_and_masks():
    sub = R.REPO_HELPER_SUB
    body = ("[rhel-9-baseos]\nbaseurl=https://u:pw1@cdn.example/x\nsslclientkey=/etc/pki/entitlement/1-key.pem\n"
            "proxy_password=pw2\npassword=pw3\n")
    fake = _helper_host({sub: f"F /etc/yum.repos.d/redhat.repo OK {_b64(body)}\n"})
    fake.nonroot["repos"] = _REPO_NR
    fake.rev[R.NONROOT["repos"]] = _REPO_NR
    items, _ = _items(fake)
    it = items["time.repos"]
    assert S(R.REBUILD_HELPER_PATH, sub) in fake.sudo_calls
    assert it["state"] == "collected" and not it.get("unreadable_paths") and not it.get("denied_paths")
    red = next(f for f in it["files"] if f["path"] == "/etc/yum.repos.d/redhat.repo")["content"]
    assert "pw1" not in red and "pw2" not in red and "pw3" not in red
    assert "/etc/pki/entitlement/1-key.pem" in red          # 路徑留著（不是祕密）


def test_yum_repos_not_in_sudoers_says_reonboard_and_sends_nothing():
    """之前納管的主機（sudo -l 沒有 yum-repos）：不送（避免被拒紀錄），畫面講「需重新納管」不是 sudo 被拒。"""
    fake = _helper_host({"lvm-backup": ""}, allow=["lvm-backup"])
    nr = "@@NOREAD /etc/yum.repos.d/redhat.repo\n"
    fake.nonroot["repos"] = nr
    fake.rev[R.NONROOT["repos"]] = nr
    items, res = _items(fake)
    it = items["time.repos"]
    assert S(R.REBUILD_HELPER_PATH, R.REPO_HELPER_SUB) not in fake.sudo_calls
    assert it["state"] == "denied" and it["deny_kind"] == "reonboard"
    assert it["label"] == R.STATE_LABEL_REONBOARD and "sudo 被拒" not in it["label"]
    assert it["reonboard_paths"] == ["/etc/yum.repos.d/redhat.repo"]
    assert res["data"]["summary"]["denied_by_kind"].get("reonboard") == 1


def test_yum_repos_partial_reonboard_note_when_others_readable():
    fake = _helper_host({"lvm-backup": ""}, allow=["lvm-backup"])
    fake.nonroot["repos"] = _REPO_NR
    fake.rev[R.NONROOT["repos"]] = _REPO_NR
    it = _items(fake)[0]["time.repos"]
    assert it["state"] == "collected" and "重新納管" in it["note"]


def test_yum_repos_rule_deployed_from_single_source():
    spec = f"{R.REBUILD_HELPER_PATH} {R.REPO_HELPER_SUB}"
    assert spec in R.rebuild_sudoers_specs() and R.REPO_HELPER_SUB in R.HELPER_SUBCOMMANDS
    assert any(r["sudoers"] == spec and not r["wildcard"] for r in R.sudo_whitelist())
    import onboard_engine as oe
    assert spec in oe.linux_sudo_specs()                     # root 金鑰主機同一份白名單
    assert "*" not in spec
    body = R.REBUILD_HELPER_SCRIPT[R.REBUILD_HELPER_SCRIPT.index("  yum-repos)"):].split(";;")[0]
    assert "/etc/yum.repos.d .repo" in body and "$2" not in body   # 固定目錄、不吃呼叫端參數


def test_denied_label_by_kind_summary():
    """摘要「權限不足」要分得出原因（sudo 被拒／只給 root 讀／需重新納管）。"""
    t = "@@NOREAD /etc/systemd/system/app-secret.service\n@@LINKS\n"
    items, res = _items(FakeHost(nonroot={"systemd_custom": t}))
    assert items["boot.systemd_custom"]["label"] == R.STATE_LABEL_FILE_DENIED
    assert res["data"]["summary"]["denied_by_kind"].get("file", 0) >= 1


FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("name", ["sudo_l_rhel_en.txt", "sudo_l_rhel_zh_TW.txt"])
def test_parse_real_sudo_l_output_en_and_zh(name):
    r"""真機 `sudo -n -l` 原文（221 實測、主機名換成 host01）：英文（LC_ALL=C）與中文（zh_TW）都要認得，
    而且 \"\"、\,、\= 都要還原，否則 blkid／iptables-save／account-facts 永遠被判沒放行。"""
    specs = R.parse_sudo_list((FIX / name).read_text(encoding="utf-8"))
    assert specs is not None and len(specs) == 45
    assert '/usr/sbin/blkid ""' in specs and '/usr/sbin/iptables-save ""' in specs
    assert '/usr/local/libexec/webit3/account-facts ""' in specs
    assert "/usr/bin/dmesg --level=err,crit" in specs
    assert not any(chr(92) in s for s in specs)
    assert R.sudo_allows(specs, ["/usr/sbin/blkid"])
    assert R.sudo_allows(specs, ["/sbin/iptables-save"])
    assert R.sudo_allows(specs, [HELPER])
    assert not R.sudo_allows(specs, ["/usr/sbin/blkid", "/dev/sda"])     # "" ＝不准任何參數
    assert R.sudo_allows(specs, ["/usr/bin/dmesg", "--level=err,crit"])
    for tool in ("vgs", "lvs", "pvs"):
        assert R.sudo_allows(specs, [f"/usr/sbin/{tool}", *R.SUDO_FIXED_ARGS[tool]]), tool
    assert R.sudo_allows(specs, [R.REBUILD_HELPER_PATH, "lvm-backup"])
    assert not R.sudo_allows(specs, ["/usr/bin/cat", "/etc/shadow"])


def test_real_zh_sudo_l_host_still_sends_no_arg_items():
    """端到端：目標機回中文 sudo -l（不吃 LC_ALL 的保守情境）→ blkid／iptables-save／sudoers 照樣送、照樣收到。"""
    zh = (FIX / "sudo_l_rhel_zh_TW.txt").read_text(encoding="utf-8")
    fake = FakeHost(sudo={S("/usr/sbin/blkid"): '/dev/sda1: UUID="u1" TYPE="xfs"',
                          S("/usr/sbin/iptables-save"): "*filter\n:INPUT ACCEPT [0:0]\nCOMMIT",
                          S(HELPER): "@@SUDOERS\nroot ALL=(ALL) ALL\n@@END"})
    fake._listing = lambda: zh
    items, res = _items(fake)
    assert res["data"]["summary"]["sudo_list"] == "ok"
    for c in (S("/usr/sbin/blkid"), S("/usr/sbin/iptables-save"), S(HELPER)):
        assert c in fake.sudo_calls, c
    assert items["storage.blkid"]["state"] == "collected"


def test_unreadable_file_read_via_sudo_when_whitelisted():
    fake = FakeHost(nonroot={"ifcfg": "@@NOREAD /etc/netplan/01-net.yaml"},
                    sudo={S("/usr/bin/cat", "/etc/netplan/01-net.yaml"): "network:\n  version: 2"})
    it = _items(fake)[0]["net.ifcfg"]
    assert it["state"] == "collected" and it["files"][0]["via"] == "sudo"


def test_partial_denied_still_collected_with_note():
    fake = FakeHost(nonroot={"ifcfg": ("@@FILE /etc/sysconfig/network-scripts/ifcfg-eth0\nDEVICE=eth0\n@@END\n"
                                       "@@NOREAD /etc/netplan/x.yaml")})
    it = _items(fake)[0]["net.ifcfg"]
    assert it["state"] == "collected" and it["unreadable_paths"] == ["/etc/netplan/x.yaml"]
    assert "權限不足" in it["note"]


def test_runner_without_stderr_is_unknown_not_empty():
    """runner 不回 stderr 時，sudo 空輸出分不出是被拒還是沒資料 → 標不明，不可以說未使用。"""
    items, _ = _items(FakeHost(with_last=False, sudo={S(HELPER): ""}))
    assert items["acct.sudoers"]["state"] == "unknown"


# ---- 指令不存在／沒跑過 ----
def test_missing_tool_marks_no_tool():
    fake = FakeHost(tools=[t for t in ALL_TOOLS if t != "systemctl"],
                    bins=[b for b in ALL_BINS if not re.search(r"/(pvs|vgs|lvs)$", b)])
    items, _ = _items(fake)
    assert items["boot.enabled_services"]["state"] == "no_tool"
    assert items["storage.pv_vg_lv"]["state"] == "no_tool"
    assert not any("vgs" in c for c in fake.sudo_calls)     # 沒有就不送


def test_file_absent_is_empty():
    assert _items(FakeHost(nonroot={"fstab": ""}))[0]["storage.fstab"]["state"] == "empty"


def test_absent_raw_key_marks_not_run():
    data = R.build_rebuild_data({})
    states = {it["id"]: it["state"] for g in data["groups"] for it in g["items"]}
    assert states["storage.fstab"] == "not_run" and states["acct.groups"] == "not_run"
    assert data["summary"]["total_a"] == 24 and data["summary"]["total_b"] == 19


def test_non_linux_skipped():
    res = R.collect_rebuild(FakeHost(), "h", "aix")
    assert res["data"] is None and "AIX" in res["skipped"]


# ---- 靜態檢查：sudo 那段不得有 shell ----
FORBIDDEN = ("sh -c", "bash -c", "|", ";", ">", "<", "&", "$", "`", "*", "?", "'", '"', "(", ")")


def _assert_clean_sudo(cmd: str):
    assert cmd.startswith("sudo -n /"), cmd
    for bad in FORBIDDEN:
        assert bad not in cmd, f"sudo 指令出現 {bad!r}：{cmd}"
    assert re.fullmatch(r"sudo -n /\S+( [A-Za-z0-9_./,:=@+-]+)*", cmd), cmd


def test_static_nonroot_commands_never_call_sudo():
    for k, cmd in R.NONROOT.items():
        assert "sudo" not in cmd, f"NONROOT[{k}] 不可以夾帶 sudo（root 的讀取要走 argv 那段）"


def test_static_every_sudo_command_is_fixed_argv():
    for name, cands in R.SUDO_BINS.items():
        for b in cands:
            _assert_clean_sudo(R.render_sudo([b, *R.SUDO_FIXED_ARGS.get(name, [])]))


def test_static_sudo_actually_sent_in_a_full_run_is_clean():
    fake = FakeHost(
        nonroot={"ifcfg": "@@NOREAD /etc/netplan/01-net.yaml",
                 "passwd": "root:x:0:0::/root:/bin/bash"},
        dirs=R.ROOT_DIRS,
        sudo={**LVM_OK, S("/usr/bin/ls", "-1", "/var/spool/cron"): "root",
              S("/usr/bin/ls", "-1", "/etc/NetworkManager/system-connections"): "eth0.nmconnection"},
        # 放行萬用字元規則，讓每一種動態組出來的指令都真的被送出、被檢查到
        rules=["/usr/bin/cat /var/spool/cron/*", "/usr/bin/cat /etc/NetworkManager/system-connections/*",
               "/usr/bin/cat /etc/netplan/*", "/usr/bin/ssh-keygen -lf *", "/usr/sbin/blkid",
               '/usr/local/libexec/webit3/account-facts ""', "/usr/sbin/iptables-save",
               "/usr/bin/ls -1 /var/spool/cron/crontabs"])
    R.collect_rebuild(fake, "h", "linux")
    assert fake.sudo_calls[0] == R.SUDO_LIST_CMD == "LC_ALL=C sudo -n -l"
    rest = fake.sudo_calls[1:]
    assert len(rest) >= 12
    for c in rest:
        _assert_clean_sudo(c)
    kinds = " ".join(rest)
    for must in ("/var/spool/cron/root", "eth0.nmconnection", "/etc/netplan/01-net.yaml",
                 "/root/.ssh/authorized_keys", "/etc/lvm/backup/rootvg"):
        assert must in kinds, must


@pytest.mark.parametrize("argv", [
    ["/usr/bin/cat", "/etc/x;rm"], ["/usr/bin/cat", "/etc/lvm/backup/../../shadow"],
    ["/usr/bin/cat", "/etc/a b"], ["/usr/bin/cat", "/etc/shadow"], ["/usr/bin/cat", "/etc/gshadow"],
    ["/usr/bin/cat", "/etc/x|y"], ["/usr/bin/cat", "/etc/*"], ["cat", "/etc/fstab"],
    ["/usr/bin/cat", "/root/.ssh/id_rsa"], ["/usr/bin/cat", "/etc/pki/tls/private/x.key"],
])
def test_render_sudo_rejects_unsafe(argv):
    assert R.render_sudo(argv) is None


def test_whitelist_deployed_rules_have_no_wildcards():
    """Q78-SHRINK：收斂後納管佈的每一條都不得有萬用字元。唯一允許的 * 是 existing 的 smartctl /dev/*。"""
    wl = R.sudo_whitelist()
    assert {r["kind"] for r in wl} == {"existing", "deployed"}
    for r in wl:
        assert r["wildcard"] == ("*" in r["cmd"])
        assert not ("sh -c" in r["cmd"] or "bash" in r["cmd"])
    assert not any(r["wildcard"] for r in wl if r["kind"] == "deployed")
    wild_existing = [r["cmd"] for r in wl if r["wildcard"]]
    assert wild_existing == [R.SMART_RULE] and "/dev/*" in R.SMART_RULE
    # 收斂掉的危險樣式都不在了
    joined = "\n".join(r["cmd"] for r in wl)
    assert "/sys/class/dmi/id/*" not in joined and "ssh-keygen" not in joined
    assert "dmidecode -t *" not in joined and "vgs *" not in joined and "lvs *" not in joined


def test_SHRINK_部署的sudoers除smartctl外沒有任何萬用字元():
    """守門（mutation-checked）：產出的 shell／playbook sudoers 行，除 smartctl 裝置掃描外無 `*`。"""
    import onboard_engine as oe
    sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
    import test_sudoers_visudo as V

    for text in (V._from_shell(oe.build_linux_script("ssh-rsa AAAATEST", "192.0.2.1")),
                 V._from_playbook(oe.build_linux_playbook("ssh-rsa AAAATEST", "192.0.2.1"))
                 if pytest.importorskip("yaml") else ""):
        if not text:
            continue
        for ln in text.splitlines():
            if "NOPASSWD:" not in ln or "*" not in ln:
                continue
            assert "smartctl" in ln and "/dev/*" in ln, f"非預期的萬用字元 sudoers 行：{ln}"
        # 危險樣式一個都不剩
        assert "/sys/class/dmi/id/*" not in text
        assert "ssh-keygen" not in text
        assert "dmidecode -t *" not in text
        assert "/usr/sbin/vgs *" not in text and "/usr/sbin/lvs *" not in text
        assert "/usr/bin/cat /etc/" not in text and "/usr/bin/cat /var/" not in text


def test_納管腳本與劇本都佈重建規則_同一份來源():
    """2026-09-22 退回事件的同一族：規則只寫在一處、佈署路徑沒跟上。驗**產出的腳本**。"""
    import onboard_engine as oe
    sys.path.insert(0, str(ROOT / "tests" / "asset_module"))
    import test_sudoers_visudo as V

    sh = V._from_shell(oe.build_linux_script("ssh-rsa AAAATEST", "192.0.2.1"))
    for spec in R.rebuild_sudoers_specs():
        assert f"webit3scan ALL=(root) NOPASSWD: {spec}" in sh.splitlines(), spec
    # 兩支唯讀輔助程式都要佈（內容隨腳本固定）
    assert R.REBUILD_HELPER_PATH in sh and "rebuild-facts dmi" in sh
    pytest.importorskip("yaml")
    pb = V._from_playbook(oe.build_linux_playbook("ssh-rsa AAAATEST", "192.0.2.1"))
    for spec in R.rebuild_sudoers_specs():
        assert f"webit3scan ALL=(root) NOPASSWD: {spec}" in pb.splitlines(), spec
    assert R.REBUILD_HELPER_PATH in pb


def test_白名單文件列出每條收斂後規則且宣告無萬用字元():
    doc = ROOT / "AI" / "OS重建收集_sudo白名單_2026-10-18.md"
    if not doc.exists():          # 公開 relay 不帶 AI/
        pytest.skip("relay 快照沒有 AI/")
    text = doc.read_text(encoding="utf-8")
    for spec in R.rebuild_sudoers_specs():
        if spec.startswith(R.REBUILD_HELPER_PATH):        # helper 子指令逐條列出
            assert spec in text, spec
    assert "沒有任何萬用字元" in text and "重新納管" in text
    # 收斂掉的危險樣式不得再以「正在佈的 sudoers 規則行」出現（描述它被拿掉可以）
    assert "NOPASSWD: /usr/sbin/dmidecode -t *" not in text
    assert "NOPASSWD: /usr/bin/cat /etc" not in text


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()


def _helper_host(subs: dict, allow=None):
    """已重新納管的機（有 helper、sudo -l 放行 helper 子指令）：回傳 helper 子指令的輸出。"""
    allow = allow if allow is not None else list(subs)
    sudo = {S(R.REBUILD_HELPER_PATH, sub): subs[sub] for sub in subs}
    rules = [f"{R.REBUILD_HELPER_PATH} {sub}" for sub in allow]
    # 固定參數指令（vgs/lvs/pvs/blkid）仍直接送，放行它們避免 LVM 項目變 denied 干擾
    rules += ["/usr/sbin/vgs *", "/usr/sbin/lvs *", "/usr/sbin/pvs *", '/usr/sbin/blkid ""']
    return FakeHost(sudo=sudo, rules=rules)


def test_helper_mode_lvm_backup_cron_nm_netplan():
    subs = {
        "lvm-backup": f"F /etc/lvm/backup/rootvg OK {_b64('rootvg { id=abc }')}\n",
        "cron-spool": f"F /var/spool/cron/root OK {_b64('0 3 * * * /x')}\n",
        "nm-connections": f"F /etc/NetworkManager/system-connections/eth0.nmconnection OK {_b64('[connection]')}\n",
        "netplan": f"F /etc/netplan/01.yaml OK {_b64('network: {}')}\n",
        "authkeys-fp": f"K root {_b64('2048 SHA256:xx root@h (RSA)')}\n",
        "dmi": "product_name=X\n",
    }
    fake = _helper_host(subs)
    items, _ = _items(fake)
    assert items["storage.lvm_metadata"]["state"] == "collected"
    assert items["storage.lvm_metadata"]["files"][0]["path"] == "/etc/lvm/backup/rootvg"
    assert items["cron.user"]["state"] == "collected"
    assert any("eth0" in f["path"] for f in items["net.ifcfg"]["files"])
    assert any("netplan" in f["path"] for f in items["net.ifcfg"]["files"])
    assert items["acct.authkeys"]["state"] == "collected"
    # 走 helper 時，一條 cat 萬用字元指令都沒送出
    assert not any(c.startswith("sudo -n /usr/bin/cat") for c in fake.sudo_calls)
    assert not any("ssh-keygen" in c for c in fake.sudo_calls)


def test_helper_denied_shows_permission_denied_not_empty():
    fake = _helper_host({"lvm-backup": "", "cron-spool": ""}, allow=[])  # 沒放行 helper → 退回舊路徑
    # 這台沒放行 helper 也沒有 cat 萬用字元（allow 空）→ lvm metadata 應為 denied/空，不是假裝沒有
    items, _ = _items(fake)
    assert items["cron.user"]["state"] in ("denied", "empty", "not_run")


def test_old_host_without_helper_still_uses_legacy_cat():
    """還沒重新納管（sudo -l 沒有 helper 子指令，但有舊的 cat 萬用字元）→ 照舊直接 cat，不壞。"""
    fake = FakeHost(sudo=LVM_OK)   # LVM_OK 用 cat /etc/lvm/backup/rootvg
    assert not any("rebuild-facts" in r for r in fake.rules)
    items, _ = _items(fake)
    assert items["storage.lvm_metadata"]["state"] == "collected"
    assert any(c.startswith("sudo -n /usr/bin/cat /etc/lvm/backup/") for c in fake.sudo_calls)


# ---- 唯讀輔助程式 bash 本體：拒符號連結、拒逃出目錄 ----
def test_helper_script_rejects_symlink_and_escape(tmp_path):
    import os
    import shutil
    import subprocess

    if os.name != "posix":
        pytest.skip("helper 是 Linux 專用；symlink/realpath 語意在非 POSIX 不準（Linux CI 會跑）")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    head = R.REBUILD_HELPER_SCRIPT[:R.REBUILD_HELPER_SCRIPT.index('case "$1" in')]
    harness = tmp_path / "h.sh"
    harness.write_text(head + '\nlist_dir "$1"\n', encoding="utf-8", newline="\n")
    d = tmp_path / "d"
    d.mkdir()
    (d / "good.conf").write_text("ok", encoding="utf-8")
    outside = tmp_path / "secret"
    outside.write_text("TOPSECRET", encoding="utf-8")
    try:
        os.symlink(outside, d / "evil")                 # 連到目錄外
        os.symlink(d / "good.conf", d / "inlink")       # 連到目錄內也一律拒
    except (OSError, NotImplementedError):
        pytest.skip("這台不能建 symlink")
    out = subprocess.run([bash, str(harness), str(d)], capture_output=True, text=True).stdout
    lines = {ln.split(" ")[1]: ln.split(" ")[0] for ln in out.splitlines() if " " in ln}
    assert lines.get(str(d / "good.conf")) == "F"
    assert lines.get(str(d / "evil")) == "D"            # 符號連結（逃出）被拒
    assert lines.get(str(d / "inlink")) == "D"          # 任何符號連結都拒
    assert "TOPSECRET" not in out


def test_helper_script_only_takes_one_fixed_subcommand():
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("沒有 bash")
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n") as f:
        f.write(R.REBUILD_HELPER_SCRIPT)
        p = f.name
    assert subprocess.run([bash, p], capture_output=True).returncode == 2            # 沒給子指令
    assert subprocess.run([bash, p, "lvm-backup", "x"], capture_output=True).returncode == 2  # 多給參數
    assert subprocess.run([bash, p, "../etc"], capture_output=True).returncode == 2   # 未知子指令


# ---- ESXi 天條 ----
def test_ESXi_連上後自我檢查_一條收集指令都不送():
    class Esx(FakeHost):
        def __call__(self, host, cmd):
            self.sent = getattr(self, "sent", []) + [cmd]
            return "VMkernel\nVMware ESXi 8.0.3 build-1\n" if cmd == R.ESXI_PROBE_CMD else ""

    fake = Esx()
    res = R.collect_rebuild(fake, "10.99.7.21", "linux")
    assert res["data"] is None and "ESXi" in res["skipped"]
    assert fake.sent == [R.ESXI_PROBE_CMD]


def test_ESXi_登記為ESXi的已納管主機_不連線(tmp_path, monkeypatch):
    import manage_state
    D.init_db(tmp_path / "t.db")
    conn = D.get_connection(tmp_path / "t.db")
    D.insert_hardware(conn, asset_serial="ESX-ON", ip="10.99.7.21", hostname="esx-on",
                      os="VMware ESXi 6.7", collect_ok=1)
    conn.commit()

    def boom(*a, **k):
        raise AssertionError("ESXi 不可以建 runner")

    monkeypatch.setattr(manage_state, "_runner_for", boom)
    out = R.run_collection(conn)
    conn.close()
    assert out["ok"] == 0 and out["failed"] == 0 and out["unsupported"] == 1
    assert "ESXi" in out["unsupported_detail"][0]["reason"]


# ---- 落庫 ----
def test_store_upsert_and_no_secrets(tmp_path):
    D.init_db(tmp_path / "t.db")
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    fake = FakeHost(nonroot={"fstab": "@@FILE /etc/fstab\nUUID=abc / ext4 defaults 0 1\n@@END",
                             "passwd": "root:x:0:0::/root:/bin/bash"},
                    sudo={S(HELPER): ("=== SHADOW\nACCT root :: L\n=== SUDOERS\n"
                                      "Defaults rootpw=TopSecretXYZ\n=== KEYS\n=== END\n")})
    res = R.collect_rebuild(fake, "10.99.0.5", "linux")
    R.store_rebuild(conn, "HW-1", "10.99.0.5", res)
    R.store_rebuild(conn, "HW-1", "10.99.0.5", res)
    assert conn.execute("SELECT COUNT(*) FROM host_rebuild").fetchone()[0] == 1
    row = conn.execute("SELECT data_json, raw_json FROM host_rebuild").fetchone()
    assert "storage.fstab" in row["data_json"]
    for leak in ("TopSecretXYZ", "ACCT root", "root:x:0:0"):
        assert leak not in row["raw_json"] and leak not in row["data_json"]


# ---- API（routers/rebuild.py，跟其他 routers 一樣攤平掛在 app 上）----
@pytest.fixture()
def client(tmp_path):
    import api
    from fastapi.testclient import TestClient
    p = tmp_path / "t.db"
    D.init_db(p)

    def _get_db():
        c = D.get_connection(p)
        try:
            yield c
        finally:
            c.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    api.app.dependency_overrides[api.require_auth] = lambda: {"username": "tester", "role": "admin"}
    try:
        yield TestClient(api.app, raise_server_exceptions=False), p
    finally:
        api.app.dependency_overrides.pop(api.get_db, None)
        api.app.dependency_overrides.pop(api.require_auth, None)


def test_api_get_rebuild_null_then_data(client):
    c, p = client
    assert c.get("/api/host-spec/rebuild?asset_serial=HW-9").json() == {"host_rebuild": None}
    conn = D.get_connection(p)
    R.store_rebuild(conn, "HW-9", "10.99.0.9",
                    R.collect_rebuild(FakeHost(nonroot={"hosts": "127.0.0.1 localhost"}), "h", "linux"))
    conn.close()
    body = c.get("/api/host-spec/rebuild?asset_serial=HW-9").json()["host_rebuild"]
    assert body["collected_at"] and body["data"]["summary"]["total_a"] == 24
    assert "raw_json" not in body


def test_api_collect_endpoint_wired(client, monkeypatch):
    c, _ = client
    seen = {}

    def fake_run(conn, trigger, only_serial):
        seen["s"] = only_serial
        return {"ok": 0}

    monkeypatch.setattr(R, "run_collection", fake_run)
    r = c.post("/api/host-spec/rebuild-collect?asset_serial=HW-1")
    assert r.status_code == 200 and seen["s"] == "HW-1"


def test_api_whitelist_endpoint(client):
    c, _ = client
    items = c.get("/api/host-spec/rebuild-sudo-whitelist").json()["items"]
    assert any("/usr/sbin/pvs" in i["cmd"] for i in items)


def test_rebuild端點攤平在app_routes且要登入():
    import api
    paths = {getattr(r, "path", None) for r in api.app.routes}
    for p in ("/api/host-spec/rebuild-collect", "/api/host-spec/rebuild",
              "/api/host-spec/rebuild-sudo-whitelist"):
        assert p in paths
    assert not (BACKEND / "rebuild_api.py").exists()
