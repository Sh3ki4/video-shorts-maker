from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .utils import parse_fraction


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def find_ffmpeg(configured: str = "", app_dir: Path | None = None) -> tuple[Path, Path] | None:
    candidates: list[Path] = []
    if configured:
        item = Path(configured).expanduser()
        candidates.extend([item, item / "ffmpeg.exe", item / "bin" / "ffmpeg.exe"])
    if app_dir:
        candidates.extend(
            [
                app_dir / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
                app_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
                app_dir / "ffmpeg.exe",
            ]
        )
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    for ffmpeg in candidates:
        if ffmpeg.name.lower() != "ffmpeg.exe" and ffmpeg.is_file():
            continue
        ffprobe = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if ffmpeg.is_file() and ffprobe.is_file():
            return ffmpeg.resolve(), ffprobe.resolve()
    return None


class FFmpeg:
    def __init__(
        self,
        ffmpeg: Path,
        ffprobe: Path,
        log: Callable[[str], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.log = log or (lambda _message: None)
        self.cancel_check = cancel_check or (lambda: False)
        self._nvenc_available: bool | None = None

    def _run(self, command: list[str], label: str = "FFmpeg") -> subprocess.CompletedProcess[str]:
        if self.cancel_check():
            raise FFmpegError("Обработка отменена пользователем.")
        self.log(f"{label}…")
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            if self.cancel_check():
                process.terminate()
                process.wait(timeout=5)
                raise FFmpegError("Обработка отменена пользователем.")
        code = process.wait()
        output = "".join(lines)
        if code:
            tail = "\n".join(output.splitlines()[-25:])
            raise FFmpegError(f"{label} завершился с ошибкой:\n{tail}")
        return subprocess.CompletedProcess(command, code, "", output)

    def probe(self, path: Path) -> MediaInfo:
        command = [
            str(self.ffprobe), "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=_creation_flags(),
        )
        if result.returncode:
            raise FFmpegError(f"Не удалось прочитать файл {path.name}:\n{result.stderr[-1500:]}")
        try:
            data = json.loads(result.stdout)
            video = next(stream for stream in data["streams"] if stream.get("codec_type") == "video")
            audio = any(stream.get("codec_type") == "audio" for stream in data["streams"])
            duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
            return MediaInfo(
                duration=duration,
                width=int(video.get("width", 0)),
                height=int(video.get("height", 0)),
                fps=parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"), 30.0),
                has_audio=audio,
            )
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise FFmpegError(f"В файле {path.name} не найден корректный видеопоток.") from exc

    def supports_nvenc(self) -> bool:
        if self._nvenc_available is None:
            result = subprocess.run(
                [str(self.ffmpeg), "-hide_banner", "-encoders"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=_creation_flags(),
            )
            self._nvenc_available = result.returncode == 0 and "h264_nvenc" in result.stdout
        return self._nvenc_available

    @staticmethod
    def video_filter(width: int, height: int, fps: float, mode: str) -> str:
        if mode == "vertical_blur":
            radius = max(12, min(50, min(width, height) // 30))
            scale_background = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur={radius}:2,eq=brightness=-0.08:saturation=0.85"
            )
            scale_foreground = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
            scale = (
                f"split=2[blur_bg][full_frame];"
                f"[blur_bg]{scale_background}[blurred];"
                f"[full_frame]{scale_foreground}[fitted];"
                f"[blurred][fitted]overlay=(W-w)/2:(H-h)/2"
            )
        elif mode == "vertical_crop":
            scale = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        elif mode == "vertical_pad":
            scale = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
            )
        else:
            scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        return f"{scale},setsar=1,fps={fps:.6f},format=yuv420p"

    @staticmethod
    def encoder_args(nvenc: bool, quality: str, bitrate_mbps: float) -> list[str]:
        if bitrate_mbps > 0:
            bitrate = f"{bitrate_mbps:g}M"
            if nvenc:
                return ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_mbps * 2:g}M"]
            return ["-c:v", "libx264", "-preset", "medium", "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_mbps * 2:g}M"]
        cq = {"high": "19", "balanced": "23", "compact": "28"}.get(quality, "21")
        if nvenc:
            return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", cq, "-b:v", "0"]
        return ["-c:v", "libx264", "-preset", "medium", "-crf", cq]

    def run_encode(
        self,
        builder: Callable[[bool], list[str]],
        prefer_nvenc: bool,
        label: str,
    ) -> None:
        use_nvenc = prefer_nvenc and self.supports_nvenc()
        if prefer_nvenc and not use_nvenc:
            self.log("NVENC не найден в этой сборке FFmpeg — используется процессор.")
        try:
            self._run(builder(use_nvenc), label)
        except FFmpegError as exc:
            if self.cancel_check() or str(exc).strip() == "Обработка отменена пользователем.":
                raise
            if not use_nvenc:
                raise
            self.log("NVENC не запустился (возможна проблема драйвера). Повторяю через CPU…")
            self._run(builder(False), f"{label} (CPU)")

    def extract_audio(self, source: Path, start: float, duration: float, target: Path) -> None:
        command = [
            str(self.ffmpeg), "-hide_banner", "-y", "-ss", f"{start:.6f}",
            "-i", str(source), "-t", f"{duration:.6f}", "-vn", "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", str(target),
        ]
        self._run(command, "Извлечение звука")

    def normalize_segment(
        self,
        source: Path,
        target: Path,
        info: MediaInfo,
        width: int,
        height: int,
        fps: float,
        mode: str,
        prefer_nvenc: bool,
        quality: str,
        bitrate_mbps: float,
        start: float = 0.0,
        duration: float | None = None,
        volume: float = 1.0,
        label: str = "Подготовка видео",
    ) -> None:
        vf = self.video_filter(width, height, fps, mode)

        def build(nvenc: bool) -> list[str]:
            command = [str(self.ffmpeg), "-hide_banner", "-y"]
            if start > 0:
                command += ["-ss", f"{start:.6f}"]
            command += ["-i", str(source)]
            if not info.has_audio:
                command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            if duration is not None:
                command += ["-t", f"{duration:.6f}"]
            command += ["-map", "0:v:0", "-map", "0:a:0?" if info.has_audio else "1:a:0"]
            audio_filter = f"volume={max(0.0, volume):.4f},aresample=async=1:first_pts=0"
            if duration is not None:
                # Keep the video duration even when the source audio ends early.
                audio_filter += f",apad=whole_dur={duration:.6f},atrim=duration={duration:.6f}"
            command += ["-vf", vf, "-af", audio_filter]
            command += self.encoder_args(nvenc, quality, bitrate_mbps)
            command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", str(target)]
            return command

        self.run_encode(build, prefer_nvenc, label)

    @staticmethod
    def ass_filter_path(path: Path) -> str:
        value = path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        return f"'{value}'"

    def compose_with_banner(
        self,
        main: Path,
        banner: Path,
        subtitles: Path,
        target: Path,
        main_duration: float,
        insert_at: float,
        banner_duration: float,
        banner_mode: str,
        output_width: int,
        output_height: int,
        banner_position_percent: float,
        main_volume_during_banner: float,
        prefer_nvenc: bool,
        quality: str,
        bitrate_mbps: float,
    ) -> None:
        if banner_mode == "overlay":
            latest = max(0.05, main_duration - min(banner_duration, max(0.05, main_duration - 0.05)))
            point = min(max(insert_at, 0.05), latest)
        else:
            point = min(max(insert_at, 0.05), max(0.05, main_duration - 0.05))
        ass_path = self.ass_filter_path(subtitles)
        if banner_mode == "overlay":
            banner_info = self.probe(banner)
            banner_height = max(2, banner_info.height)
            y = round(output_height * min(max(banner_position_percent, 0.0), 100.0) / 100.0 - banner_height / 2)
            y = min(max(y, 0), max(0, output_height - banner_height))
            end = min(main_duration, point + banner_duration)
            delay_ms = max(0, round(point * 1000))
            duck = min(max(main_volume_during_banner, 0.0), 1.0)
            graph = (
                f"[1:v]setpts=PTS-STARTPTS+{point:.6f}/TB[ban_v];"
                f"[0:v][ban_v]overlay=x=(W-w)/2:y={y}:eof_action=pass:shortest=0:repeatlast=0[cv];"
                f"[cv]ass=filename={ass_path}[outv];"
                f"[0:a]volume=volume={duck:.4f}:enable='between(t,{point:.6f},{end:.6f})'[main_a];"
                f"[1:a]adelay={delay_ms}|{delay_ms},apad=whole_dur={main_duration:.6f}[ban_a];"
                f"[main_a][ban_a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                f"alimiter=limit=0.95[outa]"
            )
            audio_map = "[outa]"
        else:
            graph = (
                f"[0:v]split=2[mv0][mv1];"
                f"[mv0]trim=start=0:end={point:.6f},setpts=PTS-STARTPTS[pre_v];"
                f"[mv1]trim=start={point:.6f},setpts=PTS-STARTPTS[post_v];"
                f"[0:a]asplit=2[ma0][ma1];"
                f"[ma0]atrim=start=0:end={point:.6f},asetpts=PTS-STARTPTS[pre_a];"
                f"[ma1]atrim=start={point:.6f},asetpts=PTS-STARTPTS[post_a];"
                f"[1:v]setpts=PTS-STARTPTS[ban_v];[1:a]asetpts=PTS-STARTPTS[ban_a];"
                f"[pre_v][pre_a][ban_v][ban_a][post_v][post_a]concat=n=3:v=1:a=1[cv][ca];"
                f"[cv]ass=filename={ass_path}[outv]"
            )
            audio_map = "[ca]"

        def build(nvenc: bool) -> list[str]:
            command = [
                str(self.ffmpeg), "-hide_banner", "-y", "-i", str(main), "-i", str(banner),
                "-filter_complex", graph, "-map", "[outv]", "-map", audio_map,
            ]
            command += self.encoder_args(nvenc, quality, bitrate_mbps)
            command += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)]
            return command

        self.run_encode(build, prefer_nvenc, "Финальный монтаж")


def even(value: int) -> int:
    return max(2, value - value % 2)


def overlay_banner_geometry(
    output_width: int,
    output_height: int,
    banner_info: MediaInfo,
    width_percent: float,
) -> tuple[int, int]:
    """Fit the complete banner in a wide area without covering captions."""
    target_width = even(round(output_width * min(max(width_percent, 20.0), 100.0) / 100.0))
    source_ratio = (banner_info.width / banner_info.height) if banner_info.width > 0 and banner_info.height > 0 else 16 / 9
    target_height = even(round(target_width / max(source_ratio, 0.1)))
    max_height = even(round(output_height * 0.40))
    if target_height > max_height:
        target_height = max_height
        target_width = even(round(target_height * source_ratio))
    return min(target_width, even(output_width)), min(target_height, even(output_height))


def output_geometry(info: MediaInfo, resolution: str, mode: str) -> tuple[int, int]:
    if resolution.lower() in {"original", "исходное"}:
        if mode.startswith("vertical"):
            height = even(info.height or 1920)
            return even(round(height * 9 / 16)), height
        return even(info.width or 1920), even(info.height or 1080)
    try:
        width, height = resolution.lower().split("x", 1)
        return even(int(width)), even(int(height))
    except (ValueError, TypeError):
        return (1080, 1920) if mode.startswith("vertical") else (1920, 1080)


def output_fps(info: MediaInfo, fps_setting: str) -> float:
    if fps_setting.lower() in {"source", "исходный"}:
        return min(max(info.fps, 1.0), 120.0)
    try:
        return min(max(float(fps_setting), 1.0), 120.0)
    except ValueError:
        return 30.0
