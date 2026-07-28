# 資產盤點模組 — 中繼快照（relay snapshot）

⚠️ **這不是主 repo，是單向匯出的快照。**

FastAPI（後端）＋ Nuxt3（前端）＋ SQLite 的 IT 資產盤點系統。這份 repo 只是為了把程式碼
送到「只能存取 public GitHub」的機器上而存在，內容經過去識別化：

- 內網位址、主機名、公司識別字都換成通用值（`YOUR_SERVER_IP`、`demo-host` 等）
- 不含開發文件、交接紀錄、部署拓撲
- **不含 git 歷史**——每次都是重新匯出的乾淨快照

主 repo（含完整歷史）不在 GitHub 上。

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
