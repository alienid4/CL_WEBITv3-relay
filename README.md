# 資產盤點模組 — 中繼快照（relay snapshot）

⚠️ **這不是主 repo，是單向匯出的快照。**

FastAPI（後端）＋ Nuxt3（前端）＋ SQLite 的 IT 資產盤點系統。這份 repo 只是為了把程式碼
送到「只能存取 public GitHub」的機器上而存在，內容經過去識別化：

- 內網位址、主機名、公司識別字都換成通用值（`YOUR_SERVER_IP`、`demo-host` 等）
- 不含開發文件、交接紀錄、部署拓撲
- **不含 git 歷史**——每次都是重新匯出的乾淨快照

主 repo（含完整歷史）不在 GitHub 上。

## 已安裝的機器要更新 → 看 `patches/`

`patches/YYYYMMDD/patch_YYYYMMDD_HHMM.tar.gz` 是給**已經裝好的機器**用的更新包
（幾百 KB，不必重抓整包）。**永遠套最新那一包就好**——進日期最大的資料夾、
取檔名時間最大的那一包。

每一包都是**累積包**（從「公司主機確定已套用的版本」一路到打包當下），所以中間漏掉
幾包都沒關係、也不必照順序補；抓最新的套一次就到位。

```bash
cd /tmp && tar xzf patch_YYYYMMDD_HHMM.tar.gz && cd patch_YYYYMMDD_HHMM
sudo bash patch.sh
```

同目錄的 `.sha256` 可用 `sha256sum -c` 核對。包裡的 `MANIFEST.txt` 寫著這包是哪個版本、
涵蓋哪些改動。`patch.sh` 冪等，重複套用不會壞。

## 怎麼跑

後端：
```bash
cd APP/asset-module/backend
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python db.py                                      # 建 SQLite（預設 data/asset.db）
python seed_admin.py admin                        # 建管理者帳號（互動輸入密碼）
uvicorn api:app --port 8000
```

前端（另開終端）：
```bash
cd APP/asset-module/frontend
npm install
npm run dev                                       # http://localhost:3000
```

> 前端預設打 `http://localhost:8000`，可用 `NUXT_PUBLIC_API_BASE` 覆蓋。
> 若 `localhost` 在你的機器上解析到 IPv6 而服務只聽 IPv4，改用 `127.0.0.1`。

想要有資料可看：`cd APP/asset-module/backend && python seed_demo.py`

## 測試

```bash
python -m pytest -q          # 在 repo 根目錄執行，約 100 項
```

## 注意

`deploy.sh` 裡的 `API_HOST=YOUR_SERVER_IP` 是佔位符，要用請自行填入實際位址。
