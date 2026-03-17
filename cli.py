#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_URL = "https://github.com/openclaw/openclaw.git"
REPO_DIR = Path("openclaw")
COMPOSE_FILE = Path("podman-compose.yml")


class SetupError(RuntimeError):
    pass


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    try:
        subprocess.run(
            cmd,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else {**os.environ, **env},
            check=True,
        )
    except FileNotFoundError as e:
        raise SetupError(f"未找到可执行文件: {cmd[0]}") from e
    except subprocess.CalledProcessError as e:
        raise SetupError(f"命令执行失败: {' '.join(cmd)}") from e


def _run_capture(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=None if cwd is None else str(cwd),
            env=None if env is None else {**os.environ, **env},
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout
    except FileNotFoundError as e:
        raise SetupError(f"未找到可执行文件: {cmd[0]}") from e
    except subprocess.CalledProcessError as e:
        raise SetupError(f"命令执行失败: {' '.join(cmd)}") from e


def _detect_playwright_chromium_executable_path(image: str) -> str:
    if not shutil.which("podman"):
        raise SetupError("未找到 podman，无法探测 Chromium 可执行路径")
    out = _run_capture(
        [
            "podman",
            "run",
            "--rm",
            image,
            "sh",
            "-lc",
            "ls -1 "
            "/home/node/.cache/ms-playwright/chromium-*/chrome-linux/chrome "
            "/home/node/.cache/ms-playwright/chromium-*/chrome-linux64/chrome "
            "2>/dev/null | head -n 1",
        ],
    ).strip()
    if not out:
        raise SetupError("未找到已安装的 Chromium，可尝试重新执行: ./cli.py setup --with-browser")
    return out.splitlines()[0].strip()


def _prompt_yes_no(prompt: str, *, default_yes: bool = True) -> bool:
    suffix = " [Y/n] " if default_yes else " [y/N] "
    while True:
        try:
            raw = input(prompt + suffix).strip().lower()
        except EOFError:
            return default_yes
        if raw == "":
            return default_yes
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False


def _resolve_compose_cmd() -> list[str]:
    if shutil.which("podman-compose"):
        return ["podman-compose", "--in-pod=false"]
    if shutil.which("podman"):
        return ["podman", "compose"]
    raise SetupError("未找到 podman-compose 或 podman")


def _print_session_persistence_hint() -> None:
    print(
        "提示：rootless Podman 容器可能会在所有 SSH 会话退出后被系统回收。"
        "如需保持后台运行，可启用 lingering：sudo loginctl enable-linger $(id -un)。",
        file=sys.stderr,
    )


def _resolve_runtime_context(
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env: dict[str, str] = {} if base_env is None else dict(base_env)

    env.setdefault("OPENCLAW_RUN_UID", str(os.getuid()))
    env.setdefault("OPENCLAW_RUN_GID", str(os.getgid()))

    image = (os.environ.get("OPENCLAW_IMAGE") or env.get("OPENCLAW_IMAGE") or "openclaw:local").strip() or "openclaw:local"
    env.setdefault("OPENCLAW_IMAGE", image)

    config_dir = Path(
        os.environ.get(
            "OPENCLAW_CONFIG_DIR",
            env.get("OPENCLAW_CONFIG_DIR", str(Path("data/openclaw"))),
        ),
    ).resolve()
    workspace_dir = Path(
        os.environ.get(
            "OPENCLAW_WORKSPACE_DIR",
            env.get("OPENCLAW_WORKSPACE_DIR", str(config_dir / "workspace")),
        ),
    ).resolve()

    env.setdefault("OPENCLAW_CONFIG_DIR", str(config_dir))
    env.setdefault("OPENCLAW_WORKSPACE_DIR", str(workspace_dir))

    uid = env["OPENCLAW_RUN_UID"]
    gid = env["OPENCLAW_RUN_GID"]
    return {
        "image": image,
        "uid": uid,
        "gid": gid,
        "config_dir": str(config_dir),
        "workspace_dir": str(workspace_dir),
        "env": env,
    }


def _ensure_minimal_openclaw_config(
    config_dir: Path,
    *,
    gateway_port: str,
    with_browser: bool,
    browser_executable_path: str | None = None,
) -> None:
    config_path = config_dir / "openclaw.json"

    if config_path.exists():
        print(f"检测到配置文件已存在，跳过自动生成/修改: {config_path}", file=sys.stderr)
        return

    browser_block = '"browser": { "enabled": true, "headless": true, "noSandbox": true }'
    if with_browser and browser_executable_path:
        escaped = browser_executable_path.replace("\\", "\\\\").replace('"', '\\"')
        browser_block = (
            '"browser": { "enabled": true, "headless": true, "noSandbox": true, '
            f'"executablePath": "{escaped}" }}'
        )


    config = (
        '{ "gateway": { "mode": "local", "controlUi": { "allowedOrigins": ['
        f'"http://127.0.0.1:{gateway_port}", '
        f'"http://localhost:{gateway_port}"'
        "] } }"
    )
    if with_browser:
        config += f", {browser_block}"
    config += " }\n"
    config_path.write_text(config, encoding="utf-8")


def _run_openclaw_cli(ctx: dict[str, str], args: list[str]) -> None:
    if not shutil.which("podman"):
        raise SetupError("未找到 podman，无法执行 openclaw CLI")
    if not args:
        raise SetupError("缺少 openclaw CLI 参数")

    resolved_env = ctx["env"]
    image = ctx["image"]
    uid = ctx["uid"]
    gid = ctx["gid"]
    config_dir = ctx["config_dir"]
    workspace_dir = ctx["workspace_dir"]

    if not Path(config_dir).exists() or not Path(workspace_dir).exists():
        raise SetupError("未检测到已初始化的数据目录，请先执行: ./cli.py setup")

    verb = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "-" for ch in args[0].strip()) or "cli"
    name = f"openclaw-cli-{verb}-{os.getpid()}"
    _run(
        [
            "podman",
            "run",
            "--rm",
            "-it",
            "--name",
            name,
            "--userns=keep-id",
            "--user",
            f"{uid}:{gid}",
            "--env",
            "HOME=/home/node",
            "--env",
            "TERM=xterm-256color",
            "--volume",
            f"{config_dir}:/home/node/.openclaw:Z",
            "--volume",
            f"{workspace_dir}:/home/node/.openclaw/workspace:Z",
            image,
            "node",
            "openclaw.mjs",
            *args,
        ],
        env=resolved_env,
    )


def cmd_setup(ctx: dict[str, str], *, with_browser: bool = False) -> int:
    if REPO_DIR.exists():
        if (REPO_DIR / ".git").exists():
            print(f"仓库已存在: {REPO_DIR}")
        else:
            raise SetupError(f"仓库目录已存在但不是 Git 仓库: {REPO_DIR}")
    else:
        _run(["git", "clone", "--depth", "1", REPO_URL, str(REPO_DIR)])
        print(f"完成: {REPO_DIR}")
    if not COMPOSE_FILE.exists():
        raise SetupError(f"缺少编排文件: {COMPOSE_FILE}")
    env = ctx["env"]
    if with_browser:
        env.setdefault("OPENCLAW_INSTALL_BROWSER", "1")
    config_dir = Path(ctx["config_dir"])
    workspace_dir = Path(ctx["workspace_dir"])
    config_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    gateway_port = os.environ.get("OPENCLAW_GATEWAY_PORT", "18789").strip() or "18789"
    compose = _resolve_compose_cmd()
    config_path = config_dir / "openclaw.json"
    image = ctx["image"]
    if with_browser and not config_path.exists():
        _run([*compose, "-f", str(COMPOSE_FILE), "up", "-d", "--build"], env=env)
        executable_path = _detect_playwright_chromium_executable_path(image)
        _ensure_minimal_openclaw_config(
            config_dir,
            gateway_port=gateway_port,
            with_browser=with_browser,
            browser_executable_path=executable_path,
        )
    else:
        _ensure_minimal_openclaw_config(config_dir, gateway_port=gateway_port, with_browser=with_browser)
        _run([*compose, "-f", str(COMPOSE_FILE), "up", "-d", "--build"], env=env)

    _print_session_persistence_hint()
    if _prompt_yes_no("是否继续进行交互式配置向导？", default_yes=True):
        _run_openclaw_cli(ctx, ["configure"])
    return 0


def cmd_up(ctx: dict[str, str]) -> int:
    if not COMPOSE_FILE.exists():
        raise SetupError(f"缺少编排文件: {COMPOSE_FILE}")
    compose = _resolve_compose_cmd()
    _run([*compose, "-f", str(COMPOSE_FILE), "up", "-d"], env=ctx["env"])
    _print_session_persistence_hint()
    return 0


def cmd_down(ctx: dict[str, str]) -> int:
    if not COMPOSE_FILE.exists():
        raise SetupError(f"缺少编排文件: {COMPOSE_FILE}")
    compose = _resolve_compose_cmd()
    _run([*compose, "-f", str(COMPOSE_FILE), "down"], env=ctx["env"])
    return 0


def cmd_logs(ctx: dict[str, str]) -> int:
    if not COMPOSE_FILE.exists():
        raise SetupError(f"缺少编排文件: {COMPOSE_FILE}")
    compose = _resolve_compose_cmd()
    _run([*compose, "-f", str(COMPOSE_FILE), "logs", "-f"], env=ctx["env"])
    return 0


def cmd_configure(ctx: dict[str, str]) -> int:
    _run_openclaw_cli(ctx, ["configure"])
    return 0

def cmd_shell(ctx: dict[str, str]) -> int:
    if not shutil.which("podman"):
        raise SetupError("未找到 podman，无法进入容器")
    name = os.environ.get("OPENCLAW_GATEWAY_CONTAINER", "openclaw-gateway").strip() or "openclaw-gateway"
    _run(["podman", "exec", "-it", name, "bash"], env=ctx["env"])
    return 0


def cmd_update(ctx: dict[str, str], *, with_browser: bool = False) -> int:
    if not (REPO_DIR / ".git").exists():
        raise SetupError("未检测到已初始化的 ./openclaw 仓库，请先执行: ./cli.py setup")
    if not COMPOSE_FILE.exists():
        raise SetupError(f"缺少编排文件: {COMPOSE_FILE}")
    _run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"])
    compose = _resolve_compose_cmd()
    env = ctx["env"]
    if with_browser:
        env.setdefault("OPENCLAW_INSTALL_BROWSER", "1")
    _run([*compose, "-f", str(COMPOSE_FILE), "up", "-d", "--build"], env=env)
    _print_session_persistence_hint()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        add_help=True,
        description="openclaw-setup 的快捷命令封装（Podman Compose 部署 + 常用运维）。",
    )
    sub = parser.add_subparsers(dest="command")
    setup = sub.add_parser(
        "setup",
        help="克隆 ./openclaw 并构建/启动服务（可选进入交互式配置向导）",
        description="克隆 OpenClaw 官方仓库到 ./openclaw，构建镜像并启动服务。",
    )
    setup.add_argument(
        "--with-browser",
        action="store_true",
        help="构建镜像时安装 Chromium（会明显增大镜像体积）",
    )
    sub.add_parser(
        "up",
        help="启动服务（不重建镜像）",
        description="启动 podman compose 服务。",
    )
    sub.add_parser(
        "down",
        help="停止并移除容器（保留数据目录）",
        description="停止并移除 podman compose 容器，不删除本地数据目录。",
    )
    sub.add_parser(
        "logs",
        help="查看服务日志（跟随输出）",
        description="查看 podman compose 日志并持续跟随输出。",
    )
    sub.add_parser(
        "configure",
        aliases=["config"],
        help="进入交互式配置向导",
        description="在独立容器中执行 openclaw configure 交互式向导。",
    )
    sub.add_parser(
        "shell",
        help="进入网关容器（bash）",
        description="进入网关容器的 bash shell。",
    )
    update = sub.add_parser(
        "update",
        help="更新 ./openclaw 并重建/重启服务",
        description="拉取 ./openclaw 最新代码，并重建镜像后重启服务。",
    )
    update.add_argument(
        "--with-browser",
        action="store_true",
        help="重建镜像时安装 Chromium（会明显增大镜像体积）",
    )
    return parser


def main() -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            return 0

        ctx = _resolve_runtime_context()
        if args.command == "setup":
            return cmd_setup(ctx, with_browser=bool(getattr(args, "with_browser", False)))
        if args.command == "up":
            return cmd_up(ctx)
        if args.command == "down":
            return cmd_down(ctx)
        if args.command == "logs":
            return cmd_logs(ctx)
        if args.command in {"configure", "config"}:
            return cmd_configure(ctx)
        if args.command == "shell":
            return cmd_shell(ctx)
        if args.command == "update":
            return cmd_update(ctx, with_browser=bool(getattr(args, "with_browser", False)))
        raise SetupError(f"未知命令: {args.command}")
    except KeyboardInterrupt:
        return 130
    except SetupError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
