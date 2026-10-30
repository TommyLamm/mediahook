# 部署到 VPS

目標：`https://dl.gyke.net` 跑起 MediaHook，Caddy 自動處理 HTTPS。

## 1. DNS 與防火牆

1. 在 DNS 新增 A 記錄：`dl.gyke.net` → VPS 的 IP。
   如果網域在 Cloudflare，先設成 **DNS only（灰色雲朵）**，讓 Caddy 能直接申請憑證。
2. 防火牆開放 TCP 80、443（HTTP/3 想用的話再加 UDP 443）。

## 2. 下載程式並設定

```bash
git clone <你的 repo 網址> /opt/mediahook
cd /opt/mediahook

cp .env.example .env
openssl rand -hex 32          # 產生 API 金鑰
nano .env                     # 填入 MEDIAHOOK_API_KEY
```

`.env` 至少要有：

```ini
MEDIAHOOK_API_KEY=剛剛產生的金鑰
MEDIAHOOK_PUBLIC_URL=https://dl.gyke.net
```

其他選項（檔案保留時間、同時下載數、proxy…）都寫在 `.env.example` 裡。

## 3. 啟動

```bash
docker compose up -d --build
docker compose logs -f        # 看到 Caddy 取得憑證、app 啟動完成就可以 Ctrl+C
```

## 4. 驗證

```bash
KEY=你的金鑰

curl https://dl.gyke.net/health
# {"ok":true}

curl -H "Authorization: Bearer $KEY" https://dl.gyke.net/api/status
# 會列出 yt-dlp / gallery-dl 版本、ffmpeg 是否可用、哪些平台已放 cookies

curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"url": "https://www.tiktok.com/@xxx/video/123"}' \
     "https://dl.gyke.net/api/jobs?wait=20"
```

## 5. 放上 cookies（IG、限動、私人內容必備）

> **請用小帳號**。自動化存取有機會讓帳號被限制，不要用主帳號。

1. 在電腦的 Chrome 或 Firefox 安裝擴充套件 **Get cookies.txt LOCALLY**（開源、只在本機匯出）。
2. 開一個**無痕視窗**，用小帳號登入平台，隨便滑一下。
3. 在該網站上點擴充套件 → Export，得到 Netscape 格式的 `cookies.txt`。
4. **直接關掉無痕視窗，不要按登出**（登出會讓這組 cookies 失效）。
5. 依平台改名後上傳到 VPS：

| 平台 | 檔名 |
|---|---|
| Instagram | `instagram.txt` |
| X (Twitter) | `twitter.txt` |
| TikTok | `tiktok.txt` |
| Facebook | `facebook.txt` |
| 抖音 | `douyin.txt` |
| 小紅書 | `xiaohongshu.txt` |

```bash
scp instagram.txt your-vps:/opt/mediahook/data/cookies/
```

不用重啟，下一個任務就會用到。用 `/api/status` 確認 `cookies` 欄位變成 `true`。
之後捷徑如果顯示「cookies 可能已失效」，重做一次匯出即可。

cookies 等同帳號登入狀態：不要 commit 到 git、不要傳給別人。

## 6. 更新 yt-dlp / gallery-dl

平台改版時下載工具常常會壞，更新通常就能解決：

```bash
cd /opt/mediahook
docker compose build --build-arg TOOLS_REFRESH=$(date +%s) app
docker compose up -d app
```

建議設成每週自動更新（`crontab -e`，注意 `%` 在 crontab 裡要跳脫）：

```cron
17 4 * * 1 cd /opt/mediahook && docker compose build --build-arg TOOLS_REFRESH=$(date +\%s) app && docker compose up -d app
```

更新 MediaHook 本身：`git pull && docker compose up -d --build`。

## 7. 查問題

```bash
docker compose logs -f app                 # 服務 log：每個任務的建立、完成、失敗原因
ls data/jobs/                               # 每個任務一個資料夾
cat data/jobs/<任務 id>/worker.log          # yt-dlp / gallery-dl 的詳細輸出
```

任務資料夾會在 `MEDIAHOOK_FILE_TTL_HOURS`（預設 6 小時）後自動刪除。

| 錯誤碼 | 怎麼處理 |
|---|---|
| `LOGIN_REQUIRED` | 該平台需要 cookies，照第 5 步放上去 |
| `COOKIES_EXPIRED` | cookies 失效或帳號被限制，重新匯出；持續發生就換小帳號 |
| `RATE_LIMITED` | 平台暫時限流，等一陣子；頻繁發生可以調高 `MEDIAHOOK_PLATFORM_MIN_INTERVAL` |
| `NETWORK_ERROR` | VPS 連不到該平台，檢查網路或 proxy 設定 |
| `EXTRACT_FAILED` | 多半是平台改版，先照第 6 步更新工具 |
| `NOT_FOUND` | 貼文被刪、設為私人，或連結錯誤 |

## 已經有反向代理的話

如果 VPS 上已經有 Nginx / Traefik 佔用 80、443：

1. 刪掉 `docker-compose.yml` 裡的 `caddy` 服務。
2. 把 app 的 `expose` 改成只綁本機：

   ```yaml
   ports:
     - "127.0.0.1:8000:8000"
   ```

3. Nginx 範例：

   ```nginx
   server {
       server_name dl.gyke.net;
       # listen 443 ssl; 與憑證設定照你現有的寫法

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_read_timeout 60s;   # 長輪詢最多等 25 秒
       }
   }
   ```

## 抖音、小紅書連不到時（美歐機房）

海外 IP 抓這兩個平台比較容易被要求驗證。先試著放 cookies；還是不行的話，可以讓這兩個平台改走亞洲出口：

1. 在你家裡的電腦（或任何台灣、香港的機器）跑一個 SOCKS5 proxy，並透過 Tailscale 讓 VPS 連得到。
2. 在 `.env` 加上：

   ```ini
   MEDIAHOOK_PROXY_DOUYIN=socks5://100.x.y.z:1080
   MEDIAHOOK_PROXY_XIAOHONGSHU=socks5://100.x.y.z:1080
   ```

3. `docker compose up -d app` 重新載入設定。
