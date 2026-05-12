# Double Stack Store (双栈商店)

一个基于 Docker 的容器管理平台，支持一键部署 Compose 文件、容器管理、仓库管理等实用功能。

## 功能特性

- **📦 容器管理** - 实时查看运行中/已停止/总容器数量，支持查看日志、删除容器
- **🚀 一键部署** - 通过 Docker Compose 快速部署应用，支持实时日志输出
- **📚 仓库管理** - 添加和管理多个 Git 仓库，一键拉取最新代码
- **📊 仪表盘** - 实时统计已部署应用数量、部署成功率等关键数据
- **🔐 用户管理** - 支持多用户注册登录，管理员密码保护
- **🔄 版本检测** - 自动检测 Docker Hub 最新版本，支持一键更新
- **💻 终端访问** - 内置 Web 终端，支持连接宿主主机 Linux 终端

## 系统要求

- Docker 20.10+
- Docker Compose 2.0+
- 浏览器（Chrome、Firefox、Edge 等现代浏览器）

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Double-Stack-Workshop/doublestack-shop.git
cd doublestack-shop
```

### 2. 启动服务

```bash
docker compose up -d
```

### 3. 访问应用

打开浏览器访问：`http://localhost:8000`

默认管理员账号：`admin`

默认管理员密码：日志中查看（修改密码和注册账号均需要提供请牢记！）

## 构建 Docker 镜像

```bash
# 构建镜像
docker build -t doublestack-shop .
```

## 项目结构

```
doublestack-shop/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml     # Docker Compose 配置
├── backend/
│   ├── app/
│   │   ├── main.py       # FastAPI 应用入口
│   │   ├── routes.py     # API 路由
│   │   ├── services.py   # 业务逻辑
│   │   ├── database.py   # 数据库操作
│   │   └── version.py    # 版本信息
│   └── requirements.txt  # Python 依赖
└── frontend/
    └── src/
        ├── images/       # 图片资源
        ├── pages/        # 页面组件
        └── components/   # 公共组件
```

## 技术栈

- **后端**: FastAPI + SQLite + Docker SDK
- **前端**: 原生 HTML/CSS/JavaScript
- **容器**: Docker + Docker Compose

## 版本信息

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| v1.0.0 | 2026-05-09 | 初始版本 |
| v1.0.7 | 2026-05-09 | 问题修复及UI优化 |
| v1.0.8 | 2026-05-11 | 修复注册页面API路径问题，统一登录/注册/忘记密码页面样式 |
| v1.0.9 | 2026-05-11 | 新增终端功能：支持连接宿主主机Linux终端；修复密码生成规则；统一所有页面侧边栏组件；修复终端页面布局一致性问题；修复终端横向滚动问题 |
| v1.0.10 | 2026-05-11 | 新增自动更新脚本功能：检测到新版本时自动生成更新脚本；更新脚本支持通过Docker Hub拉取最新镜像；自动获取当前容器配置（网络、端口、挂载目录）；支持删除旧容器和清理旧镜像 |
| v1.0.11 | 2026-05-11 | 新增镜像管理功能：支持删除本地镜像、支持从DockerHub搜索并拉取镜像、支持通过镜像名直接拉取；优化侧边栏菜单顺序 |

## 许可证

MIT License

## 联系方式

- GitHub: https://github.com/Double-Stack-Workshop/doublestack-shop
- Docker Hub: https://hub.docker.com/r/lastthree/doublestack-shop