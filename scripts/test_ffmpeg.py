"""
独立验证脚本：音视频FFmpeg模块测试
用法：python scripts/test_ffmpeg.py <输入MP4路径> [输出目录]
"""
import sys
import os
from pathlib import Path

# 把项目根目录加入Python路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model.ffmpeg_processor import FFmpegProcessor
from model.ffmpeg_utils import check_ffmpeg_available


def main():
    # 1. 检查FFmpeg是否可用
    print("=" * 50)
    print("[检查1] FFmpeg可用性检查")
    if not check_ffmpeg_available():
        print("  ✗ FFmpeg不可用，请先安装FFmpeg并添加到PATH")
        sys.exit(1)
    print("  ✓ FFmpeg可用")

    # 2. 获取输入参数
    if len(sys.argv) < 2:
        print("用法：python test_ffmpeg.py <输入MP4路径> [输出目录]")
        sys.exit(1)

    input_mp4 = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "./test_output"

    if not Path(input_mp4).exists():
        print(f"  ✗ 输入文件不存在：{input_mp4}")
        sys.exit(1)

    print(f"\n输入文件：{input_mp4}")
    print(f"输出目录：{output_dir}")

    processor = FFmpegProcessor()
    stem = Path(input_mp4).stem

    # 3. 测试音频提取
    print("\n" + "=" * 50)
    print("[检查2] 音频提取测试")
    wav_path = os.path.join(output_dir, f"{stem}.wav")
    try:
        result = processor.extract_audio(input_mp4, wav_path)
        size_mb = os.path.getsize(result) / 1024 / 1024
        print(f"  ✓ 音频提取成功：{result}（{size_mb:.2f} MB）")
    except Exception as e:
        print(f"  ✗ 音频提取失败：{e}")

    # 4. 测试MP4流拷贝封装
    print("\n" + "=" * 50)
    print("[检查3] MP4流拷贝封装测试")
    out_mp4 = os.path.join(output_dir, f"{stem}_output.mp4")
    try:
        result = processor.copy_mp4_stream(input_mp4, out_mp4)
        size_mb = os.path.getsize(result) / 1024 / 1024
        print(f"  ✓ MP4封装成功：{result}（{size_mb:.2f} MB）")
    except Exception as e:
        print(f"  ✗ MP4封装失败：{e}")

    # 5. 测试带音频标准化的封装
    print("\n" + "=" * 50)
    print("[检查4] 音频标准化+MP4封装测试")
    out_mp4_norm = os.path.join(output_dir, f"{stem}_normalized.mp4")
    try:
        result = processor.copy_mp4_stream(
            input_mp4, out_mp4_norm, audio_normalize=True
        )
        size_mb = os.path.getsize(result) / 1024 / 1024
        print(f"  ✓ 标准化封装成功：{result}（{size_mb:.2f} MB）")
    except Exception as e:
        print(f"  ✗ 标准化封装失败：{e}")

    print("\n" + "=" * 50)
    print("全部测试完成！请手动验证：")
    print("  1. 生成的WAV文件能否正常播放（用系统播放器打开）")
    print(f"  2. 输出MP4（{out_mp4}）能否正常播放且音画同步")
    print(f"  3. 标准化MP4（{out_mp4_norm}）音频响度是否比原文件更均匀")


if __name__ == "__main__":
    main()