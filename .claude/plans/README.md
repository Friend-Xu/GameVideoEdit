# GameVideoEdit 开发计划索引

## 计划文件清单

| 文件 | 内容 | 读者 |
|------|------|------|
| [00-architecture.md](00-architecture.md) | 模块化架构设计、目录结构、接口定义 | 所有Agent |
| [02-agent-tasks.md](02-agent-tasks.md) | 多Agent任务分解、依赖图、验收标准 | Claude Agent |
| [03-environment.md](03-environment.md) | 环境隔离、模型管理、依赖管理 | Agent-0B, 0C, 4C |
| [04-file-management.md](04-file-management.md) | 文件命名、导入规范、异常处理、配置管理 | 所有Agent |

## 配套配置文件

| 文件 | 路径 | 状态 |
|------|------|------|
| model_registry.json | `models/model_registry.json` | 已创建 |
| keywords.yaml | `config/keywords.yaml` | 已创建 |
| default.yaml | `config/default.yaml` | 待Agent-0B创建 |

## 当前状态

- 计划日期: 2026-05-23
- Phase 0: 待启动
- Phase 1-4: 待Phase 0完成后启动

## 快速导航

- Claude Agent: 先读 `02-agent-tasks.md` → 找到自己任务 → 读 `00-architecture.md` → 读 `04-file-management.md`
- 开发者: 读 `00-architecture.md` → `scripts/setup_venv.bat` → `python app/main.py`
