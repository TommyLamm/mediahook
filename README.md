# MediaHook

自架的社群媒體下載服務，搭配 iPhone 捷徑使用：在 App 裡按「分享」→ 執行捷徑 → 圖片和影片直接存進相簿。

- **平台**：Instagram（貼文、輪播、Reels、限動）、X、TikTok、Facebook、小紅書（影片）、抖音（影片）
- **iPhone 相容**：WebP／AVIF 轉 JPEG，影片確保是 H.264/HEVC + AAC 的 mp4，存得進相簿
- **音訊模式**：把影片的聲音抽成 m4a
- **文案**：一併回傳貼文文字、作者、原始連結
- **登入內容**：每個平台可以各自放 cookies

## 架構

```
iPhone 捷徑 ──HTTPS + API 金鑰──▶ Caddy ─▶ FastAPI
                                            │  SQLite 存任務狀態、長輪詢
                                            ▼
                               worker 子程序（逾時會強制中止）
                                 1. 展開短網址（每一跳都檢查白名單）
                                 2. 依平台依序嘗試 gallery-dl / yt-dlp
                                 3. ffmpeg / Pillow 轉成 iPhone 相容格式
                                            │
捷徑 ◀── 簽名短效下載連結 + 文案 ─────────────┘
```

| 平台 | 嘗試順序 |
|---|---|
| Instagram、X | gallery-dl → yt-dlp |
| TikTok 影片、Facebook | yt-dlp → gallery-dl |
| TikTok 圖文 | gallery-dl → yt-dlp |
| 小紅書、抖音 | yt-dlp（圖文筆記在 Phase 2 支援） |
| Threads | Phase 2 |

## 文件

- [部署到 VPS](docs/deploy.md)：DNS、Docker Compose、cookies、更新與除錯
- [建立 iPhone 捷徑](docs/shortcut.md)

## API

所有 `/api/*` 都要帶 `Authorization: Bearer <MEDIAHOOK_API_KEY>`（或 `X-API-Key`）。

### `POST /api/jobs?wait=20`

```json
{"url": "https://www.instagram.com/p/xxx/", "mode": "auto"}
```

- `url`：網址，或整段分享文字（例如抖音的「複製打開抖音…」），會自動找出裡面的網址
- `mode`：`auto`（圖片／影片）或 `audio`（抽音訊）
- `wait`：最多等幾秒再回應（上限 25）。時間內完成就直接回傳結果

### `GET /api/jobs/{id}?wait=20`

查詢任務，`wait` 意義同上。

### 回應格式

```json
{
  "id": "Xb3kP0aQ1zR7",
  "status": "done",
  "platform": "instagram",
  "poll_url": "https://dl.gyke.net/api/jobs/Xb3kP0aQ1zR7?wait=20",
  "message": "已完成：2 張圖片、1 部影片",
  "source_url": "https://www.instagram.com/p/xxx/",
  "author": "someone",
  "title": "",
  "caption": "貼文文字…",
  "share_text": "貼文文字…\n\n— someone\nhttps://www.instagram.com/p/xxx/",
  "count": 3,
  "items": [
    {"type": "image", "url": "https://dl.gyke.net/f/Xb3kP0aQ1zR7/instagram_xxx_01.jpg?exp=…&sig=…",
     "filename": "instagram_xxx_01.jpg", "size": 123456}
  ]
}
```

`status` 只有三種：`running`（還在處理，用 `poll_url` 繼續查）、`done`、`failed`。
失敗時會有 `error_code` 與 `message`：

| `error_code` | 意思 |
|---|---|
| `UNAUTHORIZED` | API 金鑰錯誤 |
| `INVALID_URL` / `UNSUPPORTED_URL` | 沒有網址，或不是支援的網站 |
| `UNSUPPORTED_LINK` | 支援的網站，但這種連結（例如個人頁）不支援 |
| `NOT_IMPLEMENTED` | 平台支援還在開發中 |
| `LOGIN_REQUIRED` / `COOKIES_EXPIRED` | 需要放 cookies，或 cookies 已失效 |
| `RATE_LIMITED` | 平台暫時限流 |
| `NETWORK_ERROR` | VPS 連不到平台 |
| `NOT_FOUND` | 貼文被刪除或設為私人 |
| `NO_MEDIA` / `NO_AUDIO` | 沒有可下載的媒體／音訊 |
| `FILE_TOO_LARGE` | 超過 `MEDIAHOOK_MAX_FILE_MB` |
| `TIMEOUT` | 超過 `MEDIAHOOK_JOB_TIMEOUT_SECONDS` |
| `QUEUE_FULL` | 排隊任務太多 |
| `EXPIRED` | 檔案已過期清除 |
| `EXTRACT_FAILED` | 其他下載錯誤（多半是平台改版，更新工具通常能解決） |

### 其他端點

- `GET /f/{id}/{filename}?exp=…&sig=…`：下載檔案。連結有簽名、預設 1 小時後失效，不需要金鑰
- `GET /api/status`：工具版本、ffmpeg 狀態、哪些平台已放 cookies
- `GET /health`：健康檢查（不需金鑰）

## 開發

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest            # 需要系統裝有 ffmpeg

MEDIAHOOK_API_KEY=dev-key-0123456789 MEDIAHOOK_DATA_DIR=./data \
  uvicorn --factory app.main:create_app --reload
```

## 路線圖

- [x] Phase 0：API、任務佇列、長輪詢、簽名連結、自動清理、Docker 部署
- [x] Phase 1：IG、X、TikTok、FB、iPhone 格式轉換、文案、音訊模式、捷徑教學
- [ ] Phase 2：Threads、小紅書圖文、抖音圖文的自寫 adapter
- [ ] Phase 3：各平台冒煙測試腳本、維運強化

請只下載你有權保存的內容，並遵守各平台的使用條款。
