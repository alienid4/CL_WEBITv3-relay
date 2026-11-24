"""正式機 443（WEBIT_HTTPS=1）：預設關、開了才改 nginx／本機綁定；collector 網址可由環境覆蓋。"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = (ROOT / "APP/asset-module/deploy.sh").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "APP/asset-module/backend"))


def test_https_default_off_keeps_old_behavior():
    assert 'WEBIT_HTTPS="${WEBIT_HTTPS:-0}"' in DEPLOY
    assert 'BIND_HOST="0.0.0.0"' in DEPLOY
    assert "--host ${BIND_HOST}" in DEPLOY and "HOST=${BIND_HOST}" in DEPLOY
    assert 'API_BASE="http://${API_HOST}:${API_PORT}"' in DEPLOY


def test_https_mode_binds_local_and_opens_only_443():
    blk = DEPLOY[DEPLOY.index('if [ "$WEBIT_HTTPS" = "1" ]; then\n  BIND_HOST'):]
    blk = blk[: blk.index("\nfi\n")]
    assert 'BIND_HOST="127.0.0.1"' in blk
    assert 'API_BASE="https://${API_HOST}"' in blk
    assert "WEBIT3_COOKIE_SECURE=1" in blk
    assert "--add-port=443/tcp" in DEPLOY
    assert "--remove-port=${API_PORT}/tcp" in DEPLOY and "--remove-port=${WEB_PORT}/tcp" in DEPLOY


def test_nginx_proxy_routes_and_fail_closed():
    assert "location /api/" in DEPLOY and "proxy_pass http://127.0.0.1:${API_PORT}" in DEPLOY
    assert "proxy_pass http://127.0.0.1:${WEB_PORT}" in DEPLOY
    assert "nginx -t ||" in DEPLOY and "exit 1" in DEPLOY[DEPLOY.index("nginx -t"):][:200]
    assert "ssl_protocols TLSv1.2 TLSv1.3" in DEPLOY


def test_deploy_syntax_ok():
    r = subprocess.run(["bash", "-n"], input=DEPLOY.encode("utf-8"), capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")


def _bash(script: str) -> str:
    r = subprocess.run(["bash"], input=script.encode("utf-8"), capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout.decode("utf-8")


def test_unit_env_lines_identical_when_off_and_added_when_on():
    tmpl = (
        '_NL=$\'\\n\'; MK_ENV_LINE=""; NODE_CA_LINE="%s"; COLLECTOR_URL_LINE="C=1"; COOKIE_SECURE_LINE="S=1"\n'
        'if [ "$WEBIT_HTTPS" = "1" ]; then\n'
        '  MK_ENV_LINE="${MK_ENV_LINE:+${MK_ENV_LINE}${_NL}}${COLLECTOR_URL_LINE}${_NL}${COOKIE_SECURE_LINE}"\n'
        'fi\n'
        'cat <<U\n[Service]\n${MK_ENV_LINE}\nE=1${NODE_CA_LINE:+${_NL}${NODE_CA_LINE}}\nU\n'
    )
    assert 'MK_ENV_LINE="${MK_ENV_LINE:+${MK_ENV_LINE}${_NL}}${COLLECTOR_URL_LINE}${_NL}${COOKIE_SECURE_LINE}"' in DEPLOY
    assert "${NODE_CA_LINE:+${_NL}${NODE_CA_LINE}}" in DEPLOY
    off = _bash("WEBIT_HTTPS=0\n" + tmpl % "")
    on = _bash("WEBIT_HTTPS=1\n" + tmpl % "N=1")
    assert off == "[Service]\n\nE=1\n"
    assert on == "[Service]\nC=1\nS=1\nE=1\nN=1\n"


def test_install_sh_checks_443_in_https_mode():
    inst = (ROOT / "APP/asset-module/install.sh").read_text(encoding="utf-8")
    assert '*" 443/tcp "*' in inst


def test_collector_base_url_env_override(monkeypatch):
    import onboard_engine as oe

    monkeypatch.setenv("ASSET_COLLECTOR_IP", "10.99.0.5")
    monkeypatch.delenv("ASSET_COLLECTOR_URL", raising=False)
    assert oe.collector_base_url() == "http://10.99.0.5:8000"
    monkeypatch.setenv("ASSET_COLLECTOR_URL", "https://10.99.0.5/")
    assert oe.collector_base_url() == "https://10.99.0.5"
    monkeypatch.setenv("ASSET_COLLECTOR_URL", "https://x y;rm")  # 不合格式＝忽略，退回預設
    assert oe.collector_base_url() == "http://10.99.0.5:8000"
