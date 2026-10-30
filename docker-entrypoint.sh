#!/bin/sh
# 以 root 修正資料夾權限（使用者可能用 root 把 cookies 複製進來），再降權執行服務
set -e
mkdir -p /data/jobs /data/cookies
chown -R mediahook:mediahook /data
exec setpriv --reuid=mediahook --regid=mediahook --init-groups "$@"
