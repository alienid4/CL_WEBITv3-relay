"""S6：建立/重設本機管理者帳號用的CLI（D8無AD/SSO，MVP不做使用者管理UI，先用CLI）。

用法：python seed_admin.py <username>　（互動輸入密碼，不回顯、不留在shell history）
若帳號已存在，重設密碼；不存在則新增。
"""
from __future__ import annotations

import getpass
import sys

from auth import hash_password
from db import create_user, get_connection, get_user_by_username, init_db


def seed_admin(username: str, password: str) -> None:
    init_db()
    conn = get_connection()
    try:
        existing = get_user_by_username(conn, username)
        password_hash = hash_password(password)
        if existing is not None:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (password_hash, username),
            )
            conn.commit()
            print(f"已重設帳號 {username} 的密碼")
        else:
            create_user(conn, username, password_hash)
            print(f"已建立帳號 {username}")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python seed_admin.py <username>")
        sys.exit(1)
    entered_username = sys.argv[1]
    entered_password = getpass.getpass("設定密碼：")
    confirm_password = getpass.getpass("再次輸入密碼：")
    if entered_password != confirm_password:
        print("兩次輸入的密碼不一致")
        sys.exit(1)
    if len(entered_password) < 8:
        print("密碼至少需要8碼")
        sys.exit(1)
    seed_admin(entered_username, entered_password)
