# Double Stack Store (双栈商店)

一个基于 Docker 的容器管理平台，支持一键部署 Compose 文件、容器管理、备份恢复、仓库管理等实用功能。

## 功能特性

- **📦 容器管理** - 实时查看容器状态，支持启动/停止/重启/删除容器，查看容器日志；配置全局域名/IP后，容器名称可点击跳转至访问地址
- **🚀 一键部署** - 通过 Docker Compose 快速部署应用，支持实时日志输出、时间戳与镜像拉取进度条；慢网环境下使用空闲超时避免部署中断；同时兼容 Docker Compose v1 与 v2
- **📚 仓库管理** - 添加和管理多个 Git 仓库，支持飞牛、绿联、极空间等多平台容器仓库，按 local_path 筛选文件
- **💾 备份恢复** - 容器完整备份（镜像+配置+数据卷），支持一键恢复和文件上传恢复；备份文件时间戳使用 UTC+8 时区
- **🖼️ 镜像管理** - 查看本地镜像，支持删除镜像，检测 Docker Hub 最新版本，支持一键拉取更新
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

### 方式一：克隆构建部署（开发/自定义）

```bash
# 1. 克隆项目
git clone https://github.com/Double-Stack-Workshop/doublestack-shop.git
cd doublestack-shop

# 2. 构建并启动（本地构建镜像）
docker compose up -d

# 3. 访问应用
# 打开浏览器访问：http://localhost:8000
```

### 方式二：Compose 部署（推荐）

```bash
# 1. 创建 docker-compose.run.yml 文件
curl -O https://raw.githubusercontent.com/Double-Stack-Workshop/doublestack-shop/main/docker-compose.run.yml

# 2. 启动服务（从 Docker Hub 拉取镜像）
# 注意：请先编辑 docker-compose.run.yml，将 {version} 替换为实际版本号
docker compose -f docker-compose.run.yml up -d

# 3. 访问应用
# 打开浏览器访问：http://localhost:8000
```

### 方式三：Docker Run 部署

```bash
# 创建必要目录
mkdir -p ./backend/data ./backend/repos ./backend/scripts ./backend/backup

# 启动容器
docker run -d \
  --name doublestack-shop \
  -p 8000:8001 \
  -v ./backend/data:/app/data \
  -v ./backend/repos:/app/repos \
  -v ./backend/scripts:/app/scripts \
  -v ./backend/backup:/app/backup \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /:/host:rw \
  -v /etc/docker:/etc/docker:rw \
  -v /etc/passwd:/etc/passwd:ro \
  -v /etc/group:/etc/group:ro \
  -e PYTHONUNBUFFERED=1 \
  --privileged \
  --restart unless-stopped \
  lastthree/doublestack-shop:{version} # 请将 {version} 替换为实际版本号，如 v2.0.8

# 访问应用
# 打开浏览器访问：http://localhost:8000
```

### 默认账号

- 默认管理员账号：`admin`
- 默认管理员密码：日志中查看（修改密码和注册账号均需要提供请牢记！）

## 项目结构

```
doublestack-shop/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 配置
├── docker-compose.run.yml  # Docker Run 部署配置
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

## 安全与运维说明

- 所有管理 API 和 WebSocket 终端均要求登录；容器、部署、备份、设置等高风险操作仅限管理员。
- 密码以 bcrypt 哈希保存。旧版密码会在成功登录后自动迁移；会话 Cookie 为 HttpOnly，服务端可撤销。
- 如需从不同域名访问 API，请通过 `CORS_ORIGINS` 以逗号配置允许的准确来源；默认仅允许本机开发地址。
- 部署命令会优先使用 `docker compose`，并在仅安装 v1 的环境回退到 `docker-compose`。
- 保存 Docker 加速源会先原子写入配置并重启宿主机 Docker。页面会轮询恢复状态；若 Docker 未在限定时间内恢复，会回滚旧配置并安排恢复重启。该功能要求容器具备 Docker socket、宿主机命名空间与特权运行权限。

## 版本信息

| 版本 | 日期 | 更新内容 |
|------|------|----------|
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
