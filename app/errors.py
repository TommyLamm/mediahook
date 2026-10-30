"""錯誤碼與給人看的錯誤訊息（捷徑會直接顯示 message）。"""

from __future__ import annotations

MESSAGES = {
    "UNAUTHORIZED": "API 金鑰錯誤",
    "FORBIDDEN": "下載連結無效或已過期",
    "INVALID_REQUEST": '請求格式錯誤，需要 JSON：{{"url": "…"}}',
    "INVALID_URL": "找不到有效的網址",
    "UNSUPPORTED_URL": "不支援這個網站：{host}",
    "UNSUPPORTED_LINK": "目前不支援這種 {platform} 連結",
    "NOT_IMPLEMENTED": "{platform} 的支援還在開發中",
    "LOGIN_REQUIRED": "{platform} 需要登入才能下載，請在 VPS 放上 cookies",
    "COOKIES_EXPIRED": "{platform} 拒絕存取，cookies 可能已失效，請重新匯出",
    "NOT_FOUND": "找不到內容：貼文可能已刪除、設為私人，或連結有誤",
    "RATE_LIMITED": "{platform} 暫時限制了請求，請稍後再試",
    "NETWORK_ERROR": "連不上 {platform}，請檢查 VPS 網路或 proxy 設定",
    "NO_MEDIA": "這則貼文裡沒有可下載的圖片或影片",
    "NO_AUDIO": "這則貼文沒有可抽取的音訊",
    "FILE_TOO_LARGE": "檔案超過 {limit} MB 上限",
    "TIMEOUT": "處理逾時，已中止",
    "QUEUE_FULL": "排隊中的任務太多，請稍後再試",
    "INTERRUPTED": "伺服器重新啟動導致任務中斷，請重試",
    "EXPIRED": "檔案已過期清除，請重新下載",
    "JOB_NOT_FOUND": "找不到這個任務",
    "FILE_NOT_FOUND": "檔案不存在或已清除",
    "EXTRACT_FAILED": "下載失敗：{detail}",
}


class _Blank(dict):
    def __missing__(self, key: str) -> str:
        return ""


class MediaHookError(Exception):
    def __init__(self, code: str, message: str | None = None, **params: object) -> None:
        self.code = code
        self.message = message or MESSAGES.get(code, code).format_map(_Blank(params))
        super().__init__(f"{code}: {self.message}")
