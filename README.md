# Cici 的游戏乐园

本项目是一个基于 Flask 的小游戏聚合平台，支持管理员上传 PyScript 游戏、玩家注册登录、评分排行以及游玩历史记录功能。

## 环境准备

若在本地或任何新环境中运行站点，建议先创建虚拟环境并安装依赖：

```bash
python -m venv .venv
source .venv/bin/activate  # Windows 请执行 .venv\\Scripts\\activate
pip install -r requirements.txt
```

若运行环境缺少 Flask 等依赖导致站点无法启动或截图失败，请先执行上述安装命令，或确保部署容器中已经安装 `requirements.txt` 中列出的所有包。

## 初始化数据库

首次启动前执行数据库初始化脚本：

```bash
flask --app app.py shell <<'PY'
from app import bootstrap
bootstrap()
PY
```

脚本会创建数据库、默认管理员账号以及内置的三款示例游戏（贪吃蛇、愤怒的小鸟、俄罗斯方块）。

## 运行开发服务器

```bash
flask --app app.py run --debug
```

访问 `http://127.0.0.1:5000/` 即可预览网站。

## 管理员默认账号

- 用户名：`admin`
- 密码：`admin123`
- 手机号：`13800000000`

使用该账号登录后即可访问后台发布新游戏。

## 目录结构

- `app.py`：应用主程序。
- `templates/`：Jinja2 模板。
- `static/`：静态资源。
- `requirements.txt`：运行所需的 Python 包列表。

## 常见问题

- **提示缺少 Flask 或相关依赖**：确认已执行 `pip install -r requirements.txt`。
- **数据库结构发生变更**：删除 `app.db` 重新执行“初始化数据库”步骤。

