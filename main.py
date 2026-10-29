# -*- coding: utf-8 -*-
"""
模块: root
文件名: main.py
作者: xionggaoqing
时间: 2026-10-27
说明: 本地调试一键启动脚本——同时拉起后端（uvicorn）与前端（Vite dev server）。

    用法：
        python main.py                 # 启动后端 8000 + 前端 5173（开发模式，前端热更新）
        python main.py --prod          # 后端单体模式（挂载 src/web/dist，只开 8000）
        python main.py --backend-only  # 只启动后端
        python main.py --frontend-only # 只启动前端（需后端已在 8000 运行）
        python main.py --port 9000     # 指定后端端口（前端代理自动跟随）
        python main.py --no-browser    # 启动后不自动打开浏览器

    ★默认在端口就绪后用系统默认浏览器**主动打开前端页面**（单体模式打开后端同源页面）。
    Ctrl+C 同时优雅停止两个进程。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "src" / "web"
BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
FRONTEND_PORT = 5173


def _is_windows() -> bool:
    return os.name == "nt"


def _open_browser(url: str, port: int, *, timeout: float = 40.0) -> None:
    """等端口就绪后用系统默认浏览器打开页面（后台线程，不阻塞启动流程）。

    端口迟迟未就绪则放弃打开，只打印提示——不因为浏览器问题影响服务启动。
    """

    def _worker() -> None:
        if _wait_port(port, timeout=timeout):
            try:
                if webbrowser.open(url, new=2):
                    print(f"[main] 已在浏览器打开 → {url}")
                    return
            except Exception as exc:  # 无 GUI / 浏览器未注册
                print(f"[main] ⚠ 打开浏览器失败：{exc}")
        else:
            print(f"[main] ⚠ {url} 端口 {timeout:.0f}s 内未就绪，未自动打开浏览器")
        print(f"[main] 可手动访问 → {url}")

    threading.Thread(target=_worker, name="open-browser", daemon=True).start()


def _has_uv() -> bool:
    from shutil import which

    return which("uv") is not None


def _backend_cmd(port: int, prod: bool) -> list[str]:
    """后端启动命令。

    优先 `uv run writer dev`（项目 .venv 由 uv 管理）；
    无 uv 时回落当前解释器直跑 uvicorn（需 PYTHONPATH=src，见 _backend_env）。
    """
    if _has_uv():
        if prod:
            return [
                "uv", "run", "python", "-m", "uvicorn", "app.main:app",
                "--host", BACKEND_HOST, "--port", str(port),
            ]
        return ["uv", "run", "writer", "dev", "--host", BACKEND_HOST, "--port", str(port)]
    return [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", BACKEND_HOST, "--port", str(port),
    ]


def _backend_env() -> dict[str, str]:
    """无 uv 回落时补 PYTHONPATH=src（uv run 场景包已装，无需设置）。"""
    if _has_uv():
        return {}
    existing = os.environ.get("PYTHONPATH", "")
    src = str(ROOT / "src")
    return {"PYTHONPATH": f"{src}{os.pathsep}{existing}" if existing else src}


def _frontend_cmd() -> list[str]:
    """前端 Vite dev server（/api 代理到后端，目标由 VITE_BACKEND 环境变量指定）。"""
    npm = "npm.cmd" if _is_windows() else "npm"
    return [npm, "run", "dev"]


def _spawn(cmd: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    env = {**os.environ, **(extra_env or {})}
    # 子进程继承当前控制台（日志混合输出，不弹新窗口）。
    # Windows 不加 CREATE_NEW_PROCESS_GROUP：该 flag 会使子进程收不到控制台 Ctrl+C；
    # Ctrl+C 由 main.py 捕获 KeyboardInterrupt 后统一走 _terminate 清理。
    if _is_windows():
        # Windows 跨盘符时 cwd 参数对 .cmd 文件不生效，用 cmd /c cd /d 强制切换
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        cmd = ["cmd", "/c", f"cd /d {cwd} && {cmd_str}"]
        kwargs: dict = {"env": env}
    else:
        kwargs: dict = {"cwd": str(cwd), "env": env}
        # POSIX：独立进程组，使 _terminate 的 killpg 只杀子进程树、不波及 main.py 自身
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)  # noqa: S603


def _ensure_frontend_deps() -> bool:
    """node_modules 缺失时自动 npm install。"""
    if (WEB_DIR / "node_modules").is_dir():
        return True
    print("[main] 前端依赖缺失，正在安装（npm install）…")
    npm = "npm.cmd" if _is_windows() else "npm"
    if _is_windows():
        # Windows 跨盘符时 cwd 参数对 .cmd 文件不生效，用 cmd /c cd /d 强制切换
        cmd = ["cmd", "/c", f"cd /d {WEB_DIR} && {npm} install"]
    else:
        cmd = [npm, "install"]
    result = subprocess.run(cmd, cwd=str(WEB_DIR) if not _is_windows() else None, check=False)  # noqa: S603,S607
    return result.returncode == 0


def _wait_port(port: int, timeout: float = 30.0) -> bool:
    """轮询等待端口就绪。"""
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((BACKEND_HOST, port)) == 0:
                return True
        time.sleep(0.4)
    return False


def _terminate(proc: subprocess.Popen) -> None:
    """优雅停止子进程（含其子进程树，如 uv/npm 派生的实际进程）。"""
    if proc.poll() is not None:
        return
    try:
        if _is_windows():
            # Windows：taskkill /T 终止整棵进程树（uv→python、npm→node）
            subprocess.run(  # noqa: S603,S607
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_dev(backend_port: int, *, open_browser: bool = True) -> int:
    """开发模式：后端（reload）+ 前端（Vite HMR）双进程。"""
    print(f"[main] 后端  → http://{BACKEND_HOST}:{backend_port}  (API 文档 /api/docs)")
    backend = _spawn(_backend_cmd(backend_port, prod=False), ROOT, extra_env=_backend_env())

    if not _wait_port(backend_port, timeout=45):
        print("[main] ⚠ 后端启动超时（仍继续拉起前端，可稍后自行检查日志）")

    if not _ensure_frontend_deps():
        print("[main] ✗ 前端依赖安装失败，仅后端可用")
        _terminate(backend)
        return 1

    frontend_env = {"VITE_BACKEND": f"http://{BACKEND_HOST}:{backend_port}"}
    print(f"[main] 前端  → http://localhost:{FRONTEND_PORT}  (Ctrl+C 同时停止两端)")
    frontend = _spawn(_frontend_cmd(), WEB_DIR, extra_env=frontend_env)

    # ★Vite dev server 就绪后主动打开前端页面
    if open_browser:
        _open_browser(f"http://localhost:{FRONTEND_PORT}", FRONTEND_PORT)

    try:
        while True:
            if backend.poll() is not None:
                print(f"[main] 后端已退出（code={backend.returncode}），停止前端")
                break
            if frontend.poll() is not None:
                print(f"[main] 前端已退出（code={frontend.returncode}），停止后端")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[main] 收到 Ctrl+C，正在停止…")
    finally:
        _terminate(frontend)
        _terminate(backend)
    return 0


def run_prod(backend_port: int, *, open_browser: bool = True) -> int:
    """单体模式：前端产物由 FastAPI 挂载，只开后端一个端口。"""
    dist = WEB_DIR / "dist"
    if not dist.is_dir():
        print("[main] src/web/dist 不存在，先构建前端…")
        if not _ensure_frontend_deps():
            return 1
        npm = "npm.cmd" if _is_windows() else "npm"
        if _is_windows():
            # Windows 跨盘符时 cwd 参数对 .cmd 文件不生效，用 cmd /c cd /d 强制切换
            cmd = ["cmd", "/c", f"cd /d {WEB_DIR} && {npm} run build"]
        else:
            cmd = [npm, "run", "build"]
        result = subprocess.run(cmd, cwd=str(WEB_DIR) if not _is_windows() else None, check=False)  # noqa: S603,S607
        if result.returncode != 0:
            print("[main] ✗ 前端构建失败")
            return 1
    print(f"[main] 单体模式 → http://{BACKEND_HOST}:{backend_port}  (Ctrl+C 停止)")
    backend = _spawn(_backend_cmd(backend_port, prod=True), ROOT, extra_env=_backend_env())

    # ★单体模式下前端由后端同源挂载，直接打开后端地址
    if open_browser:
        _open_browser(f"http://{BACKEND_HOST}:{backend_port}", backend_port)

    try:
        return backend.wait()
    except KeyboardInterrupt:
        print("\n[main] 收到 Ctrl+C，正在停止…")
        _terminate(backend)
        return 0


def run_backend_only(backend_port: int, *, open_browser: bool = True) -> int:
    backend = _spawn(_backend_cmd(backend_port, prod=False), ROOT, extra_env=_backend_env())
    print(f"[main] 仅后端 → http://{BACKEND_HOST}:{backend_port}")
    if open_browser:
        _open_browser(f"http://{BACKEND_HOST}:{backend_port}", backend_port)
    try:
        return backend.wait()
    except KeyboardInterrupt:
        _terminate(backend)
        return 0


def run_frontend_only(backend_port: int, *, open_browser: bool = True) -> int:
    if not _ensure_frontend_deps():
        return 1
    frontend = _spawn(
        _frontend_cmd(), WEB_DIR,
        extra_env={"VITE_BACKEND": f"http://{BACKEND_HOST}:{backend_port}"},
    )
    print(f"[main] 仅前端 → http://localhost:{FRONTEND_PORT}（代理到 {backend_port}）")
    if open_browser:
        _open_browser(f"http://localhost:{FRONTEND_PORT}", FRONTEND_PORT)
    try:
        return frontend.wait()
    except KeyboardInterrupt:
        _terminate(frontend)
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Writer Pro 本地调试启动器")
    parser.add_argument("--port", type=int, default=DEFAULT_BACKEND_PORT, help="后端端口（默认 8000）")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动后不自动打开浏览器（默认会在端口就绪后用系统默认浏览器打开前端页面）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prod", action="store_true", help="单体模式（构建前端 + 只开后端）")
    mode.add_argument("--backend-only", action="store_true", help="只启动后端")
    mode.add_argument("--frontend-only", action="store_true", help="只启动前端（需后端已运行）")
    args = parser.parse_args()

    open_browser = not args.no_browser

    if args.frontend_only:
        return run_frontend_only(args.port, open_browser=open_browser)
    if args.backend_only:
        return run_backend_only(args.port, open_browser=open_browser)
    if args.prod:
        return run_prod(args.port, open_browser=open_browser)
    return run_dev(args.port, open_browser=open_browser)


if __name__ == "__main__":
    raise SystemExit(main())
