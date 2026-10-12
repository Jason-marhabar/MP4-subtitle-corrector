# -*- coding: utf-8 -*-
"""
带断点续传与重试的文件下载器（GitHub 在国内直连不稳定，普通下载常中断）。

用法：
    python scripts/fetch_file.py <url> <目标路径> [期望的SHA256]

特性：
  - HTTP Range 断点续传：中断后重跑会从已下载字节继续，不重头开始
  - 指数退避重试，每轮都会重新解析跳转后的真实地址
  - 下载完成校验 SHA256；文件已存在且哈希正确时直接跳过
"""
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def sha256_of(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def remote_size(url, timeout=60):
    """取远程文件总大小，用于校验续传起点是否合法。"""
    req = urllib.request.Request(url, headers=UA, method="GET")
    req.add_header("Range", "bytes=0-0")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        cr = r.headers.get("Content-Range")  # e.g. bytes 0-0/42713078
        if cr and "/" in cr:
            return int(cr.split("/")[-1])
        return int(r.headers.get("Content-Length") or 0)


def download(url, dst, expect_sha=None, rounds=12):
    for attempt in range(1, rounds + 1):
        have = os.path.getsize(dst) if os.path.exists(dst) else 0
        try:
            total = remote_size(url)
        except Exception as e:
            wait = min(2 ** attempt, 60)
            print(f"[{attempt}/{rounds}] 无法获取远程大小：{type(e).__name__} {e} -> {wait}s 后重试")
            time.sleep(wait)
            continue

        if have == total and total > 0:
            print(f"文件已完整（{total:,} bytes），直接校验")
        else:
            if have > total:            # 本地比远程还大，说明是坏文件
                print(f"本地 {have:,} > 远程 {total:,}，删除重下")
                os.remove(dst)
                have = 0

            print(f"[{attempt}/{rounds}] 续传起点 {have:,}/{total:,} bytes")
            req = urllib.request.Request(url, headers=UA)
            if have:
                req.add_header("Range", f"bytes={have}-")
            try:
                with urllib.request.urlopen(req, timeout=120) as r, open(dst, "ab") as f:
                    n = have
                    t0 = time.time()
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        n += len(b)
                        pct = n * 100 // total if total else 0
                        speed = (n - have) / max(time.time() - t0, 0.001) / 1048576
                        print(f"\r  {n/1048576:7.1f}/{total/1048576:.1f} MB  {pct:3d}%  "
                              f"{speed:5.1f} MB/s", end="", flush=True)
                print()
            except Exception as e:
                wait = min(2 ** attempt, 60)
                print(f"\n  中断：{type(e).__name__} {e} -> {wait}s 后继续续传")
                time.sleep(wait)
                continue

        if expect_sha:
            got = sha256_of(dst)
            if got.lower() == expect_sha.lower():
                print(f"SHA256 校验通过：{got}")
                return True
            print(f"SHA256 不匹配：\n  实际 {got}\n  期望 {expect_sha}\n  删除后重下")
            os.remove(dst)
            time.sleep(2)
            continue

        print("未提供期望哈希，下载完成")
        return True

    print("重试次数用尽，下载失败")
    return False


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    url, dst = sys.argv[1], sys.argv[2]
    expect = sys.argv[3] if len(sys.argv) > 3 else None
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)

    # 已存在且哈希正确则跳过，避免重复下载
    if expect and os.path.exists(dst) and sha256_of(dst).lower() == expect.lower():
        print(f"目标已存在且校验通过，跳过：{dst}")
        return
    sys.exit(0 if download(url, dst, expect) else 2)


if __name__ == "__main__":
    main()
