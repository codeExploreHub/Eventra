# Controlled Candidate Refresh — 第二批执行记录

日期：2026-09-04。用户要求继续当前会话顺序执行、分批检查；本批为 Task 3–4。

## 工作区与提交

- 隔离工作区：`/Users/didi/Eventra-workspace/Eventra/.worktrees/eventra-candidate-refresh`。
- 分支：`codex/eventra-candidate-refresh`；起点 `cd31f05459bfeda11c0e62ebc63c3def9e6388fa`。
- Task 3：`effadc49f09930ce94a04b19dd50500899ebcba1`，权威快照与首次请求准入。
- Task 4：`3df04fa8f5c30de7a583702ff90d7ee8934f9313`，纯状态机与 workflow 防护。
- 全部为本地提交；没有 push、live Multica 写入、PR #14 更新/合并、部署或历史 Smoke。
- 未修改业务代码、canonical 知识正文、通用 skill、原 checkout 或用户未跟踪文档。

## 本批完成

1. 新只读 `RefreshAPI`：显式 profile/workspace、固定 control checkout 身份、Project/Squad/角色映射、完整评论与子任务读取、PR 与前置版本检查，以及前后双读。
2. 首次准入：绑定 member grant 的作者、父任务、UUID、revision、正文及请求摘要；拒绝重复、外来、已消费/已有 reservation、非唯一源任务、活动子任务、多个 Lead、源证据和 PR 漂移。
3. 独立 refresh 状态建议：准备 PASS 不等于发布或 QA PASS；登记前不允许 target head；发布后只允许恢复采纳；凭证完整且 reservation 清除后才返回 fresh Stage 3 gate。
4. 历史保护：保留 Stage 1 证据与 SHA；refresh 不占 repair 次数；准备失败阻断而不造 repair。真实新门禁失败仍走原 repair planner，修复和后续门禁通过仍保留人工合并等待。
5. 旧 workflow 接口防护：`ParentSnapshot.refresh_state`、独立 phase provenance、严格 feature 字段读取、旧/新快照身份交叉校验；普通 `finish-phase` 不接受 refresh。

这不是完整的刷新 executor。这里的 `create_refresh_stage` / `resume_refresh` / `publish_refresh` 是纯建议，不能当作外部写入授权。完整中断恢复、每步写后读回和故障注入仍属于 Task 5–7。

## TDD 与验证

| 检查 | 结果 |
|---|---|
| 起点 Python 全量 | 528 项通过 |
| Task 3 新测试 | 18 项先缺接口 RED，再 GREEN |
| Task 3 后全量 | 546 项通过 |
| Task 4 新测试 | 20 项纯状态测试、17 项 workflow 测试、1 项 Smoke 旁路回归 |
| 本批最终 Python 全量 | 584 项通过，较起点增加 56 项 |
| `npm run test:local-contract` | 4 项通过 |
| `npm run test:footer-meta` | 14 项通过 |
| `npm run test:layout-hydration` | 1 项通过 |
| `npm run test:dashboard-profile` | 11 项通过 |
| `npm run lint -- --ignore-pattern '.worktrees/**'` | 0 错误，34 条未改业务文件中的警告 |
| `npm run build` | exit 0，Webpack 编译和 11/11 静态页面生成成功；本轮复用已有依赖/构建缓存 |
| 知识索引 verify | 12 条通过 |
| `git diff --check` | 通过 |

Python 全量命令：`python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'`。
知识命令：`python3 -B -m tools.multica.knowledge verify --frontend-root . --backend-root /Users/didi/Eventra-workspace/Eventra-Backend`。
argparse 的 attempt=4 错误来自既有预期负例，suite 的最终结果为 OK。

补充 RED→GREEN 回归收紧了以下问题：未完成的采纳凭证被误当作可恢复；后续子任务跳过 Stage 3；外层 workflow 读完后没有复核刷新 authority；Issue 详情回显 metadata 造成摘要自引用；服务端活动时间不适合作为自写入摘要字段。

## 实现细化与后续注意事项

- 首次冻结严格相等检查 revision，不使用 `revision >= old`。尚无可靠“仅评论使 revision 增长”的证明适配，所以遇到这种变化先拒绝并重新冻结。
- `RefreshScope` 来自可信操作配置，显式传入，不由 request 反填。当前校验 checkout SHA 和干净状态，但构造 scope 本身不证明控制面已经过人的部署批准；Task 8 必须接入经过批准的部署记录和固定工具加载路径。
- `load_refresh_snapshot` 保留 runner/github/parent 接口，并增加必须显式提供的 control_root/scope；无配置默认拒绝。冻结前可显式给 prerequisite_pr，登记后可从经过严格解析的 request 定位它，再读取 GitHub 验证。
- 本机 CLI help 已确认默认评论列表配合 `--full --compact` 读取完整线程，因此不用携带 stderr 游标的 recent/tail/summary 模式。未知字段、分页 envelope、折叠/截断内容或不完整线程均拒绝；这不是当前 live 评论形态的全量兼容性验收。
- 父 projection 对独立 metadata 读取绑定一次，排除 reservation 自身及 Issue 详情中回显的 metadata；排除 `updated_at` / `last_activity_at` 两个服务端活动时钟，但继续绑定 revision 和其余父任务字段。Task 5 必须验证真实写入的 revision 推进语义并建立 expected projection，不能把更大的 revision 一概当作合法。
- refresh child 新 provenance 使用 `eventra.refresh.version` / `.request_digest` / `.source_sha`；与 phase assignment 一起验证，绝不挪用 repair provenance。
- 旧 loader 遇到 refresh 必须注入显式配置的 authority reader，否则阻断。后续 CLI/Watcher/repair executor 的配置传递与全面互锁仍归 Task 8，当前未启用 live。
- 真实 GitHub/API 网络行为、跨进程写入序列和所有中断点未在本批验收。API 测试是严格进程边界替身，控制 checkout 使用真实临时 Git；不是 live 演练。
- 既有知识测试的本机 Backend 工作区依赖仍存在，不宣称任意 CI 主机都可直接复现。

## Context Receipt

起点与代码提交后都生成了 full-SHA receipt。下方为代码提交后的 canonical JSON；六条 selected claims 对照 AGENTS、仓库路径、配置和现有测试核验，无冲突。本收据仅用于控制面开发，不是未来刷新候选的 prepared evidence。

```json
{"candidate_shas":{"frontend":"3df04fa8f5c30de7a583702ff90d7ee8934f9313"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-known-pitfalls":"c4da9281e317ec71533e6feceab82f27d13dc907ad48bede6d2a737c35b2198e","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f","frontend-testing-guide":"21b02c4f90faf9504f696ace27e7dd7c6e175543f8cbd7e0638d402ace4d1783"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-known-pitfalls":"repository+task_type+path","frontend-repository-map":"repository+task_type+path","frontend-testing-guide":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-implementation","task_type":"implementation","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-known-pitfalls","frontend-repository-map","frontend-testing-guide"]}
```

下一批建议：Task 5 的唯一 Stage 2 创建、持久化 reservation、暂停意图与逐写入故障恢复，然后 Task 6 的 Engineer 专用准备完成入口。完整计划还剩 Task 5–9；独立审阅、控制面发布及 live 授权均尚未进行。
