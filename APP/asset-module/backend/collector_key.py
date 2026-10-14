"""收集金鑰的檢視與匯入。

## 為什麼需要匯入

在此之前金鑰只有一條路：`deploy.sh` 用 `ssh-keygen` 產一把新的。但如果公司已經有
一把**佈遍全機隊的金鑰**（例如巡檢用的），匯入它就省掉重新納管每一台。

## 換金鑰的代價：所有已納管主機當場失聯

目標主機的 `authorized_keys` 裡是**舊公鑰**。換一把新的，收集端就再也連不進去——
不是「壞掉」，是那把鑰匙不開那些鎖了。要嘛匯入的正是它們認得的那把，
要嘛全部重新納管。

所以匯入前一定要講清楚「這會影響幾台」，並且要打字確認——跟取消納管同一個道理：
這個動作會讓已經在運作的東西停止運作，按鈕太容易誤觸。

## 私鑰不經過網路的那條路

從瀏覽器貼上私鑰，它會經過內網 HTTP（這套系統沒有 TLS），是**明文**。
所以提供第二種模式：人自己用 scp 把檔案放到主機上，網頁只負責
「驗證配對 → 設好權限 → 搬到正確位置 → 重啟」。私鑰完全不過網路。

方便與安全各有取捨，讓用的人自己選——但要**明白告訴他差別**，不能只給一種
然後假裝沒有這件事。

## 私鑰的生命週期

進來就寫檔，**絕不回傳、不寫 log、不進稽核紀錄**。稽核只記「誰在什麼時候
換了金鑰、新舊指紋各是什麼」——指紋是公開資訊，足以追溯，且洩漏不出東西。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

#: 跟 onboard_engine 同一個位置——那邊是唯一權威，這裡只是取用。
def key_path() -> Path:
    import onboard_engine
    return Path(onboard_engine.COLLECTOR_KEY_PUB[:-4])


def pub_path() -> Path:
    import onboard_engine
    return Path(onboard_engine.COLLECTOR_KEY_PUB)


def fingerprint(pub_file: Path) -> str | None:
    """算公鑰指紋。指紋是公開資訊，可以顯示、可以寫稽核。"""
    if not pub_file.exists():
        return None
    try:
        r = subprocess.run(["ssh-keygen", "-lf", str(pub_file)],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def status(conn) -> dict:
    """現況：有沒有金鑰、指紋是什麼、換掉會影響幾台。

    「會影響幾台」是這頁最重要的數字——沒有它，換金鑰看起來只是改個設定。
    """
    kp, pp = key_path(), pub_path()
    onboarded = conn.execute(
        "SELECT COUNT(*) FROM hardware WHERE collect_ok = 1").fetchone()[0]
    return {
        "has_key": kp.exists() and pp.exists(),
        "private_exists": kp.exists(),
        "public_exists": pp.exists(),
        "path": str(kp),
        "fingerprint": fingerprint(pp),
        "public_key": pp.read_text(encoding="utf-8").strip() if pp.exists() else None,
        "modified_at": (datetime.fromtimestamp(kp.stat().st_mtime)
                        .strftime("%Y-%m-%d %H:%M:%S") if kp.exists() else None),
        # 換金鑰會讓這些主機的收集當場失效——它們 authorized_keys 裡是舊公鑰
        "onboarded_hosts": onboarded,
    }


def derive_public(private_text: str) -> str:
    """從私鑰導出公鑰。**這同時就是「這把私鑰是不是有效的」的驗證。**

    用 `cryptography` 在記憶體裡解析，**私鑰完全不落地**——原本的做法是寫進
    暫存檔再叫 `ssh-keygen -y`，那有兩個問題：

    · 私鑰為了「驗證」而落地。就算馬上刪，那段時間它在磁碟上，而且刪除不保證抹除。
    · Windows 上 ssh-keygen 檢查 ACL 不是 chmod，暫存檔一定被判 Bad permissions
      而拒絕——開發機根本驗不了這段。

    `cryptography` 本來就是相依套件（credential_store 的 Fernet 在用），不新增相依。

    有密語的金鑰在這裡就會被擋下來：收集是排程無人值守跑的，不會有人在旁邊輸入密語。
    """
    from cryptography.hazmat.primitives import serialization

    data = (private_text or "").strip().encode()
    if not data:
        raise ValueError("私鑰是空的")
    try:
        key = serialization.load_ssh_private_key(data, password=None)
    except TypeError as exc:
        # cryptography 對「有密語但沒給」丟 TypeError
        raise ValueError(
            "這把私鑰有密語保護，收集端用不了——收集是排程無人值守跑的，"
            "不會有人在旁邊輸入密語。請提供沒有密語的金鑰，"
            "或用 ssh-keygen -p 移除密語後再匯入。") from exc
    except ValueError as exc:
        msg = str(exc).lower()
        if "password" in msg or "passphrase" in msg or "encrypted" in msg:
            raise ValueError(
                "這把私鑰有密語保護，收集端用不了——收集是排程無人值守跑的，"
                "不會有人在旁邊輸入密語。請提供沒有密語的金鑰，"
                "或用 ssh-keygen -p 移除密語後再匯入。") from exc
        raise ValueError(f"讀不出這把私鑰（格式可能不對）：{exc}") from exc

    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode()
    return pub.strip()


def _same_key(a: str, b: str) -> bool:
    """比對兩把公鑰是不是同一把。只比「類型 + 內容」，不比註解——
    註解（結尾那段 user@host）改了不代表換了金鑰，拿它當條件會誤判。"""
    def norm(s):
        parts = s.strip().split()
        return " ".join(parts[:2]) if len(parts) >= 2 else s.strip()
    return norm(a) == norm(b)


def import_key(conn, private_text: str, public_text: str | None,
               imported_by: str) -> dict:
    """匯入一把金鑰。**先全部驗完才動檔案**——中途失敗留下半套是這裡最糟的結果：
    私鑰寫了公鑰沒寫，系統下次啟動會自己再產一把，匯入的那把就對不上了。

    回傳新舊指紋。**絕不回傳私鑰內容。**
    """
    derived = derive_public(private_text)        # 同時驗證私鑰可用
    if public_text and public_text.strip():
        if not _same_key(derived, public_text):
            raise ValueError(
                "你提供的公鑰跟這把私鑰不是一對。"
                "佈下去之後目標主機認的是公鑰、收集端拿的是私鑰，"
                "對不起來的話納管會顯示成功但永遠連不進去——"
                "所以這裡直接擋掉。（不確定的話公鑰留空，系統會自己從私鑰導出。）")
        pub_final = public_text.strip()          # 保留使用者那份的註解
    else:
        pub_final = derived

    kp, pp = key_path(), pub_path()
    old_fp = fingerprint(pp)
    kp.parent.mkdir(parents=True, exist_ok=True)

    # 備份舊的。換錯金鑰＝全機隊收集失效，一定要還原得回來。
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backed_up = []
    for src in (kp, pp):
        if src.exists():
            dst = src.with_name(f"{src.name}.bak-{stamp}")
            shutil.copy2(src, dst)
            backed_up.append(str(dst))

    kp.write_text(private_text.strip() + "\n", encoding="utf-8")
    os.chmod(kp, 0o600)
    pp.write_text(pub_final + "\n", encoding="utf-8")
    os.chmod(pp, 0o644)
    # 服務是以收集帳號身分跑的，讀不到就等於沒匯入
    _chown_to_service_user(kp, pp)

    new_fp = fingerprint(pp)
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES ('collector_key_last_import', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜{imported_by}｜"
         f"舊 {old_fp or '(無)'} → 新 {new_fp}",))
    conn.commit()

    return {
        "old_fingerprint": old_fp,
        "new_fingerprint": new_fp,
        "backed_up": backed_up,
        "public_key": pub_final,
        "changed": not (old_fp and new_fp and old_fp == new_fp),
    }


def _chown_to_service_user(*paths: Path) -> None:
    """把檔案交給跑服務的帳號。

    ⚠️ 這一步漏掉的話症狀很難查：檔案在、內容對、權限 600，但服務讀不到，
    納管會說「讀不到收集金鑰」。221 是 sysctl、公司機是 sysinfra——
    兩台不一樣，所以不寫死，用檔案原本的擁有者或現在跑的身分。
    """
    if os.name == "nt":
        return
    try:
        import pwd
        uid = os.getuid()
        gid = os.getgid()
        # root 執行時抓不到「服務帳號」是誰；用金鑰目錄的擁有者當依據
        if uid == 0:
            st = paths[0].parent.stat()
            uid, gid = st.st_uid, st.st_gid
            _ = pwd  # 只是說明為什麼 import
        for p in paths:
            os.chown(p, uid, gid)
    except (OSError, ImportError):
        pass       # 權限不足就算了——下面的自我檢查會把讀不到這件事講出來


def adopt_from_path(conn, src_private: str, imported_by: str) -> dict:
    """從主機上既有的檔案接手（私鑰**不經過網路**）。

    人自己用 scp 把私鑰放到主機上，這裡只負責驗證、設權限、搬到正確位置。
    這條路比從瀏覽器貼上安全——這套系統沒有 TLS，貼上的私鑰會以明文
    走內網 HTTP。方便與安全的取捨要讓用的人自己選，但差別要講明白。
    """
    src = Path(src_private)
    if not src.is_file():
        raise ValueError(f"找不到檔案：{src_private}")
    if src.stat().st_size > 64 * 1024:
        raise ValueError(f"{src_private} 有 {src.stat().st_size} 位元組，不像是一把私鑰")
    text = src.read_text(encoding="utf-8", errors="replace")
    pub_src = src.with_name(src.name + ".pub")
    pub_text = (pub_src.read_text(encoding="utf-8")
                if pub_src.is_file() else None)
    return import_key(conn, text, pub_text, imported_by)
