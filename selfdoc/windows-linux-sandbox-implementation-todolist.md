# Windows/Linux 沙箱项目实现 TODO

来源设计文档：`selfdoc/windows-linux-sandbox-technical-design.md`\
当前状态日期：2026-04-26\
状态标记：

- `[x]` 已完成
- `[~]` 部分完成
- `[ ]` 待完成

当前实际边界：

- 已完成服务端控制面、Jinja2 基础 Web 界面、SQLite 元数据、本地对象存储、任务租约、取消、重跑、过期租约回收、ResultServer 最小替代接口、基础报告聚合、Node Agent 第一版生命周期编排和 Guest Agent 骨架。
- 已完成 PE/ELF 基础头部识别、zip/tar 归档元数据枚举，但尚未接入 libmagic、pefile/LIEF、pyelftools/LIEF、YARA、capa。
- 已实现 Node Agent 的配置加载、API Client、machinery 抽象、Guest Agent 健康检查、任务配置/样本转运、tcpdump 抓包管理骨架和 run-once/run-loop 编排；真实宿主机 VM 后端、overlay、网络隔离和 Guest VM 内样本执行尚待验证与补齐。
- 尚未实现 Guest VM 内样本执行、Windows/Linux Analyzer、网络隔离 Rooter、独立 ResultServer、签名引擎、完整 Web 报告和生产部署。

## 1. 第一版工程基础

- [x] 建立 Python 项目结构：`diting_sandbox/core`、`server`、`node_agent`、`guest_agent`。
- [x] 增加 `pyproject.toml`，声明 FastAPI、Uvicorn、python-multipart、Jinja2、PyYAML、pytest、httpx 等依赖。
- [x] 服务端支持 `python -m diting_sandbox.server` 启动。
- [x] Node Agent 支持 `python -m diting_sandbox.node_agent` 启动。
- [x] Guest Agent 支持 `python -m diting_sandbox.guest_agent` 启动。
- [x] README 改为中文，并说明启动、提交、注册节点、领取任务、Guest Agent 使用方式。
- [x] 增加 Jinja2 Web 页面目录：`diting_sandbox/server/templates` 和 `diting_sandbox/server/static`。
- [x] 增加自动化测试：覆盖 API 主链路、取消/重跑、租约回收、事件上报、Node Agent 编排、Guest Agent 边界、PE/ELF/zip 识别和 Web 页面提交。
- [ ] 增加 CI 流程，但 CI 只能执行测试和静态检查，不能执行样本。
- [x] 增加配置文件模板目录：`configs/templates`、`analysissettings.yaml`、`routing.yaml`、`processing.yaml`。
- [ ] 增加生产部署目录：`deploy/systemd`、`deploy/docker-compose.yml`、`deploy/ansible`。

## 2. 服务端 API

- [x] `GET /api/v1/health` 健康检查。
- [x] `POST /api/v1/analyses` 文件提交。
- [x] `GET /api/v1/analyses/{id}` 查询分析概要。
- [x] `GET /api/v1/analyses/{id}/tasks` 查询分析任务。
- [x] `GET /api/v1/analyses/{id}/report` 查询 JSON 报告。
- [x] `GET /api/v1/analyses/{id}/artifacts` 查询工件列表。
- [x] `GET /api/v1/artifacts/{artifact_id}` 下载工件。
- [x] `POST /api/v1/nodes/register` 注册分析节点和 VM 机器池。
- [x] `GET /api/v1/nodes` 查询节点。
- [x] `GET /api/v1/machines` 查询机器池。
- [x] `POST /api/v1/tasks/lease` 节点领取任务。
- [x] `POST /api/v1/tasks/leases/recover` 回收过期任务租约。
- [x] `GET /api/v1/tasks/{task_id}/sample` 节点按租约下载样本，用于传入 VM。
- [x] `POST /api/v1/tasks/{task_id}/artifacts` 节点按租约上传工件。
- [x] `POST /api/v1/tasks/{task_id}/events` 节点按租约上传行为事件。
- [x] `POST /api/v1/tasks/{task_id}/result-status` 节点按租约上传 ResultServer 风格状态消息。
- [x] `POST /api/v1/tasks/{task_id}/status` 节点上报任务状态。
- [x] `POST /api/v1/analyses/{id}/cancel` 取消分析。
- [x] `POST /api/v1/analyses/{id}/rerun` 重新分析。
- [x] `GET /` Web 总览页。
- [x] `GET /submit` Web 提交页。
- [x] `POST /submit` Web 表单提交。
- [x] `GET /analyses` Web 分析列表。
- [x] `GET /analyses/{id}` Web 分析详情。
- [x] `POST /analyses/{id}/cancel` Web 取消分析。
- [x] `POST /analyses/{id}/rerun` Web 重跑分析。
- [x] `GET /nodes` Web 节点和机器池页。
- [ ] `WS /api/v1/analyses/{id}/events` 实时状态和日志摘要。
- [ ] 用户/API Key 认证。
- [ ] 权限控制和下载审计。
- [ ] 生产环境 TLS 配置。

## 3. SQLite 元数据存储

- [x] 使用 SQLite 替代 PostgreSQL。
- [x] 启用 WAL 模式。
- [x] 启用 foreign key。
- [x] 设置 busy timeout。
- [x] 建立 `samples` 表。
- [x] 建立 `analyses` 表。
- [x] 建立 `tasks` 表。
- [x] 建立 `nodes` 表。
- [x] 建立 `machines` 表。
- [x] 建立 `artifacts` 表。
- [x] 建立 `reports` 表。
- [x] 样本按 sha256 去重入库。
- [x] 任务状态变化后刷新 analysis 状态。
- [x] 任务 lease 使用 SQLite `BEGIN IMMEDIATE` 事务保证单任务不被重复领取。
- [x] 实现 lease 过期回收。
- [ ] 实现节点离线检测。
- [ ] 实现任务重试次数和失败原因归档。
- [ ] 增加数据库迁移机制。
- [ ] 增加定期 VACUUM/归档策略。

## 4. 对象存储和分析目录

- [x] 本地对象存储目录。
- [x] 样本按 sha256 分层保存。
- [x] 分析目录保存 `analysis.json`。
- [x] 任务目录保存 `task.json`。
- [x] 工件写入任务目录。
- [x] 任务事件追加写入 `events.jsonl`。
- [x] 任务状态消息追加写入 `status.jsonl`。
- [x] 报告写入 `report.json`。
- [ ] 支持 MinIO/S3。
- [ ] 支持大文件分片上传。
- [~] 支持工件大小配额：当前复用全局 `max_file_size` 限制工件和任务 JSONL，尚未支持按任务/类型配额。
- [ ] 支持工件压缩。
- [ ] 支持冷热数据清理。
- [ ] 支持按权限下载样本和工件。

## 5. 静态识别与预处理

- [x] PE 文件头 `MZ` 基础识别为 Windows。
- [x] ELF 文件头基础识别为 Linux。
- [x] ELF 机器架构基础识别。
- [x] 脚本和常见扩展名基础平台推断。
- [x] ZIP/RAR/7z/TAR/GZ/JAR/PDF 等多平台扩展名基础识别。
- [x] 静态识别阶段只读字节，不执行样本。
- [ ] 接入 libmagic。
- [~] 接入 pefile 或 LIEF 解析 PE 元数据：当前先用标准库方式解析 PE 头部、架构、节数量和可选头魔数，尚未接入 pefile/LIEF。
- [~] 接入 pyelftools 或 LIEF 解析 ELF 元数据：当前先用标准库方式解析 ELF class、端序、架构和入口点，尚未接入 pyelftools/LIEF。
- [~] 提取 PE 导入表、导出表、节区、证书、版本信息、imphash、pdb、overlay：当前仅提取 PE 头部和节数量。
- [~] 提取 ELF section、dynamic symbols、rpath、interpreter、packed 痕迹：当前仅提取 ELF 头部基础字段。
- [ ] 提取脚本中的 URL、IP、命令和混淆特征。
- [~] 安全枚举压缩包内容：当前支持 zip/tar 元数据枚举，不解压、不执行；rar/7z 尚未实现。
- [ ] 安全解包压缩包，限制层级、路径穿越和总大小。
- [ ] 禁止执行自解压程序、安装器、文档宏和归档内文件。
- [ ] 接入 YARA 静态规则。
- [ ] 接入 capa 能力识别。
- [ ] safelist 过滤已知良性或无执行价值文件。
- [~] 输出完整 `identification.json`，包含 target、dependencies、ignored、children：当前已有 metadata 和 children 字段，target 结构仍需补齐。

## 6. VM-only 安全边界

- [x] 在代码中定义 VM-only 执行策略。
- [x] Node lease 返回 `allowed_execution_context=guest_vm`。
- [x] Node lease 返回禁止上下文：server、processing\_worker、node\_agent\_host、host\_shell、container、ci。
- [x] Node Agent 合同函数明确禁止宿主机执行。
- [x] Guest Agent 在非 `guest_vm` 上拒绝 `/store`。
- [x] Guest Agent 在非 `guest_vm` 上拒绝 `/execute`。
- [x] 测试覆盖宿主机执行阻断。
- [x] 服务端和 Node Agent 当前只做字节存储、传输、只读静态解析和结果上报，不执行样本。
- [ ] 增加样本文件目录 noexec 挂载建议和检查。
- [ ] 增加宿主机共享剪贴板、共享目录、drag-drop 禁用检查清单。
- [ ] 增加 Rooter 最小权限进程设计。
- [ ] 增加样本下载和工件下载审计日志。
- [ ] 增加插件执行白名单和管理员部署策略。
- [ ] 增加防止普通用户上传任意 Python 插件执行的机制。

## 7. 调度器和任务状态机

- [x] 提交后创建 `queued` task。
- [x] 节点按平台领取任务。
- [x] 领取任务后机器状态变为 `leased`。
- [x] 任务进入 `finished`、`failed`、`cancelled` 后释放机器。
- [x] 支持状态：`queued`、`leasing`、`starting_vm`、`preparing_guest`、`running`、`collecting`、`postprocessing`、`finished`、`failed`、`cancelled`。
- [x] 状态机基础字段已持久化：task 记录 status、node、machine、lease、timeout、route、error。
- [ ] 支持 `submitted`、`identifying`、`waiting_config` 等更细粒度 analysis 状态。
- [ ] 支持优先级调度。
- [~] 支持超时控制：当前有任务 timeout 字段和 lease 过期回收；真实 VM 执行超时控制待接入。
- [ ] 支持指定机器、标签、系统版本和架构。
- [~] 支持网络路由策略选择：当前提交参数和任务字段保留 route；真实 VM 网络路由尚未实现。
- [ ] 支持批量提交。
- [x] 支持任务取消。
- [x] 支持任务重跑。
- [ ] 支持节点故障转移。
- [ ] 支持多节点并发租约压测。
- [ ] 接入 Redis Streams/RQ 或 RabbitMQ 队列。

## 8. Node Agent

- [x] CLI 注册节点。
- [x] CLI 上报 VM 机器池。
- [x] CLI 领取任务。
- [x] CLI 将 Guest Plan 写入本地文件。
- [x] CLI 上报任务状态。
- [x] CLI 上传 JSON/JSONL 行为事件。
- [x] CLI 上传 ResultServer 风格状态消息。
- [x] CLI 上传普通任务工件。
- [x] 支持 YAML/JSON 节点配置加载，包含机器池、Guest Agent 端口、machinery、抓包和工作目录配置。
- [x] 封装 Node Agent API Client：注册、租约、状态、事件、ResultServer 状态、样本下载和工件上传。
- [x] 支持 `run-once` 和 `run-loop` 任务生命周期编排。
- [x] Node Agent 不执行样本。
- [x] 定义 `machinery` 统一抽象和 `noop` 开发后端。
- [~] 对接 KVM/libvirt/QEMU：已实现 `virsh` 基础命令后端，支持快照恢复、启动、关闭、截图、内存转储命令，待真实宿主机验证。
- [~] 对接 VirtualBox：已实现 `VBoxManage` 基础命令后端，支持快照恢复、启动、关闭和截图命令，待真实宿主机验证。
- [~] 对接 Hyper-V：已实现 PowerShell Hyper-V 基础命令后端，支持快照恢复、启动和关闭命令，待真实宿主机验证。
- [~] VM 启动前恢复 clean snapshot：命令后端已按机器配置的 snapshot 字段调用恢复，尚未做快照健康校验。
- [ ] 每任务创建 disposable overlay。
- [~] 任务结束后关闭 VM 并销毁 overlay：已实现 VM 关闭命令和 overlay 清理占位，overlay 创建尚未实现。
- [x] 检查 Guest Agent 健康状态，并要求返回 `is_guest_vm=true`。
- [~] 上传 analyzer、配置和样本到 Guest VM：当前已上传任务配置和样本；analyzer 包尚未实现。
- [~] 启动和停止宿主机抓包：已实现 tcpdump 管理器和 PCAP 工件上传路径，待真实权限、接口和网络策略验证。
- [ ] 管理网络隔离和路由。
- [ ] 管理 ResultServer 本地服务。
- [~] 收尾采集内存转储、截图和 PCAP：machinery 已有内存/截图接口，PCAP 可上传；尚未在完整收尾策略中自动启用内存和截图。
- [~] 处理任务超时、Guest Agent 失联和 VM 启动失败：run-once 已归档为失败状态并上报错误码，重试和故障转移待实现。

## 9. Guest Agent

- [x] Guest Agent API 骨架。
- [x] `/health` 返回执行上下文。
- [x] `/store` 仅在 `guest_vm` 上允许保存样本。
- [~] `/execute` 已定义接口，但当前返回 501。
- [ ] 实现 Windows Guest Agent 单文件部署。
- [ ] 实现 Linux Guest Agent 单文件部署。
- [ ] 支持 `/status`。
- [ ] 支持 `/store` 分片传输。
- [ ] 支持 `/extract`。
- [ ] 支持 `/execute`。
- [ ] 支持 `/kill`。
- [~] 支持上传状态、日志和工件到 ResultServer：当前服务端提供最小替代接口，Guest Agent 尚未直接对接。
- [ ] 支持自更新和版本校验。
- [ ] 支持随机化 Agent 路径和名称，降低被样本识别概率。

## 10. Windows 动态分析

- [ ] Windows generic package 执行 exe。
- [ ] dll、msi、ps1、bat/cmd、js/vbs/wsf/hta、lnk 执行包。
- [ ] Office/PDF 文档分析包。
- [ ] URL 浏览器访问包。
- [ ] 进程树采集。
- [ ] 命令行采集。
- [ ] 文件创建、修改、删除采集。
- [ ] 注册表采集。
- [ ] 服务和计划任务采集。
- [ ] 网络连接、DNS、HTTP、TLS 采集。
- [ ] 截图采集。
- [ ] 掉落文件采集。
- [ ] 内存和进程 dump。
- [ ] ETW/Sysmon collector。
- [ ] 可选 API Hook 或 Procmon 风格 collector。
- [ ] AMSI/ETW 增强脚本采集。

## 11. Linux 动态分析

- [ ] Linux generic package 执行 ELF。
- [ ] shell、python、perl、deb、rpm、AppImage、JAR 执行包。
- [ ] URL 浏览器访问包。
- [ ] strace 基础采集。
- [ ] `/proc` 进程信息采集。
- [ ] 目录快照 diff。
- [ ] 文件 open/create/write/delete/rename/chmod/chown 事件归一化。
- [ ] 网络 connect/listen/accept/DNS/HTTP 事件归一化。
- [ ] tcpdump PCAP 采集。
- [ ] 截图采集。
- [ ] 掉落文件采集。
- [ ] 内存和进程 dump。
- [ ] eBPF collector PoC。
- [ ] auditd fallback。
- [ ] fanotify/inotify 文件变更增强。
- [ ] seccomp notify 高级受控执行。

## 12. ResultServer

- [x] 当前服务端已支持按任务上传工件，作为 ResultServer 的最小替代。
- [x] 最小行为事件上报接口。
- [x] 最小状态消息上报接口。
- [ ] 独立 ResultServer 服务。
- [~] 事件流 JSONL 或 gRPC stream：当前支持批量事件 API 并落盘 JSONL，尚未实现长连接流式或 gRPC。
- [~] 写入 `events.jsonl.zst`：当前已写入未压缩 `events.jsonl`，尚未接入 zstd。
- [~] 接收截图、掉落文件、内存、PCAP、TLS key、日志：当前通用 artifact 上传可保存任意工件字节，尚未做专用类型校验和解析。
- [x] 状态消息：running、complete、exception、heartbeat。
- [ ] 大文件断点续传。
- [~] 上传限流和配额：当前有大小上限，尚未实现速率限制和细粒度配额。
- [x] 校验 task id 和 token。
- [ ] 校验来源 IP 是否属于绑定 VM。
- [~] 校验上传路径是否属于允许目录：服务端存储路径已限制在 storage root 且工件名会安全化，来源路径策略尚未实现。
- [~] 支持同名替换和追加策略：events/status JSONL 支持追加和工件 upsert，普通 artifact 仍按唯一 id 保存。
- [ ] 异步转存对象存储。

## 13. 结果处理和报告

- [x] 未执行动态分析时返回基础报告。
- [x] 任务进入终态或工件上传后刷新 `report.json`。
- [x] 报告包含 info、target、static、behavior、tasks、artifacts、errors 基础结构。
- [x] report 表已存在并记录 report key。
- [ ] Identification Worker。
- [ ] Pre Worker。
- [ ] Post Worker。
- [ ] 插件接口 `ProcessingPlugin`。
- [~] 解析动态行为日志：当前解析任务 `events.jsonl` 基础事件。
- [~] 进程树聚合：当前按 `process.create`、`process.start`、`execve` 生成基础进程树列表。
- [~] 文件、注册表、网络行为聚合：当前聚合 file、registry、network 基础事件计数和摘要。
- [ ] PCAP 解析，提取 DNS、HTTP、连接摘要。
- [ ] MITRE ATT\&CK 映射。
- [ ] 行为签名引擎。
- [ ] 威胁评分。
- [ ] HTML 报告。
- [ ] IOC 导出。
- [ ] STIX/MISP 导出。
- [ ] ATT\&CK Navigator layer 导出。

## 14. 行为签名和检测层

- [ ] 签名基类 `Signature`。
- [ ] Windows 持久化、进程注入、注册表、服务、计划任务签名。
- [ ] Linux 反调试、提权、文件写入、网络连接、LD\_PRELOAD 签名。
- [ ] 签名证据字段。
- [ ] severity 和 confidence 评分。
- [ ] MITRE 技术编号映射。
- [ ] ML 检测插件框架。
- [ ] 静态模型：MalConv、strings n-gram、PE 特征。
- [ ] 动态模型：API/syscall 序列、行为统计。
- [ ] 模型版本、训练数据日期、特征版本、阈值记录。
- [ ] 模型输出可解释字段。

## 15. 网络隔离和路由

- [ ] 默认 `drop` 网络策略。
- [ ] 允许 ResultServer、DNS、DHCP 必要通信。
- [ ] 支持 internet 路由。
- [ ] 支持 VPN 路由。
- [ ] 支持 Tor 路由。
- [ ] 支持 SOCKS5 路由。
- [ ] 支持 INetSim。
- [ ] 阻断 VM 到 VM。
- [ ] 阻断 VM 到宿主机非必要端口。
- [ ] 每 VM 独立 bridge。
- [ ] 出口限速。
- [ ] 出口审计。
- [ ] tcpdump 和 Suricata 集成。

## 16. 部署和运维

- [ ] 单节点 MVP 部署文档。
- [ ] Ubuntu Server 24.04 LTS 宿主机部署文档。
- [ ] KVM/libvirt/QEMU 安装脚本。
- [ ] Windows 10/11 x64 VM 模板制作文档。
- [ ] Ubuntu 22.04/24.04 x64 VM 模板制作文档。
- [ ] VM clean snapshot 校验。
- [ ] systemd 服务文件。
- [ ] Docker Compose 仅用于可信服务组件，不用于执行样本。
- [ ] 日志配置。
- [ ] 监控指标。
- [ ] 数据清理任务。
- [ ] 备份和恢复 SQLite。

## 17. M1 单节点可跑通验收

- [x] API 可以提交文件并返回 analysis id。
- [x] Web 页面可以提交文件并跳转到分析详情页。
- [x] SQLite 和本地对象存储基础模型。
- [x] Node Agent 可以注册节点、上报机器、领取任务。
- [x] 服务端可以按 analysis id 查询状态、报告、工件。
- [x] 任务完成后能生成基础 `report.json`。
- [~] Node Agent 控制 KVM 恢复和关闭 VM：已实现 `virsh` 命令后端和 run-once 编排，待真实 KVM/libvirt 宿主机验证。
- [~] Windows Guest Agent 响应 health/store/execute/status/upload：当前通用 Guest Agent 骨架有 health/store/execute，其中 execute 返回 501，status/upload 待实现。
- [~] Linux Guest Agent 响应 health/store/execute/status/upload：当前通用 Guest Agent 骨架有 health/store/execute，其中 execute 返回 501，status/upload 待实现。
- [ ] Windows VM 执行 exe 并采集进程树、PCAP、截图、掉落文件。
- [ ] Linux VM 执行 ELF 并采集 strace、进程树、PCAP。
- [~] 每次任务后 VM 回滚到干净快照：machinery 后端具备快照恢复命令，overlay 和健康校验尚未完成。
- [~] 默认网络为 drop：提交参数和任务路由默认值已实现，真实 VM 网络隔离尚未实现。
- [~] 单任务工件大小有上限并可记录超限错误：当前有全局大小上限和 413 响应，尚未按单任务累计配额。
- [x] 验证服务端、处理 Worker、Node Agent、宿主机不会执行样本。

## 18. M2 处理和报告完善

- [ ] identification/pre/post Worker。
- [~] PE/ELF 静态解析。
- [ ] YARA。
- [ ] capa。
- [ ] PCAP 解析。
- [~] 行为事件归一化：当前按事件名聚合 process/file/registry/network 基础字段，统一事件模型待完善。
- [ ] 签名引擎。
- [ ] 威胁评分。
- [~] Web/HTML 报告：当前已有 Jinja2 分析详情页和基础行为摘要，完整报告页面、签名、MITRE、PCAP、工件预览待完善。
- [ ] 按进程查看文件、网络、注册表或 syscall。
- [ ] 签名命中显示证据和 MITRE 映射。

## 19. M3 增强监控

- [ ] Windows ETW collector。
- [ ] Windows Sysmon collector。
- [ ] Linux eBPF collector PoC。
- [ ] 内存 dump。
- [ ] 进程 dump。
- [ ] Suricata。
- [ ] TLS key 或代理能力。
- [ ] 可选交互桌面。
- [ ] Windows 常见持久化、进程注入、网络行为稳定识别。
- [ ] Linux 文件写入、反调试、网络连接、子进程链路稳定识别。

## 20. M4 分布式和高隐蔽后端

- [ ] 多节点调度。
- [ ] 节点故障转移。
- [ ] 任务重试。
- [ ] DRAKVUF/VMI 后端 PoC。
- [ ] S3 长期存储。
- [ ] 冷热数据清理。
- [ ] ML 检测插件。
- [ ] 多节点并发任务不重复分配。
- [ ] 节点离线后任务可恢复或失败原因可解释。
- [ ] VMI 后端对指定 Windows 样本生成基础进程、文件、网络报告。
