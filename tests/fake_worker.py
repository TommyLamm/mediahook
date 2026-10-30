"""測試用的假 worker：依網址內容模擬成功、失敗、卡住或當掉。"""

import json
import sys
import time
from pathlib import Path

job_dir = Path(sys.argv[1])
request = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
url = request["url"]

if "sleep" in url:
    time.sleep(30)
if "crash" in url:
    sys.exit(1)

if "fail" in url:
    result = {"ok": False, "code": "LOGIN_REQUIRED", "message": "Instagram 需要登入才能下載，請在 VPS 放上 cookies"}
else:
    files_dir = job_dir / "files"
    files_dir.mkdir()
    (files_dir / "instagram_ABC_01.jpg").write_bytes(b"jpeg-bytes")
    (files_dir / "instagram_ABC_02.mp4").write_bytes(b"mp4-bytes!")
    result = {
        "ok": True,
        "url": url,
        "platform": "instagram",
        "caption": "今天的晚餐",
        "author": "someone",
        "title": None,
        "post_id": "ABC",
        "files": [
            {"name": "instagram_ABC_01.jpg", "kind": "image", "size": 10},
            {"name": "instagram_ABC_02.mp4", "kind": "video", "size": 10},
        ],
    }

(job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
