# Controlled Candidate Refresh — 第一批执行记录

日期：2026-09-04。用户选择当前会话顺序执行、分批检查；本批仅 Task 1–2。

## 隔离与边界

- 实现分支：`codex/eventra-candidate-refresh`。
- 实现工作区：`/Users/didi/Eventra-workspace/Eventra/.worktrees/eventra-candidate-refresh`。
- 起点：`5549959697e98345490d255c619142705caec900`。
- 原 checkout、运行器工作区和用户未跟踪文件未修改。
- 无 live Multica 配置、评论、授权或状态写入，无远端 push，无 PR #14 更新/合并，无部署或历史 Smoke。
- 未修改前后端业务代码、知识正文、通用 skill；未新增依赖。

## 实现结果

| 任务 | 本地提交 | 内容 |
|---|---|---|
| Task 1 | `ebb0ac63e7b7bc15cc8a4f67164b34d57321c14c` | 严格请求编码、摘要与派生 ref；member grant 和 Engineer prepared evidence 契约 |
| Task 2 | `3f7d571a14af1e225593c04335100bba2a2e86fa` | 隔离 Git 对象验证、精确双父无冲突合并树、暂存与候选 ref 发布边界 |

两组新增测试各 16 项，均先观察缺接口失败，再实现并观察通过。协议测试包含表驱动负例，测试方法数量不等于负例数量。

Git 测试使用临时 bare origin、Engineer 工作仓库以及模块创建的临时 bare verifier，而非原计划描述的两个持久 clone；真实执行 Git merge/object/ref 检查，运输层只将固定 fork URL 映射到本地 bare origin。测试不会连接 GitHub，也不模拟树验证结果。

验证覆盖父顺序、缺父/额外父、额外内容、冲突、无意义刷新、版本漂移、replace refs、自定义 merge driver、dirty index 保持、远端暂存证明、基线漂移、暂存冲突、并发分歧和 ACK 丢失恢复。

## 本批验证

| 检查 | 结果 |
|---|---|
| 起点 Python 全量 unittest | 496 项通过 |
| Task 1 后 Python 全量 unittest | 512 项通过 |
| Task 2 后 Python 全量 unittest | 528 项通过，15.483 秒 |
| `npm run test:footer-meta` | 14 项通过 |
| `npm run test:layout-hydration` | 1 项通过 |
| `npm run test:dashboard-profile` | 11 项通过 |
| `npm run test:local-contract` | 4 项通过 |
| `npm run lint -- --ignore-pattern '.worktrees/**'` | 0 错误，34 条警告；本批未改这些业务文件 |
| 知识 `verify` | 12 条索引通过 |
| `npm run build` | 沙箱内字体下载因本地代理 `EPERM` 失败；同一代码主机重跑 exit 0，编译及 11/11 静态页面生成成功 |

Python 全量命令：`python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'`。
知识命令：`python3 -B -m tools.multica.knowledge verify --frontend-root . --backend-root /Users/didi/Eventra-workspace/Eventra-Backend`。
Python 输出中的 attempt=4 argparse 错误来自预期负例，不是 suite 失败。

起点使用完整 SHA 生成 Context Receipt（任务 `PRO-122-refresh-implementation`），读取并验证 frontend repository map / invariants / known pitfalls / testing guide，以及 shared system map / dependency graph，无冲突。此收据仅用于本批实现上下文，不是未来刷新候选的 prepared evidence。

## 未完成与限制

- Task 3–9 尚未实现：真实 authority 快照、请求准入、状态机、durable reservation、准备完成入口、登记/采纳恢复、CLI/Watcher 互锁和完整交付评审。
- 协议解析仅验证声明结构与绑定，不证明 live 授权有效、当前身份、未消费状态或命令真实执行；这些必须由后续 executor 重新读取并核验。
- Git 发布方法不是授权入口。正常 push 不是服务器端 expected-old CAS；仍要求单 Lead 和协作写者暂停/互斥。
- Git 测试仅证明本地对象与 ref 行为；真实 GitHub 凭据/网络可用性未验收。
- 新测试不依赖真实 Backend 或 live 服务；既有知识测试仍有本机历史 Backend 工作区依赖，不能据此宣称任意 CI 环境可复现。
- Node 依赖通过原 checkout 的祖先 `node_modules` 解析；本批没有从零安装依赖。
- 本批通过不等于控制面已发布，也不等于 PR #14 可以刷新或合并。

下一批：Task 3 的 authoritative snapshot / 精确授权准入，再执行 Task 4 的纯状态转换与历史证据保护。
