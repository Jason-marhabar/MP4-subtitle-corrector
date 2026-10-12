import subprocess
from pathlib import Path


class FFmpegProcessor:
    """FFmpeg音视频处理类，供业务调度模块调用"""

    def __init__(self, ffmpeg_path: str = "ffmpeg"):
        self.ffmpeg_path = ffmpeg_path

    # ================== 方法1：提取音频（你已成功） ==================
    def extract_audio(
        self,
        input_mp4: str,
        output_wav: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> str:
        """从MP4中提取音频，输出为Whisper可用的WAV格式。"""
        input_path = Path(input_mp4)
        if not input_path.exists():
            raise FileNotFoundError(f"输入文件不存在：{input_mp4}")

        output_path = Path(output_wav)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(input_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            str(output_path),
        ]
        self._run_command(cmd, "提取音频")
        return str(output_path)

    # ================== 方法2：MP4流拷贝封装（缺失部分） ==================
    def copy_mp4_stream(
        self,
        input_mp4: str,
        output_mp4: str,
        audio_normalize: bool = False,
    ) -> str:
        """对MP4进行流拷贝封装（不重新编码），输出新的MP4文件。"""
        input_path = Path(input_mp4)
        if not input_path.exists():
            raise FileNotFoundError(f"输入文件不存在：{input_mp4}")

        output_path = Path(output_mp4)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if audio_normalize:
            cmd = [
                self.ffmpeg_path, "-y",
                "-i", str(input_path),
                "-c:v", "copy",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_path),
            ]
        else:
            cmd = [
                self.ffmpeg_path, "-y",
                "-i", str(input_path),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ]

        self._run_command(cmd, "MP4封装")
        return str(output_path)

    # ================== 辅助方法：统一执行命令（缺失部分） ==================
    def _run_command(self, cmd: list, operation_name: str):
        """执行FFmpeg命令，统一处理错误和日志"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{operation_name}失败。\n"
                    f"命令：{' '.join(cmd)}\n"
                    f"错误信息：{result.stderr[:500]}"
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{operation_name}超时，已超过1小时限制")