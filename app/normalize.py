"""把下載結果轉成 iPhone 相簿／檔案 App 收得下的格式。

- 圖片：WebP、AVIF 等轉成 JPEG（有透明度就轉 PNG，動圖轉 GIF）
- 影片：H.264/HEVC + AAC 的 mp4 原樣保留；容器不對只換封裝，編碼不對才重新轉檔
- 音訊模式：抽成 m4a，來源本來是 AAC 就直接複製音軌
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .errors import MediaHookError

log = logging.getLogger("mediahook.normalize")

IMAGE_KEEP = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".heic": ".heic", ".heif": ".heic", ".gif": ".gif"}
IMAGE_CONVERT = {".webp", ".avif", ".bmp", ".tif", ".tiff", ".jfif"}
MEDIA_EXTS = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".flv", ".ts", ".3gp",
    ".m4a", ".mp3", ".aac", ".opus", ".ogg", ".oga", ".wav", ".flac", ".weba",
}
IPHONE_VIDEO_CONTAINERS = {".mp4", ".mov", ".m4v"}
IPHONE_PIX_FMTS = {
    "h264": {"yuv420p", "yuvj420p"},
    "hevc": {"yuv420p", "yuvj420p", "yuv420p10le"},
}
IPHONE_AUDIO_CODECS = {"aac", "alac"}
FFMPEG_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class OutputFile:
    path: Path
    kind: str  # image | video | audio


def normalize(files: list[Path], mode: str, out_dir: Path, stem: str) -> list[OutputFile]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[OutputFile] = []
    for src in files:
        dest = out_dir / f"{stem}_{len(outputs) + 1:02d}"  # 副檔名由轉換結果決定
        try:
            output = _to_audio(src, dest) if mode == "audio" else _to_iphone(src, dest)
        except Exception:
            log.warning("無法轉換 %s，略過", src.name, exc_info=True)
            continue
        if output:
            outputs.append(output)
    if not outputs:
        raise MediaHookError("NO_AUDIO" if mode == "audio" else "NO_MEDIA")
    return outputs


def _to_iphone(src: Path, dest: Path) -> OutputFile | None:
    ext = src.suffix.lower()
    if ext in IMAGE_KEEP:
        return _move(src, dest.with_suffix(IMAGE_KEEP[ext]), "image")
    if ext in IMAGE_CONVERT or (ext not in MEDIA_EXTS and _is_image(src)):
        return _convert_image(src, dest)

    streams = probe(src)
    video, audio = _first_stream(streams, "video"), _first_stream(streams, "audio")
    if video:
        return _to_mp4(src, dest, video, audio)
    if audio:
        return _to_audio(src, dest)
    return None


def _to_mp4(src: Path, dest: Path, video: dict, audio: dict | None) -> OutputFile:
    vcodec = video.get("codec_name")
    video_ok = video.get("pix_fmt") in IPHONE_PIX_FMTS.get(vcodec, ())
    hevc_tag_ok = vcodec != "hevc" or video.get("codec_tag_string") == "hvc1"
    audio_ok = audio is None or audio.get("codec_name") in IPHONE_AUDIO_CODECS
    ext = src.suffix.lower()

    if video_ok and hevc_tag_ok and audio_ok and ext in IPHONE_VIDEO_CONTAINERS:
        return _move(src, dest.with_suffix(".mov" if ext == ".mov" else ".mp4"), "video")

    target = dest.with_suffix(".mp4")
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?"]
    if video_ok:
        # 編碼本身相容，只換封裝；HEVC 要標成 hvc1，iOS 才認得
        cmd += ["-c:v", "copy"] + (["-tag:v", "hvc1"] if vcodec == "hevc" else [])
    else:
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        ]
    cmd += ["-c:a", "copy"] if audio_ok else ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(target)]
    _run(cmd)
    src.unlink(missing_ok=True)
    return OutputFile(target, "video")


def _to_audio(src: Path, dest: Path) -> OutputFile | None:
    ext = src.suffix.lower()
    if ext in IMAGE_KEEP or ext in IMAGE_CONVERT:
        return None
    audio = _first_stream(probe(src), "audio")
    if audio is None:
        return None
    target = dest.with_suffix(".m4a")
    codec = ["-c:a", "copy"] if audio.get("codec_name") == "aac" else ["-c:a", "aac", "-b:a", "192k"]
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
          "-map", "0:a:0", "-vn", *codec, "-movflags", "+faststart", str(target)])
    src.unlink(missing_ok=True)
    return OutputFile(target, "audio")


def _convert_image(src: Path, dest: Path) -> OutputFile:
    try:
        with Image.open(src) as img:
            if getattr(img, "n_frames", 1) > 1:
                target = dest.with_suffix(".gif")
                img.save(target, save_all=True, loop=0, duration=img.info.get("duration", 100), disposal=2)
            else:
                img = ImageOps.exif_transpose(img)
                if _has_transparency(img):
                    target = dest.with_suffix(".png")
                    img.save(target, "PNG")
                else:
                    target = dest.with_suffix(".jpg")
                    img.convert("RGB").save(target, "JPEG", quality=95)
    except OSError:
        # Pillow 讀不了的格式交給 ffmpeg
        target = dest.with_suffix(".jpg")
        _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(target)])
    src.unlink(missing_ok=True)
    return OutputFile(target, "image")


def _has_transparency(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA", "PA"):
        return img.getchannel("A").getextrema()[0] < 255
    return img.mode == "P" and "transparency" in img.info


def _is_image(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def probe(path: Path) -> list[dict]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_entries",
         "stream=codec_type,codec_name,codec_tag_string,pix_fmt:stream_disposition=attached_pic",
         str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return json.loads(result.stdout or "{}").get("streams", [])


def _first_stream(streams: list[dict], codec_type: str) -> dict | None:
    for stream in streams:
        # 音檔內嵌的封面圖也會被當成 video stream，要排除
        if stream.get("codec_type") == codec_type and not stream.get("disposition", {}).get("attached_pic"):
            return stream
    return None


def _move(src: Path, target: Path, kind: str) -> OutputFile:
    shutil.move(src, target)
    return OutputFile(target, kind)


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失敗：{result.stderr.strip()[-500:]}")
