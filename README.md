# 影序 CineMint

> 中文优先、可 Docker 自托管的影视库整理工具：扫描文件 → 解析文件名 → 匹配 TMDb → 人工纠错 → 按模板移动 / 重命名 → 生成 NFO 与图片。

[English](README.en.md) | **中文**

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![Version](https://img.shields.io/badge/version-0.4.10-2dd4bf)
![Docker](https://img.shields.io/badge/docker-ready-2496ed)

---

## 这是什么

CineMint（影序）只处理你已有的媒体文件：

- 支持本地目录、WebDAV（OpenList / Alist、Nextcloud、群晖等）、SMB / Samba；
- 自动识别电影 / 剧集、季集、画质、HDR、音轨、发布组等信息；
- 匹配 TMDb 元数据，允许人工搜索、批量纠错、修正季集；
- 按模板把文件移动到目标目录，自动带上字幕、音轨、NFO、海报等附件；
- 生成 Emby / Jellyfin / Plex / Kodi / Infuse 可读的 NFO 和图片；
- 支持「仅重命名」，不移动、不刮削，只按规则改文件名 / 文件夹名。
- 同名文件可配置为「自动加 (1) / 跳过 / 覆盖」；每部影视库有默认策略，夸克 / RSS 订阅可单独覆盖，跳过时源文件留在暂存目录，覆盖时先备份旧目标再替换。
- 匹配识别后，文件列表 / 作品详情 / 海报墙预览里会显示「整理后」名称，点铅笔即可修改，整理预览也可直接改；保存后整理 / 仅重命名都会使用该名称，可随时恢复自动命名。

**它不提供片源、下载、播放或 DRM 绕过。**

---

## 功能

### 文件与扫描
- 本地 / WebDAV / SMB 文件源，支持测试连接、新建目录。
- 递归扫描并跳过隐藏目录、回收站目录（如 `Filmix.Trash`）、符号链接目录。
- **待整理目录自动扫描（可选）**：按设定间隔（默认 15 分钟）轻量探测待整理目录，只有视频文件路径或体积变化才真正排队扫描，任务中心标为「自动」；首次开启立即扫描一次，之后不再每轮全量扫描。演示库不参与，同库有任务在跑时跳过。
- 「全部文件」双视图：
  - **仅视频**：按真实目录结构浏览待整理文件；
  - **所有文件**：浏览视频、字幕、音轨、NFO、海报和其他文件，支持表头排序、单个文件 / 文件夹重命名、批量移动 / 删除、按包含文本或正则规则预览清理。
- 删除 / 移动后同步条目与手动关联，避免幽灵记录。

### 识别与匹配
- 中文 / 英文标题、年份、`S01E02`、`第01集`、`1x02`、多集、分辨率、HDR、音轨、发布组等解析。
- TMDb 自动匹配、候选评分、人工搜索、批量纠错、整部换剧集、集数偏移；手动搜索支持翻页（每页 20 条），手动匹配和手工修正的季集信息在重新扫描时保留，「重新自动匹配」可恢复自动识别。
- 海报墙 / 「全部文件」按真实目录校验；外部删除或目录已删除的条目会自动标记为丢失并移出作品计数，网络临时不可用时不会误清。
- 可选 AI 文件名解析：在标题低置信、TMDb 无候选或候选接近时，结合文件名和上级目录提取标题 / 别名 / 季集；AI 标题与别名再回 TMDb 验证并参与候选重排，同一目录只调用一次，结果复用。
- AI token / 费用统计：支持缓存命中 / 未命中 / 输出三档价、峰谷倍率、中国法定节假日，以及平台返回费用、OpenRouter / LiteLLM 价格同步。

### 整理与刮削
- 电影 / 剧集 / 季目录 / 文件名四层模板，支持分类目录、季文件夹、附加信息顺序与分隔符。
- 可选「整理后清理空目录」：视频和附带文件移走后，由深到浅删除已经变空的源目录（包括 `Subs` 等字幕子目录）；源根目录、整理后目录、非空目录和符号链接目录不会被删除。
- 电影 NFO 与最终视频同名；单集 NFO 与视频同名；`tvshow.nfo` / `season.nfo` 固定名。
- 自动跟随同目录及 `Subs` / `Sub` / `Subtitles` 子目录的字幕、音轨；支持手动关联到当前视频或同目录其他集。
- 同名冲突自动加 `(1)`，不覆盖原文件；整理可重试且幂等。

### 订阅与追更
- **夸克订阅**：添加夸克分享链接，定时检查更新并转存到暂存目录，复用影视库扫描与整理流程。支持关键字 / 正则过滤、V2 版本替换（同 TMDb + 季 + 集自动替换旧版本）、定时调度（固定间隔 / 标准 cron）。
- **分享链接目录定位**：可以直接粘贴带 `#/list/share/<目录ID>` 的夸克分享网址，粘贴后自动识别成对应目录；也可以点分享链接右侧的文件夹按钮浏览分享文件，选中目录会同步更新链接。只转存该目录（含子目录）中的文件，不需要再手动选一遍。
- **订阅任务参数**：订阅卡片可提取 / 复制完整任务参数（含提取码），或直接「用此参数新建」；「粘贴任务参数」可快速创建同类任务，识别后进入表单补全影视库、转存目录和执行计划。
- **RSS 订阅**：添加 RSS 源地址，通过 115 云添加下载新条目，同样复用整理流程。支持蜜柑 RSS 和任意标准 RSS / Atom 源；115 支持网页 Cookie 与开放平台 `refresh_token`（自动续期）。
- **追更与放送日历**：蜜柑计划每周放送表（默认）+ Bangumi 放送表（可选开启）+ 蜜柑 RSS 追更进度；可直接关联 RSS / 115 订阅自动下载。支持追更中 / 已暂停 / 已完结状态管理。
- **通知渠道**：23 种推送渠道（Webhook、Bark、Telegram、企业微信、飞书、钉钉等）。订阅每次运行合并成一条推送：标题只用任务名，正文先给转存成功 / 失败 / 整理 / 未识别 / 待确认计数，再列分类文件明细，最后是时间；可按事件订阅筛选，发送日志可查。
- 详细步骤、任务参数、通知结构见下方「订阅与追更」。

### 其他
- 任务中心：队列、进度、日志、取消。
- 附加信息自定义词库：影视版本 / 发布标签 / 发布组可扩展。
- 单管理员密码、scrypt、签名会话 Cookie、Fernet 加密文件源密钥。
- 响应式界面，手机与桌面均可使用；侧边栏含总览、影视库、订阅管理、追更日历、通知渠道、任务中心、文件源、设置。

---

## 快速开始（Docker）

预构建镜像：`ghcr.io/oz6d/cinemint:latest` 和 `docker.io/oz6d/cinemint:latest`，支持 `linux/amd64` 和 `linux/arm64`。GHCR 包地址：[GHCR](https://github.com/Oz6d/CineMint/pkgs/container/cinemint)。

下面两种方式选一种即可，不要同时运行；使用预构建镜像不需要克隆整个仓库。

### 方式一：docker compose（推荐）

新建一个部署目录：

```bash
mkdir -p cinemint
cd cinemint
```

然后在目录里保存下面两个文件。

#### `docker-compose.yml`

```yaml
services:
  cinemint:
    image: ghcr.io/oz6d/cinemint:latest
    container_name: cinemint
    restart: unless-stopped
    ports:
      - "${CINEMINT_HTTP_PORT:-8080}:8080"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      CINEMINT_DATA_DIR: /data
      CINEMINT_ADMIN_PASSWORD: ${CINEMINT_ADMIN_PASSWORD:-change-me}
      CINEMINT_SECRET_KEY: ${CINEMINT_SECRET_KEY:-}
      CINEMINT_COOKIE_SECURE: ${CINEMINT_COOKIE_SECURE:-false}
      CINEMINT_SESSION_DAYS: ${CINEMINT_SESSION_DAYS:-14}
      CINEMINT_DEMO: ${CINEMINT_DEMO:-false}
      CINEMINT_TMDB_API_BASE: ${CINEMINT_TMDB_API_BASE:-https://api.themoviedb.org/3}
      CINEMINT_TMDB_IMAGE_BASE: ${CINEMINT_TMDB_IMAGE_BASE:-https://image.tmdb.org/t/p}
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
CINEMINT_ADMIN_PASSWORD=change-me

# 宿主机媒体目录，会挂载到容器内 /media
MEDIA_PATH=/path/to/your/media

# 对外访问端口
CINEMINT_HTTP_PORT=8080

# 时区
TZ=Asia/Shanghai

# 可选：服务器无法直连 api.themoviedb.org 时，取消注释换用备用 API 入口
# CINEMINT_TMDB_API_BASE=https://api.tmdb.org/3
# CINEMINT_TMDB_IMAGE_BASE=https://image.tmdb.org/t/p
```

#### 启动

实际部署**只需要改两项**：

- `CINEMINT_ADMIN_PASSWORD`：改成你自己的唯一强密码；
- `MEDIA_PATH`：改成宿主机媒体目录。

其余都可以保持默认。`CINEMINT_DATA_DIR` 必须保持 `/data`，它对应 `./data:/data` 数据卷；`CINEMINT_SECRET_KEY` 留空会自动生成；`CINEMINT_COOKIE_SECURE` 只有通过 HTTPS 反向代理访问时才改成 `true`；`PUID` / `PGID` 只有 Linux 下遇到文件权限问题才需要填写。

> 如果服务器无法直连 `api.themoviedb.org`（例如报 `TMDb 请求失败: [Errno 101] Network is unreachable`），在 `.env` 取消注释 `CINEMINT_TMDB_API_BASE=https://api.tmdb.org/3`，再 `docker compose up -d`。图片仍默认走 `image.tmdb.org`。

```bash
docker compose pull
docker compose up -d
```

升级：

```bash
docker compose pull
docker compose up -d
```

#### 从旧版 CineFlow 升级

项目已更名为 CineMint（影序），旧镜像 `oz6d/cineflow` 不再更新。迁移时改三处：

1. `image` / `container_name` 改成 `cinemint`（`ghcr.io/oz6d/cinemint:latest`）；
2. `.env` 与 `environment` 里的 `CINEFLOW_*` 改成 `CINEMINT_*`；
3. 保留原来的 `./data` 挂载。首次启动会自动把 `data/cineflow.db` 连同 `-wal` / `-shm` 重命名为 `cinemint.db`，数据库内容、加密密钥和已保存的 Cookie / API Key 都继续可用。

登录 Cookie 名称也换了，升级后需要重新登录一次；旧的 `CineFlow` 容器和镜像可以删除。若要回滚，把主题名改回 `CineFlow` 的 compose 文件和 `CINEFLOW_*` 环境变量，并把 `cinemint.db` 改回 `cineflow.db` 即可。

查看日志：

```bash
docker compose logs -f cinemint
```

如果你要基于本地源码构建，而不是用预构建镜像：

```bash
git clone https://github.com/Oz6d/CineMint.git
cd CineMint
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

### 方式二：docker run（不使用 compose）

适合不想创建 compose 文件、只想直接跑镜像的情况。

```bash
mkdir -p data
docker pull ghcr.io/oz6d/cinemint:latest

docker run -d \
  --name cinemint \
  --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Asia/Shanghai \
  -e CINEMINT_DATA_DIR=/data \
  -e CINEMINT_ADMIN_PASSWORD='change-me' \
  -v "$PWD/data:/data" \
  -v "/path/to/your/media:/media" \
  ghcr.io/oz6d/cinemint:latest
```

说明：

- 把 `change-me` 换成你自己的强密码，不要直接使用示例值；
- `-v "$PWD/data:/data"` 保存数据库和密钥，**不要删除这个目录**；
- `-v "/path/to/your/media:/media"` 是媒体目录；只用 WebDAV / SMB 时可以去掉；
- 查看日志：`docker logs -f cinemint`；
- 升级：`docker pull ghcr.io/oz6d/cinemint:latest`，然后删除旧容器并按上面的 `docker run` 重新启动（保留 `./data`）。

### 启动后配置

打开 `http://localhost:8080`，用你设置的管理员密码登录，然后：

1. 「文件源」添加本地目录 / WebDAV / SMB，先点「测试连接」；
2. 「设置 → TMDb」填写 API Key 或 v4 Read Access Token；
3. 「影视库」新建库，选择文件源、待整理目录、整理后目录；
4. 「扫描识别」→「待确认」纠错 →「整理预览」→「整理」。

> 首次启动后建议在「设置 → 安全」修改密码。
> 数据保存在 `./data`，请定期备份；`data/secret.key` 不要泄露。
> 如果 GHCR 包不可见，请在 GitHub 仓库的 **Packages → cinemint → Package settings** 中把可见性设为 Public。

---

## 订阅与追更

v0.2.0 新增了完整的订阅能力：把夸克分享或 RSS 里的新内容转存 / 下载到影视库的待整理目录，再复用已有的扫描、TMDb 匹配、整理、刮削和通知流程。入口在侧边栏「订阅管理」，放送日历在「追更日历」。

### 前置条件

- 先建好一个影视库；订阅必须选择要整理到的影视库。
- 夸克订阅需要在「设置 → 夸克网盘」填写夸克 Cookie，并确认「测试连接」通过。
- RSS 订阅通过 115 云添加下载，需要先在「设置 → 115」配置网页 Cookie 或开放平台 `refresh_token`（开放平台令牌会自动续期）。
- 想接收推送，到「通知渠道」添加 Bark、Telegram、Webhook 等渠道；订阅里可以单独选择渠道。

### 夸克订阅

1. 「订阅管理」→「夸克订阅」，填写订阅名称、选择影视库。
2. 粘贴夸克分享链接；有提取码就填提取码。
3. **分享内目录**（可选）：
   - 直接在夸克分享页点进目标文件夹，复制带 `#/list/share/<目录ID>` 的网址，粘贴到分享链接即可。粘贴后会自动识别目录，不需要再手动选。
   - 也可以点分享链接右侧的文件夹按钮打开分享文件，选中目录后分享链接会同步更新。
   - 留空表示整个分享；选择目录后只转存该目录（含子目录）里的文件。
4. **转存目录**：留空直接保存到影视库根目录；也可以「浏览网盘」选择库内子目录。库外目录会提示并禁止选择。
5. **过滤规则**：关键词 / 正则二选一。关键词可用逗号分隔，需要全部命中；正则匹配文件名，留空不启用。还可以限制每次处理数量、检查间隔或 cron、每日时间窗口和截止日期。
6. **自动整理 / V2 替换**：
   - 开启自动整理后，匹配成功的文件会直接重命名、刮削并移动到整理后目录；关闭时保留在待整理目录等待人工处理。
   - V2 替换需要自动整理开启：同一 TMDb + 季 + 集出现更高版本时，用新版本替换旧版本，旧文件移入回收目录，不会生成 `(1)`。
7. **通知渠道**：不选表示使用所有已启用渠道；选择后只向所选渠道推送。
8. 保存并启用的订阅会立即排队检查一次，之后按执行计划运行。运行记录里可以看每个条目的状态、错误原因，也可以随时「立即运行」。

名词说明：

- **转存**：本次从分享里新转存到暂存目录的文件数量。
- **复用**：暂存目录里已经有同名文件，跳过重复转存，直接复用已有文件继续整理。
- **重复**：同一集已有其他版本或已经整理过，本次不再整理。
- **失败**：转存失败或整理失败；可以看运行记录里的具体错误，修复后重新运行。

### 订阅任务参数

订阅卡片上的「参数」按钮可以提取影序自己的完整任务参数（订阅名称、类型、完整配置、执行计划、截止日期、自动整理 / V2 替换、通知渠道和提取码）。可以复制保存，也可以直接「用此参数新建」一个同配置订阅。

「粘贴任务参数」入口可以粘贴影序任务参数，快速创建同类任务；识别后仍会进入手动配置，由你补全影视库、转存目录和执行计划后再保存。任务参数包含提取码，请妥善保存。

### RSS 订阅

「订阅管理」→「RSS 订阅」，填写 RSS / Atom 地址、影视库、关键词或正则、每次下载上限、暂存目录和执行计划。转存目录留空同样表示影视库根目录。支持蜜柑计划和任意标准 RSS / Atom 源。

### 追更与放送日历

「追更日历」提供蜜柑计划每周放送表（默认）和可选的 Bangumi 放送表。点选番剧会读取 Bangumi 条目、字幕组和蜜柑 RSS，可以先预览 RSS 内容；添加追更会同时创建一个 RSS 订阅，并按设定的间隔自动检查、下载和整理。支持追更中 / 已暂停 / 已完结状态；删除追更会一并删除对应的 RSS 订阅（正在运行时会拒绝删除，已下载 / 已整理文件不受影响）。

### 通知

- 23 种推送渠道：Webhook、Bark、Telegram、企业微信、飞书、钉钉等。
- **每次订阅运行只合并成一条推送**，不会因为 3 个未识别就发 3 条。
- 标题只用任务名；正文第一行是「转存成功 / 失败 / 整理 / 未识别 / 待确认」计数，之后按分类列出文件名和处理建议，最后是时间。
- 定时检查没有新内容时不推送；整次运行失败会单独发一条失败通知。
- 在「通知渠道」里勾选事件可以筛选；不勾选表示接收全部。发送日志可查。

### 注意事项

- 转存 / 暂存目录留空 = 影视库根目录，不会自动创建 `_订阅/<订阅名>`。
- OpenList 挂载名不需要硬编码：识别目录时会自动去掉影视库根路径的第一段挂载名；如果修改了挂载名，把影视库的「待整理目录 / 整理后目录」同步改掉即可。
- OpenList + 夸克 WebDAV 移动偶尔会返回 HTTP 500（OpenList 侧目录缓存 / 驱动瞬时问题）。文件通常仍在待整理目录，直接「立即运行」重试即可；多次失败时查看 OpenList 日志。
- 分享链接目录 ID 反查最多 8 层 / 约 2 万条；极深或超大分享可能定位失败，此时用分享链接右侧的文件夹按钮手动选择。
- 手动匹配会记住标题对应的 TMDb 作品，后续同一部剧的新集即使文件名是罗马音、没有季号，也会自动识别并继续整理；订阅开启自动整理时会直接整理。

---

## 配置说明

### TMDb

在「设置 → TMDb」填写 v3 API Key 或 v4 Read Access Token。海报等图片由后端代理并缓存，客户端不直连 TMDb 图床。

TMDb API 默认使用 `https://api.themoviedb.org/3`。如果服务器所在网络无法直连该域名，可改用备用入口：

```dotenv
CINEMINT_TMDB_API_BASE=https://api.tmdb.org/3
```

`api.tmdb.org` 与 `api.themoviedb.org` 提供相同的 TMDb API 响应；如果图片域名也不通，可另外设置 `CINEMINT_TMDB_IMAGE_BASE`。

### 自动扫描待整理目录（可选）

在「设置 → 识别与扫描」打开「自动扫描待整理目录」并设置检查间隔（5–1440 分钟，默认 15）。后台到期后做一次轻量探测：WebDAV / OpenList 先刷新库根缓存，最多向下 3 层列出视频文件，把「路径 + 体积」算成指纹与上次比较。

- 指纹没变：只更新检查时间，不扫描、不请求 TMDb；
- 指纹变化或首次检查：排队一次普通扫描任务，任务中心显示「自动」；
- 同库已有排队 / 运行中的任务，或订阅正在写同一目录：本轮跳过；
- 自动扫描只负责发现和识别，**不会自动整理**，也不推送通知；演示库不参与。

已知限制：轮询有延迟（最慢约「间隔 + 30 秒」）；探测固定 3 层；正在下载、体积还在变的文件可能连续触发两次；OpenList 只刷新库根一层缓存。

### AI 解析兜底（可选）

支持任意 OpenAI 兼容接口（OpenAI、DeepSeek、Ollama、SiliconFlow 等）。触发条件包括文件名标题为空 / 低置信、TMDb 无候选、分数过低或多个候选接近：

- 只发送文件名，不发送文件内容；
- 结果仍会与 TMDb 交叉验证；
- 可配置三档价格、峰谷时段、中国法定节假日；
- 可优先使用平台返回的 `usage.cost`，或从 OpenRouter / LiteLLM 同步模型价格；
- 费用为估算值，实际账单请以服务商为准。

### 文件源

- **WebDAV**：地址通常需要以 `/dav` 结尾；OpenList / Alist 可先在浏览器确认。
- **SMB**：填写主机、共享名、用户名、密码和子目录。
- 所有整理操作限制在当前影视库的待整理根目录内。

### 夸克网盘 / 115

- **夸克网盘**：在「设置 → 夸克网盘」填写 Cookie；保存后点「测试连接」，能读到昵称和目录才算成功。
- **115**：在「设置 → 115」选择网页 Cookie 或开放平台 `refresh_token`。开放平台令牌由后台自动续期并轮换保存，不需要频繁更换 Cookie。
- 订阅通知需要先在「通知渠道」添加渠道；渠道的密钥 / Webhook 地址使用 Fernet 加密保存。

---

## 安全与隐私

- 单管理员密码；密码使用 scrypt 哈希。
- 会话 Cookie 使用 HMAC 签名；可选 HTTPS Secure Cookie。
- WebDAV / SMB 密码、TMDb Key、AI Key 使用 Fernet 加密后存入 SQLite。
- 数据目录包含数据库、密钥和演示文件，默认不纳入 Git。
- 不上传媒体文件；AI 识别只发送文件名和上级目录路径，结果仍与 TMDb 交叉验证。

---

## 已知限制

- 不提供片源、下载、播放或 DRM 绕过。
- 手动修改的整理后名称只影响文件名，不会改作品文件夹 / 季文件夹；仅对未整理条目生效，已整理条目需要重新整理才能应用。
- 「所有文件」重命名不支持覆盖同名目标；删除没有回收站，无法撤销；递归规则会跳过隐藏目录、回收站和符号链接目录。
- WebDAV 大量移动可能较慢；服务器只完成复制时会报告失败并保留源文件。
- 附件只自动扫描同目录和下一层常见字幕子目录（`Subs` / `Sub` / `Subtitles` / `字幕` 等）。
- 海报墙真实性校验缓存约 20 秒；外部客户端直接改名 / 删除最多延迟缓存过期。
- 自动扫描是定时轮询：发现新文件最慢约「间隔 + 30 秒」，只探测 3 层，正在下载的文件可能连续触发两次；它只识别，不自动整理。
- AI 费用统计依赖服务商返回的 usage / cost；历史调用无法回溯。
- 中国法定节假日自动同步依赖外网数据源，失败时保留旧日期。
- 订阅任务参数包含提取码；分享链接目录 ID 反查最多 8 层 / 约 2 万条，极深或超大分享可能定位失败，可改用文件夹按钮手动选择。
- OpenList + 夸克 WebDAV 移动偶尔会返回 HTTP 500（目录缓存 / 驱动瞬时问题）；文件通常仍在待整理目录，点「立即运行」重试即可。

---

## 免责声明

- 本项目只做文件重命名、移动和元数据整理，不提供、不下载、不播放任何影视内容。
- 请只整理你拥有合法访问权的媒体文件，并遵守所在地区的版权法规。
- 本项目使用 TMDb API，但未经 TMDb 认可或认证。
- 产品灵感来自 Filmix，但为完全独立实现，不包含对方私有代码或接口。

## License

[MIT](LICENSE)
