# Double Stack Store (双栈商店)

一个基于 Docker 的容器管理平台，支持一键部署 Compose 文件、容器管理、备份恢复、仓库管理等实用功能。

## 功能特性

- **📦 容器管理** - 实时查看容器状态，支持启动/停止/重启/删除容器，查看容器日志；配置全局域名/IP后，容器名称可点击跳转至访问地址
- **🚀 一键部署** - 通过 Docker Compose 快速部署应用，支持实时日志输出、时间戳与镜像拉取进度条；慢网环境下使用空闲超时避免部署中断；同时兼容 Docker Compose v1 与 v2
- **📚 仓库管理** - 支持 Compose 与 Scripts 两种 Git 仓库：Compose 扫描 YML 用于部署，Scripts 扫描 `.sh` 并持久化保存
- **💾 备份恢复** - 容器完整备份（镜像+配置+数据卷），支持一键恢复和文件上传恢复；备份文件时间戳使用 UTC+8 时区
- **🖼️ 镜像管理** - 查看、拉取、删除本地镜像，支持导入 Docker `.tar` 镜像包和导出单个镜像，检测 Docker Hub 最新版本
- **📊 仪表盘** - 实时统计容器数量、备份状态等关键数据；展示宿主机系统信息（CPU、内存、磁盘、系统版本、网络）；展示 Docker/Docker Compose 版本信息；测试 GitHub 和 Docker Hub 连接性
- **🔐 用户管理** - 支持多用户注册登录、bcrypt 密码存储、可撤销会话与登录失败限流
- **📝 操作日志** - 完整记录系统操作日志，支持按类型筛选查看
- **⚙️ 系统设置** - 代理配置、全局域名/IP配置、Docker加速源等系统参数管理
- **💡 容器推荐** - 推荐热门容器应用，一键部署，配套教程链接
- **💻 终端访问** - 内置 Web 终端，支持连接宿主主机 Linux 终端

## 系统要求

- Docker 20.10+
- Docker Compose 2.0+
- 浏览器（Chrome、Firefox、Edge 等现代浏览器）

## 快速开始

### 方式一：Compose 部署（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/Double-Stack-Workshop/doublestack-shop.git
cd doublestack-shop

# 2. 启动服务（从 Docker Hub 拉取镜像）
docker compose up -d

# 3. 访问应用
# 打开浏览器访问：http://localhost:8000
```

### 方式二：Docker Run 部署

```bash
# 创建必要目录
mkdir -p ./backend/data ./backend/repos ./backend/scripts ./backend/backup ./backend/logs ./backend/image

# 启动容器
docker run -d \
  --name doublestack-shop \
  -p 8000:8001 \
  -v ./backend/data:/app/data \
  -v ./backend/repos:/app/repos \
  -v ./backend/scripts:/app/scripts \
  -v ./backend/backup:/app/backup \
  -v ./backend/logs:/app/logs \
  -v ./backend/image:/app/image \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /:/host:rw \
  -v /etc/docker:/etc/docker:rw \
  -v /etc/passwd:/etc/passwd:ro \
  -v /etc/group:/etc/group:ro \
  -e PYTHONUNBUFFERED=1 \
  --privileged \
  --restart unless-stopped \
  lastthree/doublestack-shop:v2.1.1

# 访问应用
# 打开浏览器访问：http://localhost:8000
```

### 卷映射说明

目录映射用于在宿主机持久化应用数据。即使不映射这些目录，应用也可以启动；但容器被删除或重建后，未映射目录中的数据会丢失。

| 宿主机映射 | 容器目录 | 用途 | 不映射的影响 |
| --- | --- | --- | --- |
| `./backend/data` | `/app/data` | 用户、会话、系统设置、数据库及仓库 Git 缓存 | 所有应用数据会随容器重建丢失 |
| `./backend/repos` | `/app/repos` | 已同步 Compose 仓库所选子目录的文件 | 部署文件会丢失 |
| `./backend/scripts` | `/app/scripts` | 更新与 Compose 升级脚本，以及 Scripts 仓库同步的 `.sh` 脚本 | 宿主机无法直接使用生成或同步的脚本 |
| `./backend/backup` | `/app/backup` | 容器备份文件 | 备份文件会丢失 |
| `./backend/logs` | `/app/logs` | 操作日志实体文件 | 日志文件会丢失 |
| `./backend/image` | `/app/image` | 导入及导出的 Docker `.tar` 镜像包 | 镜像包会丢失 |

以下是 Docker 管理和宿主机功能所需的特殊映射：

| 映射 | 用途 | 可否省略 |
| --- | --- | --- |
| `/var/run/docker.sock:/var/run/docker.sock` | 管理宿主机 Docker 容器、镜像与网络 | 不可省略；省略后 Docker 管理功能不可用 |
| `/:/host:rw`、`/etc/docker:/etc/docker:rw` | 获取宿主机信息、更新 Docker 配置及重启 Docker | 可省略，但宿主机操作和 Docker 加速源功能受限 |
| `/etc/passwd:/etc/passwd:ro`、`/etc/group:/etc/group:ro` | 识别宿主机用户与组权限 | 可省略，但终端与权限识别可能受限 |

### 默认账号

- 默认管理员账号：`admin`
- 默认管理员密码：日志中查看（修改密码和注册账号均需要提供请牢记！）

## 项目结构

```
doublestack-shop/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 运行配置（直接拉取发布镜像）
├── backend/
│   ├── app/
│   │   ├── main.py        # FastAPI 应用入口
│   │   ├── routes.py      # API 路由
│   │   ├── services.py    # 业务逻辑
│   │   ├── database.py    # 数据库操作
│   │   ├── version.py     # 版本信息
│   │   ├── logger.py      # 日志服务
│   │   ├── schemas.py     # 数据模型
│   │   └── terminal.py    # 终端服务
│   ├── data/              # 数据配置文件
│   └── requirements.txt   # Python 依赖
└── frontend/
    └── src/
        ├── images/        # 图片资源
        ├── components/    # 公共组件
        │   ├── sidebar/   # 侧边栏组件
        │   └── common/    # 页面认证、请求与用户信息公共逻辑
        └── pages/         # 页面组件
            ├── login/     # 登录页面
            ├── register/  # 注册页面
            ├── forgot/    # 找回密码
            ├── dashboard/ # 仪表盘
            ├── container/ # 容器管理
            ├── image/     # 镜像管理
            ├── deploy/    # 应用部署
            ├── backup/    # 备份恢复
            ├── logs/      # 操作日志
            ├── terminal/  # 终端管理
            ├── settings/  # 系统设置
            ├── recommend/ # 推荐应用
            └── repository/# 仓库管理
```

## 技术栈

- **后端**: FastAPI + SQLite + Docker SDK
- **前端**: 原生 HTML/CSS/JavaScript
- **容器**: Docker + Docker Compose

## 版本信息

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| v2.1.1 | 2026-08-29 | 新增局域网 Apprise API 通知：支持 `/notify` 与完整通知端点、测试通知，并可选择部署、仓库、镜像、备份和 Docker 重启通知；仓库与推荐容器仅以 JSON 为唯一初始化来源，移除旧数据库及初始化入口；修复仓库列表上次同步时间固定显示“刚刚”的问题，并统一认证页面 API 配置。 |
| v2.1.0 | 2026-08-28 | 仓库管理支持 Compose 与 Scripts 两种仓库类型；Compose 仓库仅将所选 `local_path` 导出到 `repos` 映射目录，Scripts 仓库仅将选中的 `.sh` 脚本导出到 `scripts` 映射目录，Git 缓存保存在 `data` 中；Scripts 不会出现在容器部署来源中。 |
| v2.0.9 | 2026-08-27 | 检查更新后自动反推 `scripts` 挂载对应的宿主机绝对路径；提示用户先执行 `sudo -i`，再提供唯一的一行更新命令；修复更新脚本生成接口未返回脚本路径的问题；镜像管理新增 Docker `.tar` 包导入与镜像导出。 |
| v2.0.8 | 2026-08-27 | 完成会话鉴权、bcrypt 密码迁移、管理员 API 与 WebSocket 权限校验；统一数据库事务与前端公共认证/请求逻辑；部署页改用 YAML 解析器并拆分部署流和网络模块；Docker 加速源增加重启恢复检测与回滚；新增 Ruff、ESLint、CI 和回归测试；新增前端缓存清理；修复登录 422、管理员添加用户、仪表盘快捷跳转、部署页仓库筛选/加载、Compose 文件宿主机映射路径解析及普通用户仪表盘权限显示问题。 |
| v2.0.7 | 2026-08-22 | Docker 加速源保存后支持自动重启宿主机 Docker 服务；修复部署页高级模式无法保存和部署的问题；修复卷挂载路径包含连字符时显示被截断的问题； |
| v2.0.6 | 2026-08-20 | 仪表盘逻辑及UI优化；Docker Compose 自动生成脚本，并提供运行指令；容器部署页面新增「网络管理」；Compose 部署新增 `name: doublestack-shop` 统一 Compose 项目名；容器日志获取修复并支持倒序显示；|
| v2.0.5 | 2026-08-20 | 仓库策略更新；仓库管理新增「未同步」状态与筛选；修复仓库UI问题；设置页面新增 GitHub 项目地址；|
| v2.0.4 | 2026-08-19 | 容器部署日志改为实时推送，新增时间戳与进度条；部署超时改为空闲超时，解决慢网环境下误判失败；|
| v2.0.3 | 2026-07-06 | 优化代理配置逻辑；优化默认仓库初始化，服务启动不再阻塞；优化仓库拉取可靠性；优化UI界面； |
| v2.0.2 | 2026-06-28 | 修复备份文件时间戳时区问题（统一使用UTC+8）；新增默认仓库配置（飞牛容器仓库、绿联新系统容器仓库、绿联旧系统容器仓库、极空间容器仓库）；修复多仓库文件列表重复问题；新增全局域名/IP配置功能（设置界面配置，容器名称可点击跳转）；新增仪表盘宿主机系统信息展示（CPU使用率、内存使用、磁盘空间、系统版本、网络信息）；新增仪表盘 Docker 环境信息展示（Docker/Docker Compose 版本）；修复多个界面样式和API错误问题； |
| v2.0.1 | 2026-06-28 | 新增 Docker 容器备份功能：修复容器备份恢复功能：修复数据卷打包路径映射问题；修复恢复时数据卷写入失败问题；备份前自动停止容器确保数据一致性；恢复前清空旧数据确保纯净恢复； |
| v2.0.0 | 2026-06-27 | 新增 Docker 加速源管理功能（支持增删改查、拖拽排序）；统一操作日志类别中文显示；优化设置页面功能； |

<details>
<summary>v1.0 版本历史更新内容</summary>

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| v1.0.19 | 2026-06-07 | 修复数据库问题；修复项目时区问题；新增更多日志记录 |
| v1.0.18 | 2026-06-07 | 新增操作日志详情功能：部署成功日志支持查看详情，展示完整的镜像拉取、容器启动等详细日志；优化部署日志记录，修复日志类型错误；修复日志显示顺序问题 |
| v1.0.17 | 2026-06-07 | 修复UI一致性问题：优化区域布局；修复容器详情运行时间、端口映射和创建时间显示问题；修复运行中容器无法打开详情弹窗的问题 |
| v1.0.16 | 2026-05-12 | 新增容器推荐页面：展示常用容器模板，支持一键跳转到部署页面和查看使用教程 |
| v1.0.15 | 2026-05-12 | 修复 f-string 中 awk $1 $2 变量转义问题，解决脚本语法错误 |
| v1.0.14 | 2026-05-12 | 修复更新脚本端口解析逻辑，使用 awk/sed 替代 cut 避免分隔符错误；修复旧镜像清理使用正确的 IMAGE_NAME 变量 |
| v1.0.13 | 2026-05-12 | 新增容器代理配置功能：支持配置 HTTP/HTTPS 代理，部署容器时自动应用代理环境变量 |
| v1.0.12 | 2026-05-12 | 新增仪表盘连接性模块：测试 GitHub 和 Docker Hub 的网络连通性，显示连接状态和延迟时间 |
| v1.0.11 | 2026-05-11 | 新增镜像管理功能：支持删除本地镜像、支持从DockerHub搜索并拉取镜像、支持通过镜像名直接拉取；优化侧边栏菜单顺序 |
| v1.0.10 | 2026-05-11 | 新增自动更新脚本功能：检测到新版本时自动生成更新脚本；更新脚本支持通过Docker Hub拉取最新镜像；自动获取当前容器配置（网络、端口、挂载目录）；支持删除旧容器和清理旧镜像 |
| v1.0.9 | 2026-05-11 | 新增终端功能：支持连接宿主主机Linux终端；修复密码生成规则；统一所有页面侧边栏组件；修复终端页面布局一致性问题；修复终端横向滚动问题 |
| v1.0.8 | 2026-05-11 | 修复注册页面API路径问题，统一登录/注册/忘记密码页面样式 |
| v1.0.7 | 2026-05-09 | 问题修复及UI优化 |
| v1.0.0 | 2026-05-09 | 初始版本 |

</details>

## 许可证

MIT License

## 联系方式

- GitHub: https://github.com/Double-Stack-Workshop/doublestack-shop
- Docker Hub: https://hub.docker.com/r/lastthree/doublestack-shop
