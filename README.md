# 影序 CineFlow

> 中文优先、可 Docker 自托管的影视库整理工具：扫描文件 → 解析文件名 → 匹配 TMDb → 人工纠错 → 按模板移动 / 重命名 → 生成 NFO 与图片。

[English](README.en.md) | **中文**

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)

---

## 这是什么

CineFlow（影序）只处理你已有的媒体文件：

- 支持本地目录、WebDAV（OpenList / Alist、Nextcloud、群晖等）、SMB / Samba；
- 自动识别电影 / 剧集、季集、画质、HDR、音轨、发布组等信息；
- 匹配 TMDb 元数据，允许人工搜索、批量纠错、修正季集；
- 按模板把文件移动到目标目录，自动带上字幕、音轨、NFO、海报等附件；
- 生成 Emby / Jellyfin / Plex / Kodi / Infuse 可读的 NFO 和图片；
- 支持「仅重命名」，不移动、不刮削，只按规则改文件名 / 文件夹名。

**它不提供片源、下载、播放或 DRM 绕过。**

---

## 功能

### 文件与扫描
- 本地 / WebDAV / SMB 文件源，支持测试连接、新建目录。
- 递归扫描并跳过隐藏目录、回收站目录（如 `Filmix.Trash`）、符号链接目录。
- 「全部文件」双视图：
  - **仅视频**：按真实目录结构浏览待整理文件；
  - **所有文件**：浏览视频、字幕、音轨、NFO、海报和其他文件，支持表头排序、批量移动 / 删除、按包含文本或正则规则预览清理。
- 删除 / 移动后同步条目与手动关联，避免幽灵记录。

### 识别与匹配
- 中文 / 英文标题、年份、`S01E02`、`第01集`、`1x02`、多集、分辨率、HDR、音轨、发布组等解析。
- TMDb 自动匹配、候选评分、人工搜索、批量纠错、整部换剧集、集数偏移。
- 可选 AI 文件名解析兜底：只发送文件名，结果再与 TMDb 交叉验证。
- AI token / 费用统计：支持缓存命中 / 未命中 / 输出三档价、峰谷倍率、中国法定节假日，以及平台返回费用、OpenRouter / LiteLLM 价格同步。

### 整理与刮削
- 电影 / 剧集 / 季目录 / 文件名四层模板，支持分类目录、季文件夹、附加信息顺序与分隔符。
- 电影 NFO 与最终视频同名；单集 NFO 与视频同名；`tvshow.nfo` / `season.nfo` 固定名。
- 自动跟随同目录及 `Subs` / `Sub` / `Subtitles` 子目录的字幕、音轨；支持手动关联到当前视频或同目录其他集。
- 同名冲突自动加 `(1)`，不覆盖原文件；整理可重试且幂等。

### 其他
- 任务中心：队列、进度、日志、取消。
- 附加信息自定义词库：影视版本 / 发布标签 / 发布组可扩展。
- 单管理员密码、scrypt、签名会话 Cookie、Fernet 加密文件源密钥。
- 响应式界面，手机与桌面均可使用。

---

## 快速开始（Docker）

预构建镜像：`ghcr.io/oz6d/cineflow:latest`，支持 `linux/amd64` 和 `linux/arm64`。包地址：[GHCR](https://github.com/Oz6d/Cineflow/pkgs/container/cineflow)。

下面两种方式选一种即可，不要同时运行；使用预构建镜像不需要克隆整个仓库。

### 方式一：docker compose（推荐）

新建一个部署目录：

```bash
mkdir -p cineflow
cd cineflow
```

然后在目录里保存下面两个文件。

#### `docker-compose.yml`

```yaml
services:
  cineflow:
    image: ghcr.io/oz6d/cineflow:latest
    container_name: cineflow
    restart: unless-stopped
    ports:
      - "${CINEFLOW_HTTP_PORT:-8080}:8080"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      CINEFLOW_DATA_DIR: /data
      CINEFLOW_ADMIN_PASSWORD: ${CINEFLOW_ADMIN_PASSWORD:-change-me}
      CINEFLOW_SECRET_KEY: ${CINEFLOW_SECRET_KEY:-}
      CINEFLOW_COOKIE_SECURE: ${CINEFLOW_COOKIE_SECURE:-false}
      CINEFLOW_SESSION_DAYS: ${CINEFLOW_SESSION_DAYS:-14}
      CINEFLOW_DEMO: ${CINEFLOW_DEMO:-false}
      PUID: ${PUID:-}
      PGID: ${PGID:-}
    volumes:
      - ./data:/data
      - ${MEDIA_PATH:-./media}:/media
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

#### `.env`

```dotenv
# 必须改成唯一强密码；change-me 只是示例值
CINEFLOW_ADMIN_PASSWORD=replace-with-a-strong-password

# 宿主机媒体目录，会挂载到容器内 /media
MEDIA_PATH=/path/to/your/media

# 对外访问端口
CINEFLOW_HTTP_PORT=8080

# 时区
TZ=Asia/Shanghai
```

#### 启动

```bash
docker compose pull
docker compose up -d
```

升级：

```bash
docker compose pull
docker compose up -d
```

查看日志：

```bash
docker compose logs -f cineflow
```

如果你要基于本地源码构建，而不是用预构建镜像：

```bash
git clone https://github.com/Oz6d/Cineflow.git
cd Cineflow
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### 方式二：docker run（不使用 compose）

适合不想创建 compose 文件、只想直接跑镜像的情况。

```bash
mkdir -p data
docker pull ghcr.io/oz6d/cineflow:latest

docker run -d \
  --name cineflow \
  --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Asia/Shanghai \
  -e CINEFLOW_DATA_DIR=/data \
  -e CINEFLOW_ADMIN_PASSWORD='replace-with-a-strong-password' \
  -v "$PWD/data:/data" \
  -v "/path/to/your/media:/media" \
  ghcr.io/oz6d/cineflow:latest
```

说明：

- 把 `replace-with-a-strong-password` 换成你自己的强密码，不要直接使用示例值；
- `-v "$PWD/data:/data"` 保存数据库和密钥，**不要删除这个目录**；
- `-v "/path/to/your/media:/media"` 是媒体目录；只用 WebDAV / SMB 时可以去掉；
- 查看日志：`docker logs -f cineflow`；
- 升级：`docker pull ghcr.io/oz6d/cineflow:latest`，然后删除旧容器并按上面的 `docker run` 重新启动（保留 `./data`）。

### 启动后配置

打开 `http://localhost:8080`，用你设置的管理员密码登录，然后：

1. 「文件源」添加本地目录 / WebDAV / SMB，先点「测试连接」；
2. 「设置 → TMDb」填写 API Key 或 v4 Read Access Token；
3. 「影视库」新建库，选择文件源、待整理目录、整理后目录；
4. 「扫描识别」→「待确认」纠错 →「整理预览」→「整理」。

> 首次启动后建议在「设置 → 安全」修改密码。
> 数据保存在 `./data`，请定期备份；`data/secret.key` 不要泄露。
> 如果 GHCR 包不可见，请在 GitHub 仓库的 **Packages → cineflow → Package settings** 中把可见性设为 Public。

---

## 本地开发

### 后端

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

CINEFLOW_DATA_DIR=../data CINEFLOW_DEMO=true \
  uvicorn app.main:app --reload --port 8080
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

Vite 默认运行在 `http://localhost:5173`，`/api` 会代理到 `http://127.0.0.1:8080`。

### 测试

```bash
cd backend
.venv/bin/python ../tests/test_parser.py
.venv/bin/python ../tests/test_naming.py
.venv/bin/python ../tests/test_attachments.py
.venv/bin/python ../tests/test_attachment_subdirs.py
.venv/bin/python ../tests/test_scraper.py
.venv/bin/python ../tests/test_webdav.py
.venv/bin/python ../tests/test_files_api.py
.venv/bin/python ../tests/test_rename.py
.venv/bin/python ../tests/test_presence_cache.py
.venv/bin/python ../tests/test_ai_usage.py
.venv/bin/python ../tests/test_ai_pricing.py
.venv/bin/python ../tests/test_ai_holidays.py
```

前端检查：

```bash
cd frontend
npx tsc --noEmit
npm run build
```

---

## 目录结构

```text
backend/app/
├── api/                  # auth / sources / libraries / items / tasks / settings
├── core/
│   ├── parser.py         # 文件名、季集、标签解析
│   ├── tag_dictionary.py # 附加信息自定义词库
│   ├── matcher.py        # TMDb 候选与评分
│   ├── naming.py         # 命名模板与冲突处理
│   ├── attachments.py    # 附件分类 / 匹配
│   ├── attachment_links.py
│   ├── organizer.py      # 移动、附件跟随、重名处理
│   ├── renamer.py        # 仅重命名
│   ├── runner.py         # 扫描 / 整理 / 重命名任务
│   ├── scraper.py        # NFO 与图片
│   ├── metadata/         # TMDb、AI、占位图
│   └── providers/        # local / WebDAV / SMB
├── db.py                 # SQLite Schema
├── security.py
└── main.py

frontend/src/
├── pages/                # 总览 / 影视库 / 任务 / 文件源 / 设置
├── components/           # Layout、FileBrowser、AttachmentPanel 等
├── lib/                  # API、hooks、格式化、Toast
└── types.ts

tests/                    # 回归测试
docker/                   # 容器入口
```

---

## 配置说明

### TMDb

在「设置 → TMDb」填写 v3 API Key 或 v4 Read Access Token。海报等图片由后端代理并缓存，客户端不直连 TMDb 图床。

### AI 解析兜底（可选）

支持任意 OpenAI 兼容接口（OpenAI、DeepSeek、Ollama、SiliconFlow 等）：

- 只发送文件名，不发送文件内容；
- 结果仍会与 TMDb 交叉验证；
- 可配置三档价格、峰谷时段、中国法定节假日；
- 可优先使用平台返回的 `usage.cost`，或从 OpenRouter / LiteLLM 同步模型价格；
- 费用为估算值，实际账单请以服务商为准。

### 文件源

- **WebDAV**：地址通常需要以 `/dav` 结尾；OpenList / Alist 可先在浏览器确认。
- **SMB**：填写主机、共享名、用户名、密码和子目录。
- 所有整理操作限制在当前影视库的待整理根目录内。

---

## 安全与隐私

- 单管理员密码；密码使用 scrypt 哈希。
- 会话 Cookie 使用 HMAC 签名；可选 HTTPS Secure Cookie。
- WebDAV / SMB 密码、TMDb Key、AI Key 使用 Fernet 加密后存入 SQLite。
- 数据目录包含数据库、密钥和演示文件，默认不纳入 Git。
- 不上传媒体文件；AI 兜底只发送文件名。

---

## 已知限制

- 不提供片源、下载、播放或 DRM 绕过。
- 「所有文件」删除没有回收站，无法撤销；递归规则会跳过隐藏目录、回收站和符号链接目录。
- WebDAV 大量移动可能较慢；服务器只完成复制时会报告失败并保留源文件。
- 附件只自动扫描同目录和下一层常见字幕子目录（`Subs` / `Sub` / `Subtitles` / `字幕` 等）。
- 海报墙真实性校验缓存约 20 秒；外部客户端直接改名 / 删除最多延迟缓存过期。
- AI 费用统计依赖服务商返回的 usage / cost；历史调用无法回溯。
- 中国法定节假日自动同步依赖外网数据源，失败时保留旧日期。

---

## 免责声明

- 本项目只做文件重命名、移动和元数据整理，不提供、不下载、不播放任何影视内容。
- 请只整理你拥有合法访问权的媒体文件，并遵守所在地区的版权法规。
- 本项目使用 TMDb API，但未经 TMDb 认可或认证。
- 产品灵感来自 Filmix，但为完全独立实现，不包含对方私有代码或接口。

## License

[MIT](LICENSE)
