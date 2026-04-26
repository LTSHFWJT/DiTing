# DiTing Sandbox

DiTing Sandbox 是一个面向 Windows/Linux 程序动态分析的沙箱项目，当前按
`selfdoc/windows-linux-sandbox-technical-design.md` 落地第一版控制面和 Agent 骨架。

核心安全边界：

- 用户提交文件、解包后的子文件、脚本、安装包、文档宏、自解压程序只能在 Windows/Linux 分析虚拟机中执行。
- 服务端、处理 Worker、Node Agent、宿主机 shell、容器和 CI 只能读取、传输、解包和静态解析样本字节，不能直接执行样本。
- 当前代码已经把该约束写入任务租约、Guest 执行计划和测试用例中。

## 当前已实现

- FastAPI 服务端。
- Jinja2 服务端页面：总览、提交样本、分析列表、分析详情、节点视图。
- SQLite 元数据数据库，启用 WAL 模式。
- 本地对象存储目录，用于保存样本、分析目录、任务 JSON、报告和工件。
- 文件提交、分析查询、任务查询、报告查询、工件上传/下载、取消、重跑接口。
- 节点注册、机器池上报、任务租约、租约过期回收、任务状态上报接口。
- ResultServer 最小替代接口：行为事件批量上报、任务状态消息上报、事件 JSONL 落盘和报告聚合。
- 只读静态识别：PE/ELF 基础元数据、脚本识别、zip/tar 子文件元数据枚举。
- 配置模板：服务端、节点、分析设置、路由、处理插件。
- Node Agent：支持 YAML/JSON 节点配置、注册节点、领取任务、导出 Guest Plan、`run-once/run-loop` 生命周期编排、VM machinery 抽象、Guest Agent 健康检查、样本/配置转运、事件和工件上报。
- Guest Agent API 骨架：只有在 `DITING_EXECUTION_CONTEXT=guest_vm` 时才允许接收样本。
- 基础测试覆盖 VM-only 策略、提交链路、任务租约、工件回传、Node Agent 编排和 Guest Agent 拒绝宿主机执行。

## 环境准备

建议使用项目虚拟环境或独立 Python 3.11+ 环境：

```powershell
python -m pip install -e ".[dev]"
```

当前必需依赖包括：

- `fastapi`
- `uvicorn`
- `python-multipart`
- `Jinja2`
- `PyYAML`
- `pytest`
- `httpx`

## 启动服务端

服务端支持直接通过模块方式启动：

```powershell
python -m diting_sandbox.server --host 127.0.0.1 --port 8000 --reload
```

默认运行数据写入 `.diting-data`。可以通过环境变量修改：

```powershell
$env:DITING_DATA_DIR="E:\sandbox-data"
python -m diting_sandbox.server --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

Web 页面：

- `http://127.0.0.1:8000/`：总览。
- `http://127.0.0.1:8000/submit`：提交样本。
- `http://127.0.0.1:8000/analyses`：分析列表。
- `http://127.0.0.1:8000/nodes`：节点和机器池。

## 提交样本

接口路径：

```text
POST /api/v1/analyses
```

表单字段：

- `file`：样本文件。
- `options`：可选 JSON 字符串，例如超时、网络路由、指定平台。

PowerShell 示例：

```powershell
$form = @{
  file = Get-Item ".\sample.exe"
  options = '{"timeout":90,"route":"drop"}'
}
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/analyses `
  -Method Post `
  -Form $form
```

## 取消和重跑

取消分析会把未完成任务置为 `cancelled`，并释放已租约的机器槽位：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/analyses/<analysis_id>/cancel `
  -Method Post
```

重跑分析会复用原样本、原识别结果和原提交参数，创建新的 analysis 和 task：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/analyses/<analysis_id>/rerun `
  -Method Post
```

## 注册节点

Node Agent 可用模块方式运行：

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 register `
  --node-id node-1 `
  --name node-1 `
  --machine win10-01:windows:10:amd64:192.168.30.11 `
  --machine ubuntu-01:linux:ubuntu22.04:amd64:192.168.30.21
```

当前 `--machine` 表示已经准备好的分析 VM 槽位，格式为：

```text
NAME:PLATFORM:OS_VERSION:ARCH[:IP]
```

也可以使用配置文件注册，配置模板在 `configs/templates/node.yaml`：

```powershell
python -m diting_sandbox.node_agent --config configs/templates/node.yaml register
```

配置里的 `machinery.backend` 支持以下值：

- `noop`：开发自检后端，只记录生命周期事件，不控制真实 VM。
- `libvirt`、`kvm`、`qemu`：通过 `virsh` 做快照恢复、启动、关闭、截图和内存转储基础命令。
- `virtualbox`：通过 `VBoxManage` 做快照恢复、启动、关闭和截图基础命令。
- `hyperv`、`hyper-v`：通过 PowerShell Hyper-V cmdlet 做快照恢复、启动和关闭基础命令。

这些后端只控制虚拟化生命周期，不会在宿主机执行提交样本。真实环境使用前需要按宿主机、VM 名称、快照名、网络接口和权限做验证。

## 领取任务

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 lease `
  --node-id node-1 `
  --plan-dir .diting-node-plans
```

领取成功后服务端会返回：

- `task`：任务元数据。
- `lease_token`：任务租约令牌。
- `guest_plan`：只能交给 Guest VM 执行的计划。
- `forbidden_execution_contexts`：明确禁止执行样本的上下文。

Node Agent 只能把样本传入 VM，不能在宿主机执行样本。

## 运行 Node Agent 生命周期

`run-once` 会领取一个任务，并按技术方案中的 Node Agent 流程执行第一版编排：

1. 校验租约里的 VM-only 策略。
2. 将 Guest Plan 写入本地计划目录。
3. 调用 machinery 后端恢复快照并启动 VM。
4. 检查 Guest Agent `/health`，要求其报告 `is_guest_vm=true`。
5. 将任务配置和样本文件上传到 Guest VM 的 `/store`。
6. 调用 Guest VM 内的 `/execute`。
7. 收尾停止抓包、关闭 VM、上报生命周期事件和最终状态。

示例：

```powershell
python -m diting_sandbox.node_agent --config configs/templates/node.yaml run-once --register
```

持续拉取任务：

```powershell
python -m diting_sandbox.node_agent --config configs/templates/node.yaml run-loop --interval 5
```

当前 Guest Agent 的 `/execute` 仍是占位接口，会返回 501；因此真实动态执行还需要后续补 Windows/Linux Analyzer 和 Guest Agent 执行器。Node Agent 已经能把这类失败归档为任务失败，并写入 ResultServer 风格状态消息。

## 上报状态

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 status `
  --task-id 1 `
  --status running `
  --lease-token "<lease_token>"
```

终态包括：

- `finished`
- `failed`
- `cancelled`

任务进入终态后会释放机器槽位，并刷新分析报告。

## 上传行为事件

节点或 VM 内结果收集器可以把行为事件上传到服务端。事件会追加写入任务目录下的 `events.jsonl`，并在 `report.json` 中聚合为 `behavior.events_count`、`behavior.process_tree` 和 `network.connections`。

JSONL 示例：

```jsonl
{"event":"process.create","pid":100,"ppid":50,"image":"C:\\sample.exe","command_line":"C:\\sample.exe"}
{"event":"network.connect","pid":100,"protocol":"tcp","dst_ip":"203.0.113.10","dst_port":443}
```

上传：

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 events `
  --task-id 1 `
  --lease-token "<lease_token>" `
  --source guest_agent `
  --file .\events.jsonl
```

上传普通工件：

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 artifact `
  --task-id 1 `
  --lease-token "<lease_token>" `
  --type pcap `
  --file .\task-1.pcap
```

也可以上传 ResultServer 风格的任务状态消息：

```powershell
python -m diting_sandbox.node_agent --server http://127.0.0.1:8000 result-status `
  --task-id 1 `
  --lease-token "<lease_token>" `
  --status complete `
  --message "guest finished"
```

状态映射规则：

- `heartbeat`：只记录状态消息，不改变任务状态。
- `complete`：映射为任务 `finished`。
- `exception` 或 `error`：映射为任务 `failed`。
- 其他已支持任务状态会直接写入任务状态机。

## 回收过期租约

服务端在节点领取任务前会自动回收已过期租约，也可以手动触发：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/tasks/leases/recover `
  -Method Post
```

回收后任务会重新进入 `queued`，原机器槽位会恢复为 `available`。

## 启动 Guest Agent

Guest Agent 必须在分析虚拟机中启动：

```powershell
$env:DITING_EXECUTION_CONTEXT="guest_vm"
python -m diting_sandbox.guest_agent --host 0.0.0.0 --port 8765
```

如果没有设置 `DITING_EXECUTION_CONTEXT=guest_vm`，Guest Agent 的 `/store` 和 `/execute` 会拒绝操作，避免在宿主机误收样本或误执行样本。

当前 `/execute` 仍是第一版占位接口，返回 501。后续需要接入 Windows/Linux 平台 Analyzer 后，才能真正让 VM 内执行样本并采集行为。

## 测试

```powershell
pytest -q -p no:cacheprovider
```

测试覆盖：

- Jinja2 页面渲染和 Web 表单提交。
- PE/ELF 只读识别。
- PE 基础元数据识别。
- zip 子文件元数据枚举，且不解压、不执行。
- 文件提交和任务创建。
- 节点注册和任务租约。
- 分析取消、重跑和过期租约回收。
- 样本下载必须携带租约令牌。
- 工件上传、下载和报告刷新。
- 行为事件上传、状态消息上传和报告行为摘要聚合。
- 宿主机执行样本被阻断。
- Guest Agent 在非 VM 上拒绝接收样本。

## 目录说明

```text
diting_sandbox/
  core/          配置、SQLite、存储、识别、安全策略
  server/        FastAPI 服务端和 python -m 启动入口
    templates/   Jinja2 页面模板
    static/      页面样式
  node_agent/    节点 Agent CLI、API Client、配置、machinery 抽象、Guest 通信和任务编排
  guest_agent/   VM 内 Guest Agent API 骨架
configs/
  templates/     服务端、节点、分析设置、路由、处理插件模板
tests/           当前第一版测试
selfdoc/         设计文档和实现 TODO
```

## 下一步重点

- 在真实宿主机上验证 Node Agent 的 libvirt/QEMU/Hyper-V/VirtualBox 后端。
- 实现 VM 一次性 overlay、任务后强制回滚和快照健康校验。
- 实现 ResultServer，接收 VM 内日志、截图、PCAP、掉落文件和内存转储。
- 将当前事件上报接口扩展为独立 ResultServer，支持断点续传、限流和来源 VM 校验。
- 实现 Windows Analyzer 和 Linux Analyzer。
- 增加 PE/ELF 深度静态解析、YARA、capa 和安全解包。
- 加入认证、审计、任务重试、节点离线恢复和 WebSocket 事件。
