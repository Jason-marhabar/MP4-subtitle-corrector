# model/ffmpeg_utils.py
import subprocess
import shutil

def check_ffmpeg_available() -> bool:
    """检查系统中FFmpeg是否可用"""
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        return False
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def get_ffmpeg_path() -> str:
    """获取FFmpeg可执行文件路径"""
    path = shutil.which("ffmpeg")
    if path is None:
        raise FileNotFoundError(
            "未找到FFmpeg，请确认已安装并添加到系统PATH中。"
        )
    return path