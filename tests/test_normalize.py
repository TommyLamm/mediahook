import shutil
import subprocess

import pytest
from PIL import Image

from app.errors import MediaHookError
from app.normalize import normalize, probe

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="需要 ffmpeg")


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *map(str, args)], check=True)


def make_video(path, vcodec, acodec, extra=()):
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-f", "lavfi", "-i", "sine=frequency=440",
           "-t", "1", "-c:v", vcodec, "-c:a", acodec, *extra, path)


def codecs(path):
    streams = probe(path)
    return {s["codec_type"]: s for s in streams}


def test_keeps_jpeg_and_renames(tmp_path):
    src = tmp_path / "raw.jpeg"
    Image.new("RGB", (8, 8), "red").save(src)
    [out] = normalize([src], "auto", tmp_path / "out", "x_1")
    assert out.kind == "image" and out.path.name == "x_1_01.jpg"


def test_webp_to_jpeg(tmp_path):
    src = tmp_path / "a.webp"
    Image.new("RGB", (8, 8), "blue").save(src)
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.path.suffix == ".jpg"
    assert Image.open(out.path).format == "JPEG"


def test_transparent_webp_to_png(tmp_path):
    src = tmp_path / "a.webp"
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(src)
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.path.suffix == ".png"


def test_animated_webp_to_gif(tmp_path):
    src = tmp_path / "a.webp"
    frames = [Image.new("RGB", (8, 8), color) for color in ("red", "green")]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=100)
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.path.suffix == ".gif"
    assert Image.open(out.path).n_frames == 2


def test_avif_to_jpeg(tmp_path):
    src = tmp_path / "a.avif"
    Image.new("RGB", (8, 8), "green").save(src)
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.path.suffix == ".jpg"


def test_image_without_extension(tmp_path):
    src = tmp_path / "noext"
    Image.new("RGB", (8, 8), "red").save(src, "WEBP")
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.kind == "image" and out.path.suffix == ".jpg"


def test_compatible_mp4_untouched(tmp_path):
    src = tmp_path / "a.mp4"
    make_video(src, "libx264", "aac", ["-pix_fmt", "yuv420p"])
    size = src.stat().st_size
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.kind == "video" and out.path.suffix == ".mp4"
    assert out.path.stat().st_size == size


def test_vp9_opus_webm_transcoded(tmp_path):
    src = tmp_path / "a.webm"
    make_video(src, "libvpx-vp9", "libopus")
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    streams = codecs(out.path)
    assert out.path.suffix == ".mp4"
    assert streams["video"]["codec_name"] == "h264"
    assert streams["audio"]["codec_name"] == "aac"


def test_h264_in_mkv_remuxed(tmp_path):
    src = tmp_path / "a.mkv"
    make_video(src, "libx264", "aac", ["-pix_fmt", "yuv420p"])
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.path.suffix == ".mp4"
    assert codecs(out.path)["video"]["codec_name"] == "h264"


def test_hevc_retagged_hvc1(tmp_path):
    src = tmp_path / "a.mp4"
    make_video(src, "libx265", "aac", ["-pix_fmt", "yuv420p", "-tag:v", "hev1", "-x265-params", "log-level=error"])
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    video = codecs(out.path)["video"]
    assert video["codec_name"] == "hevc" and video["codec_tag_string"] == "hvc1"


def test_audio_mode_copies_aac(tmp_path):
    src = tmp_path / "a.mp4"
    make_video(src, "libx264", "aac", ["-pix_fmt", "yuv420p"])
    [out] = normalize([src], "audio", tmp_path / "out", "x")
    streams = codecs(out.path)
    assert out.kind == "audio" and out.path.suffix == ".m4a"
    assert set(streams) == {"audio"} and streams["audio"]["codec_name"] == "aac"


def test_audio_mode_reencodes_opus(tmp_path):
    src = tmp_path / "a.webm"
    make_video(src, "libvpx-vp9", "libopus")
    [out] = normalize([src], "audio", tmp_path / "out", "x")
    assert codecs(out.path)["audio"]["codec_name"] == "aac"


def test_audio_mode_skips_images(tmp_path):
    image = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(image)
    video = tmp_path / "b.mp4"
    make_video(video, "libx264", "aac", ["-pix_fmt", "yuv420p"])
    outputs = normalize([image, video], "audio", tmp_path / "out", "x")
    assert [o.path.name for o in outputs] == ["x_01.m4a"]


def test_audio_mode_without_audio(tmp_path):
    image = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(image)
    with pytest.raises(MediaHookError) as info:
        normalize([image], "audio", tmp_path / "out", "x")
    assert info.value.code == "NO_AUDIO"


def test_auto_mode_audio_only_file(tmp_path):
    src = tmp_path / "a.opus"
    ffmpeg("-f", "lavfi", "-i", "sine", "-t", "1", "-c:a", "libopus", src)
    [out] = normalize([src], "auto", tmp_path / "out", "x")
    assert out.kind == "audio" and out.path.suffix == ".m4a"


def test_mixed_post_keeps_order(tmp_path):
    first = tmp_path / "1.webp"
    Image.new("RGB", (8, 8)).save(first)
    second = tmp_path / "2.mp4"
    make_video(second, "libx264", "aac", ["-pix_fmt", "yuv420p"])
    third = tmp_path / "3.png"
    Image.new("RGB", (8, 8)).save(third)
    outputs = normalize([first, second, third], "auto", tmp_path / "out", "ig_ABC")
    assert [(o.path.name, o.kind) for o in outputs] == [
        ("ig_ABC_01.jpg", "image"), ("ig_ABC_02.mp4", "video"), ("ig_ABC_03.png", "image"),
    ]
