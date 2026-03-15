# openclaw-setup（自用 Podman 启动脚本）

自用的 OpenClaw 网关启动/运维脚本：在当前目录拉取 `openclaw/` 源码，使用其 `openclaw/Dockerfile` 构建镜像，并通过 Podman Compose 启动 `openclaw-gateway`。

约定：

- 数据目录固定为 `./data/`（配置与工作区都放这里）
- 配置文件 `./data/openclaw/openclaw.json` 存在则不修改（只提示跳过）
- `configure` 在独立一次性容器中运行，避免主容器因配置加载/重载中断流程

## 依赖

- Podman（rootless）
- Git
- Python 3
- `podman-compose` 或 `podman compose`

## 用法

- 初始化（拉取源码 + 构建镜像 + 启动服务）

```bash
./cli.py setup
```

- 初始化时安装 Chromium（写入可用的 `browser.executablePath` 到新生成的 `openclaw.json`）

```bash
./cli.py setup --with-browser
```

- 交互式配置向导（别名：`config`）

```bash
./cli.py configure
./cli.py config
```

- 查看日志（Ctrl-C 直接退出）

```bash
./cli.py logs
```

## Token 与设备配对

- 查看网关 token

```bash
jq -r '.gateway.auth.token' ./data/openclaw/openclaw.json
```

- 首次设备配对

1. 打开 Control UI，在连接/登录处填入上面的 token
2. 当有设备发起配对请求后，执行授权：

```bash
./cli.py shell
openclaw devices list
openclaw devices approve <requestId>
```

`<requestId>` 从 `openclaw devices list` 输出中获取。

## 命令

- `./cli.py setup [--with-browser]`
- `./cli.py up`
- `./cli.py down`
- `./cli.py logs`
- `./cli.py configure`（别名：`config`）
- `./cli.py shell`
- `./cli.py update [--with-browser]`

## 数据目录

- `./data/openclaw`：配置目录（容器内 `/home/node/.openclaw`）
- `./data/openclaw/workspace`：工作区目录
