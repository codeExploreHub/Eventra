# Eventra：受控候选刷新设计

日期：2026-09-04

状态：书面设计已获用户确认（“符合”）；进入实现计划，尚未实现或启用。

范围：Eventra 本地试点，不修改通用 `multica-multi-repo-delivery`。

## 1. 问题与已知事实

PRO-122 的 Stage 1 子任务 PRO-123 已以 `done/pass` 完成，候选是
`e507758673640a5b0e0da5bec479245f5a72a086`，管理的 PR 是
<https://github.com/codeExploreHub/Eventra/pull/14>。原始证据为
`01a06887-583a-7dca-9e14-b7ac3f477a2f`。这些历史身份不能重写。

前置 PR #15 已合并，合并提交为
`d7af5a5c12e006024d3d71a7d3faf210c1a96df5`，包含知识可信度修复
`49eb4d5a2fa4637308d0d8dcf0dbcf2cdbf945e0`。本轮重新查询 GitHub 确认
PR #15 为 MERGED、PR #14 仍为 OPEN 且 head 未变。父任务暂停状态及其
Stage 1 身份来自此前读取；执行前必须重新读取，本文不是实时执行授权。

现有 `workflow.py` 有两条有意设置的保护：

- `finish_phase` 不允许替换已完成子任务的终态元数据。
- `_implementation_assignment_problem` 要求实现证据、父候选和当前 PR head
  一致；先推新 head 或只手改父候选都会触发拒绝。

已有 repair 由真实非 PASS 门禁及 FailureBundle 驱动。前置更新不是失败修复，
不能编造 FAIL、消费 repair 次数，或给旧 PASS 换一个 SHA。

## 2. 选择与最小范围

采用独立的 `refresh` 阶段，保留原实现阶段，随后重新进入正常质量门。
另建交付父任务会割裂 PRO-122 的追踪；直接改旧元数据会破坏证据，因此均不采用。

第一版只允许：

- version-2、`frontend-only`、attempt=0 的 Eventra 父任务。
- 唯一 Stage 1 implementation 已 done/pass；next_stage=2；尚无后续子任务。
- 管理的个人 fork PR 仍 open、未合并，head 与父候选、原实现证据一致。
- 无活动子任务、无 repair/smoke/refresh reservation；仅一个 Delivery Lead 写入者。
- 一个父任务最多一次刷新；不支持刷新后再次刷新、跨仓刷新、评审中插入刷新。
- 前置为同一仓库中已合并 PR 的确切 merge SHA，并位于当前目标分支历史中。
- 仅自动无冲突的 merge：不得夹带业务修复、手动冲突解决或新知识正文。

内容冲突、测试失败或非法身份均停为可见 blocked，保留诊断与现场，不自动
进入 repair。扩大这些能力必须另行设计。正常 review/QA 的真实失败仍走原 repair。

## 3. 授权与身份

新增版本化 `eventra-candidate-refresh-request-v1`，由可信只读快照构造规范 JSON。
SHA-256 摘要绑定以下字段：

- schema_version、workspace、parent UUID/identifier、源 Stage/attempt；
- parent revision（冻结时的起始 revision）、原 last_action 与 merge_state；
- 源 implementation UUID、原证据 UUID/revision、源 SHA；
- 同一 managed PR 的规范 URL、仓库、head/base 分支；
- 前置 PR URL、merge SHA、检查时的目标分支 SHA；
- 源 Project、Squad、Lead、Frontend Engineer 身份；
- 固定刷新 Stage=2、唯一暂存 ref 名、允许的树变换、`merge_permission=hold`；
- 执行本协议的已审阅 control-tool commit SHA。

暂存 ref 在同一 fork 内，确定性命名为 `refs/heads/eventra-refresh/<request_digest>`，
不是第二个 PR。请求明确包含创建该 ref 及正常快进更新原 PR 分支的授权范围。

live grant 必须来自父任务中 API 确认 `author_type=member` 的评论，正文只含
版本、request_digest、`granted_refresh=1`。引用 server-assigned 评论 UUID，
重新验证所属父任务、作者、正文和 revision；agent 转述、普通“确认”、本设计
文件和 caller 传入的作者身份都不是运行时 grant。

本对话批准的是设计方向，不自动向 Multica 写入该 grant。准备 live 时先展示
精确请求，再由成员发布，或取得明确的代发授权。被编辑、外来、重复、已消费
或旧请求的 grant 都拒绝。PR #15 的合并批准不等于 PR #14 的合并批准。

## 4. 状态转换与证据语义

| 步骤 | 状态与允许动作 | 保持不变的内容 |
|---|---|---|
| 冻结 | Core 校验请求，返回 create_refresh_stage；Lead 建立 reservation | 原候选、原 PASS、原 PR head |
| 初始化 | Stage 2 唯一 refresh 子任务先 backlog，写完并读回 provenance 后才启动 | Stage 1 所有记录 |
| 准备 | Engineer 生成并测试新的纯合并提交，发布到指定暂存 ref | 原 PR 分支和父候选 |
| 准备完成 | refresh 子任务 done/pass，证据明确只证明新暂存提交的准备与测试 | 不表示 PR 已更新，不是 Review/QA PASS |
| 发布 | Lead 验证准备证据与 Git 对象，登记唯一 target SHA 后快进原 PR 分支 | 父候选暂仍为 source，reservation 解释这个窄窗口 |
| 采纳 | Lead 读回 PR head、记录采纳凭证、复制新候选并提交消费状态 | 旧实现证据不变，merge_state=not_ready |
| 新质量门 | 清除已提交 reservation，Core 返回 Stage 3 的 review + qa | attempt=0；旧 PASS 不转移 |
| 等人合并 | 新门禁通过后保留 merge hold，等待精确候选合并授权 | 不自动合并、Smoke、部署 |

刷新使用独立的 `refresh_generation=1`，不增加 repair attempt，也不重用 Stage。
父 next_stage 在完整 Stage 2 初始化后变为 3，last_action 记录刷新创建动作；
发布与采纳使用 reservation 的子状态和独立采纳凭证，不篡改创建动作。
进入 Stage 3 时按现有规则记 gate action 并将 next_stage 推进到 4。

刷新期间父任务只在有效 grant 和 durable reservation 建立后从暂停变为
in_progress。若准备失败，父任务 blocked，候选不变；若 PR 已快进但采纳中断，
只允许恢复采纳，不能回滚 PR、丢弃 reservation 或启动普通门禁。

## 5. 候选构造与发布边界

Engineer 的职责是准备，不直接推送原 PR 分支：

1. 从安全 fetch 来源获取 source 和 prerequisite，逐一核对 SHA；使用任务专属
   命名工作区，不修改权威 checkout、别人的 worktree 或运行器基线。
2. 生成两父 merge commit，父提交顺序严格为 `[source, prerequisite]`。
3. 新树必须等于指定 Git 工具版本无额外策略参数执行 `merge-tree` 得到的无冲突树。
   有冲突即退出；禁止 ours/theirs 替代、额外提交或内容修复。
4. 运行完整测试，生成新 Context Receipt，对 selected claims 显式核验。
5. 仅向请求指定暂存 ref 正常推送，记录 immutable prepared evidence，再用专用
   refresh 完成入口结束。重试遇到已存在且相同 SHA 的 ref 为幂等成功；不同则拒绝。

准备证据由所分配 Engineer 发布，绑定 child/request/source/prerequisite/target、
命令退出码、测试结果和暂存 ref。Core 要读取内容、严格解析版本化封套、核验
作者及未修改的评论身份，不仅检查 UUID。由新证据证明 target，不改旧证据。

Lead 必须独立从 fork 读取 target 的 Git 对象，关闭 replace-object 影响，
核验两个父提交与树；不信任证据声称的父关系或本地路径。纯合并检查针对整个树，
不只验证文件名 allowlist。还需确认当前目标分支仍等于请求冻结的 SHA；漂移则
本轮停止，不能偷偷改用新的 master。git 版本与合并算法变化也应可诊断地拒绝。

发布前将唯一 target SHA 与 prepared evidence UUID/digest 写入并读回 reservation。
然后 Lead 正常快进原 head ref（不使用 force、force-with-lease 或重写历史）。
若其他人已推送不等于 source/登记 target 的提交，立即阻断，不覆盖。
发布后重新读取 GitHub PR URL、分支、state 和 head，再采纳。暂存 ref 本轮保留，
清理属于后续明确操作，不自动删除。

只有合法 refresh reservation 内的登记 target 可以解释 head 漂移；对其他父任务、
其他阶段、未登记 SHA 或不匹配 evidence，原有 out-of-band 保护完全保留。

## 6. 持久化、并发与中断恢复

采用 `eventra.refresh.version=1` 作为 v2 的显式扩展标志。没有该标志的任务行为
不变；旧工具遇到新 kind 必须拒绝，不能忽略后按旧候选继续。

持久字段包括 request/authorization 的 UUID 与摘要、不可变源身份、refresh child
身份、prepared target/evidence、consumed receipt、adoption receipt 和 reservation。
精确字段与规范编码由专门契约模块定义；缺失、额外字段、非法枚举和冲突值拒绝。
consumed/adoption receipt 永久保留，reservation 最后清除。

merge hold 在后续 Gate/Repair 中持续生效；只能由新的成员授权、绑定当前全部
候选 SHA 和 managed PR 身份后释放。刷新 grant 和刷新 PASS 均不能释放它，
候选再次变化后原合并授权失效。该设计只要求守住 hold，自动化合并授权入口
若未实现则交回人工操作，不默认恢复旧的自动合并路径。

reservation 只按以下持久前缀推进：
`reserved → child_initialized → child_dispatched → candidate_registered → published → adopted`。
每个动作均重新读回相关 authority，普通 planner、Watcher 和其他 executor 在该
窗口只允许等待或提示同一动作恢复，不能另建子任务、门禁、repair 或 Smoke。

- 冻结起始 revision 不是要求整个执行过程 revision 不变。每个自身写入边界绑定
  预期元数据/状态变更并重新读回；不把全部 revision 更新当作合法，也不完全忽略。
- 不假设 Multica KV 写入具备 CAS/事务。试点要求单一 Lead；本机进程锁仅防合作
  进程重复进入，durable reservation 负责断点恢复，不宣称跨机器强一致锁。
- 子任务创建后丢 ACK：按 parent/stage/request/assignment 查找；唯一精确匹配才续接。
  初始化不完整但匹配已知写入前缀时补齐；额外字段、重复 child、未知 run 均阻断。
- 推暂存 ref 或原 PR 分支后丢 ACK：先读远端；等于指定 SHA 才视为效果已发生。
- 采纳写到一半：只能用源/登记 target 的合法字段组合恢复；不能再建 child 或再消费授权。
- 无活跃 run 的保留 reservation 不是垃圾。Watcher 只能提示确定的恢复入口，不能清除。
- 任意异常都保留已完成历史和解释性证据；不伪造成功，不用回滚掩盖部分发布。

## 7. Review/QA 的工作区与运行器分离

针对本轮 fresh gate 的 handoff，显式区分 runtime workspace 和 inspection workspace：

- runtime workspace 保持启动时的 branch/commit，不执行 detach/reset/clean；它不是被测候选。
- 在任务拥有的独立 inspection worktree 中 fetch 并固定 exact candidate；只从这里测试。
- 在被测提交生成 Context Receipt；执行完成 helper 时使用已批准、固定 SHA 的 control
  checkout，并记录两个身份。不得把 runtime baseline checkpoint 当作被测或交付代码。
- 带 private env 的文件不复制；缺依赖按 lockfile 安装。Python 测试结束后串行运行
  全树 ESLint，避免临时目录竞态。保留此前发现的主机专属测试夹具限制。
- done/pass 只证明对应阶段及 SHA；运行器失败和测试 PASS 分开记录，不修改原证据。
- 不删除他人的 worktree。由当前任务创建的检查工作区只在证据完整且干净时按明确
  生命周期规则清理；失败现场保留。

这不是把 PRO-126 的失败记录改成成功，也不修改全局 Multica runtime 行为。
只修改相关 Eventra 角色指令与当前 gate handoff，并测试契约说明的一致性。

## 8. 模块与控制面上线顺序

预期改动仅限 Eventra：

- 新 `tools/multica/candidate_refresh.py`：请求/证据/凭证严格模型、纯验证与刷新执行边界；
  不把全部实现继续堆入大型 workflow 文件。
- `workflow.py`：快照解析、planner 分支、refresh 完成与采纳接入、fresh gate 和 merge hold；
  Watcher/恢复/阶段历史校验同样识别新 kind，不仅放宽枚举。
- 现有适配层按必要性增加 Git 对象读取和暂存 ref/正常快进发布，不泄露凭证。
- 配套 `test_candidate_refresh.py`、workflow/adapter/operator-docs 回归测试。
- `instructions/delivery_lead.md`、`frontend_engineer.md`、`independent_reviewer.md`、
  `integration_qa.md`、`README.md` 和 pilot runbook 的局部说明。
- 不修改前后端业务代码、知识正文/种子摘要、通用 skill 或 PRO-116/120/121 历史。

不能让尚未审阅的刷新代码自行批准和部署自己。先在独立控制面变更中实现、测试、
评审并获人批准，再启用固定工具 SHA；PRO-122 保持暂停直到这一步完成。
如果控制面上线推进了 master，则 live 请求必须重新冻结实际 prerequisite/base SHA，
它至少包含 PR #15 的合并与获准的工具版本；不得继续拿本文的 d7af5a5c 当作最新 master。
新的精确请求再获 live 授权，随后才能执行 PRO-122 的刷新。

## 9. 验收与测试

行为实现必须先 RED 再 GREEN，至少覆盖：

1. 正常链：原 Stage 1 不变 → 唯一 Stage 2 refresh → target 被采纳 → Stage 3 两个新门禁。
2. 非成员、错父任务、修改后评论、错摘要、重复/已消费授权、错 source/prerequisite 均拒绝。
3. 已有 gate、repair、Smoke、活动 child、跨仓、attempt>0、第二次 refresh 均拒绝。
4. 错父顺序、缺父、附加代码、冲突树、replace object、错 ref/仓库、目标分支漂移均拒绝。
5. 准备测试失败时原 PR/候选不变；不转为 repair，不增加 attempt。
6. 每个持久写入边界中断与丢 ACK 可恢复；重复调用不重复创建 child、run 或消费凭证。
7. 发布中只允许登记 target；任意其他 head 漂移仍拒绝；未采纳不得创建 gate。
8. PRO-123 原 evidence/metadata byte-for-byte 不变；旧 PASS 不计入新 target 的门禁。
9. 旧 v2 流程回归、未知新 kind 拒绝、Watcher 与所有 mutation executor 的 reservation 互斥。
10. runtime 分支保留、inspection SHA 精确、QA/Reviewer 使用同一新 target、merge hold 生效。

运行全量 Python 控制面测试、四组前端测试、串行 lint、Webpack build；报告真实数量、
退出码、既有告警与主机依赖。Git 变换测试使用真实临时仓库，live API 测试默认使用
严格快照/故障注入，不为验证而真实重复推送或运行历史 Smoke。

## 10. 审阅结论与本轮边界

本设计不声称刷新已实现，也不声称 PRO-122 已恢复。方案自检重点是：没有复用旧
PASS、没有虚构 repair、发布前已有登记 target、部分提交可恢复、工具先审阅后启用、
且最终仍保留人的合并决定。下一步先由用户审阅本设计，再写实现计划。

设计阶段读取的知识：frontend-repository-map、frontend-invariants、eventra-system-map、
eventra-dependency-graph。其职责/安全/依赖约定与当前 AGENTS、索引和正文相符；
这不表示其余业务事实已经重新验证。这里的设计文档不是 canonical Knowledge Candidate。
