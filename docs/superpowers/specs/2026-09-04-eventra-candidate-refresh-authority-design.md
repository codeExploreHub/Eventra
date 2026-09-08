# Eventra 受控候选刷新：冻结基线与 revision 证明补充设计

日期：2026-09-04。

状态：用户已同意“增加冻结 authority 与评论基线摘要”的修订方向；本书面补充待审阅，尚未实现。

本文件补充 `2026-09-04-eventra-candidate-refresh-design.md` 的授权与恢复规则。
只针对 Eventra 未发布的 refresh 协议；不改变原 Stage 1 证据、两父纯合并、
Engineer 准备 / Lead 发布、fresh Stage 3、merge hold 和独立控制面上线边界。
不修改通用 skill，不增加本地恢复日志或新服务，不执行 live 操作。

## 1. 为什么需要补充

官方 v0.4.38 的 [CreateComment SQL](https://github.com/multica-ai/multica/blob/v0.4.38/server/pkg/db/queries/comment.sql#L424)
在创建评论时原子地递增所属 Issue 的 revision。请求与 grant 发布会各推进一次。
[元数据更新 SQL](https://github.com/multica-ai/multica/blob/v0.4.38/server/pkg/db/queries/issue.sql#L561)
则只在值实际改变时递增；相同值重放不递增。
此前完整检查与本地复现见 `../plans/2026-09-04-eventra-candidate-refresh-batch-3-preflight.md`。

不能只检查 revision 不变，也不能把 `+2` 或 `>=` 作为授权证明。
需要同时证明当前内容仍来自同一个冻结基线，且版本差额恰好由允许的已发生动作解释。
这里证明的是受限协作环境中的状态一致性，不是服务端 CAS 或防恶意管理员篡改的审计日志。

## 2. 请求新增字段与编码

未发布的 request-v1 payload 新增必填对象 `baseline`，且只包含：

- `authority_digest`：第 3 节规范 authority 投影的 SHA-256。
- `comments_digest`：第 4 节冻结时父任务评论清单的 SHA-256。

两个值均为 64 位小写十六进制。它们参与现有 request_digest 的整体计算；
先算基线摘要，再算 payload 摘要，最后派生 staging ref，避免自引用。
基线不包含本次尚未创建的请求评论、grant 或 refresh 元数据。
`parent.revision` 继续保存冻结 revision，绝不被恢复代码改写为当前值。

现有本地 request-v1 尚未上线，因此收紧必填字段，不新增另一套兼容分支。
缺 baseline 的旧本地请求拒绝，不自动补摘要或迁移旧 grant；上线前重新冻结和授权。
普通 v2 delivery 不受影响。baseline 是摘要承诺，不是 caller 自报事实的真实性证明；
生成和每次使用都必须经过可信、明确 workspace/profile 的双读适配器。

## 3. authority 投影的精确范围

冻结只允许父任务已经 `blocked`；暂停动作不混入本次冻结事务。
若尚未暂停，先按另行授权的暂停流程处理，再生成请求。
冻结时必须无 refresh/repair/smoke 保留字段，且唯一 Stage 1 已 done/pass。

投影对象精确包含 `parent / metadata / source / assignment / pr / prerequisite / tool`：

- `parent`：完整父 Issue detail，移除 `metadata`、`updated_at`、`last_activity_at`。
  revision、status、status_category、position、title、description、labels、properties、
  分配与其他已支持的 detail 字段全部保留；缺失与 null 不互相替代。
- `metadata`：独立读取的完整父 KV，包括非 Eventra 键；冻结时不删除任何业务键。
  detail 中回显的 metadata 必须与独立读取相符，不能忽略两份响应的冲突。
- `source`：唯一原 Stage 1 的 detail（移除相同三个字段）、完整 metadata、原证据
  的身份 / revision / 正文。原证据必须继续满足已有的作者、UUID、digest 校验。
- `assignment`：完整已验证的角色、项目、Squad、成员映射。
- `pr / prerequisite / tool`：刷新快照中已经验证的全部规范字段，包含 head、base、
  merge SHA、control tool SHA 与 Git 版本。

规范编码沿用 canonical JSON：键排序、无额外空白、UTF-8、拒绝重复键与非有限数。
数组不擅自排序或丢弃元素；只有已有契约明确为集合的角色成员列表按契约排序。
原始 detail 出现适配器未支持的语义字段时拒绝，不能只取老字段凑出相同摘要。

运行中的 run 列表不进入静态摘要；每次写前独立验证单一 Lead、无未知活动 child，
分配一致。进入准备期后只允许唯一已登记 Engineer child 的合法运行。
不是把运行状态永久冻结，也不是以摘要代替运行互斥检查。

## 4. 评论基线

适配器完整读取父任务评论与线程，产生按 `comment_uuid` 排序的规范清单。
每条精确包含 `issue_id / comment_uuid / author_id / author_type / type / revision /
parent_id / created_at / content_digest`。`content_digest` 对原始 UTF-8 正文求 SHA-256；
不得 trim、改换行或只提取代码块。根评论的缺省 parent_id 规范为 null。
created_at 保留服务端的已验证值，不重新格式化。UUID、作者、revision、线程父子
关系均验证；缺失分页、重复 UUID、未知类型、折叠正文、缺失线程节点均拒绝。
`reply_count` 等派生信息用于完整性校验，不进入摘要；活动时间不代替业务 revision。

执行时只允许从当前清单中移除本次精确绑定的请求与 grant，再与冻结摘要比较。
请求必须为原父任务上 Lead 或 member 的单个规范封套；grant 必须为 member 发布。
两者 UUID 不同、revision=1，作者、正文与 request_digest 逐项校验，重复匹配拒绝。
不是按出现“批准”字样、任意两条最新评论或作者名称过滤。

因此新增普通评论、编辑或删除基线评论、换线程、修改请求/grant 都不能被计入
“允许的两次推进”。从冻结到采纳完成的窄窗口，Lead 不在父任务发布额外进度评论；
Engineer 的准备与诊断写在 refresh child。Watcher 若自动留言导致漂移也必须停止，
不能把该留言默默加入基线。接受额外父评论或重新基线化需要后续单独授权流程，
本版本不自动清除保留字段或覆盖旧请求以恢复。

## 5. 首次登记、授权与 revision 计算

`plan-refresh` 只读生成带 baseline 的请求。`stage-refresh-request` 仍需明确获准，
在同一父任务锁内双读并验证原始基线，再按固定顺序登记：

1. `eventra.refresh.request`：完整规范 envelope（第一笔就绑定唯一请求）。
2. `eventra.refresh.version=1`。
3. `eventra.refresh.merge_permission=hold`。
4. `eventra.refresh.request_digest`。

每次实际变更后读回。部分前缀也必须阻断旧流程；只有同一请求可补齐严格前缀。
不同请求、乱序字段、未知键、已存在但值不同的键均拒绝；相同值不重复写。
父任务始终 blocked；此过程不创建 child、发布评论、启动 run 或消费 grant。
前四笔未全部完成时出现本轮请求/grant 评论，不自动继续登记。

完整暂停标记读回后，才能发布请求评论，再发布成员 grant。二者分别创建后可
唤醒只读检查，但没有有效 grant 时不得执行刷新。首次执行需明确传入两个 UUID，
验证它们是相对于冻结评论基线新增的两条，再写 request_comment、authorization_comment，
最后建立 reserved。不能因发布了请求文件而自动代发任一评论。

设 R0 为冻结 revision，K 为已验证的本轮新增评论数，M 为已读回的固定父 KV
初始化前缀长度。建立 reservation 前必须同时满足：

- 当前 revision 精确等于 `R0 + K + M`。
- 仅撤销该已验证前缀的 KV、把 revision 恢复为 R0 后，authority 摘要等于冻结值。
- 仅移除该已验证评论集合后，评论清单摘要等于冻结值。
- 子任务、PR、证据、分配、工具和运行身份仍满足原约束。

K 在完整暂停登记之前只能为 0；之后依次为 0、1、2；有 grant 无请求非法。
M 前四步为 0..4；两个评论都通过后，绑定两个 UUID 时为 4..6。
在 revision 7 冻结、四笔暂停 KV、两条评论、两个 UUID 绑定后，revision 应为 15；
reserved 的首次写入后应为 16。这个数值只是上述内容证明全部成立时的算术结果。
不能通过选择一个计数来迁就当前 revision，也不能修改 request 内的 R0。

## 6. reservation 后的逐步恢复

保留原六个语义状态以及 `parent_projection_digest`，不增加独立本地日志。
写 reservation 时，其摘要必须绑定这笔 reservation 变更**之后**的预期父投影：
摘要排除 reservation 的值本身，但 revision 计入此次实际变更，避免自引用和落后一版。
写后必须读回相同值与预期投影，不能用读回值重新生成一个“正确”摘要覆盖冲突。

两个 checkpoint 之间只允许该状态定义的固定写入顺序及其前缀。恢复先确认前缀
内容，再逆向还原对应字段与精确 revision 差额，核对已有 checkpoint；全部一致才补
剩余写入。不接受任意键子集、不推断未知旧值、不忽略摘要不符、不跳状态。
checkpoint 自身写成功但丢 ACK 时，用写后状态验证；未发生时保留写前状态。
无法唯一解释是哪一种情况就停止，不能重复创建或重写授权。

子任务创建丢 ACK 仍要求 parent/stage/project/assignee/request/action/body digest
唯一匹配。子任务 metadata 与证据更新推进的是子任务 revision，不能计为父任务写入。
child 的 revision、运行及完成证据要在自己的专用完成流程逐项校验，不能用父摘要豁免。

状态更新选择显式保留 position 的已验证写入方式，避免后台自动排序产生无法复原
的旧值。父状态更新必须 no-start；child 的启动是最后单独的一步，不能由中间写入
隐式触发。status_category 必须按已确认的 workspace 状态映射变化。部署契约未证明
这些副作用时禁止启用写入口，不能猜测或把 position/status_category 从摘要删掉。

发布与采纳仍要求预先登记唯一 target；Git 快进不能当作 Multica revision 的增量。
后续恢复按已登记 target 和固定采纳前缀解释新候选，不把 source 换成“当前最新”。
adoption/consumed 完整且 reservation 清除后，旧基线仅用于历史请求证明，不能继续
要求 Stage 3 及正常门禁的父字段/评论都保持冻结状态。旧 grant、原证据和永久凭证的
身份仍须验证，后续不能再用冻结基线重复执行一次 refresh。

## 7. 大小与部署契约

官方元数据接口约束每个 Issue 最多 50 个键、整张 map 有 8KB 限制，见
[metadata handler](https://github.com/multica-ai/multica/blob/v0.4.38/server/internal/handler/issue_metadata.go)。
本试点加保守预算：request envelope 的 UTF-8 canonical JSON 不超过 3072 字节；
每个未来持久状态的整张 metadata map，用 ASCII 转义、带分隔空格的 JSON 序列化
计算，不超过 6144 字节且不超过 50 键。包含非 Eventra 键、receipt 共存时的峰值、
字符串二次转义；不能只测 request 长度。刷新使用的 Issue identifier 限 64 字符。

预算是本地拒绝策略，不宣称等同 PostgreSQL 存储计量。先为完整生命周期验证最坏
占用，再允许第一笔写入；每一步仍检查实际 map 并接受服务端最终拒绝。未知字段长度
没有可信上界时停止，不截断、不自动删除用户元数据、不把额外状态移到本地缓存。
request/grant 只存两个基线摘要，不把整份任务正文或评论历史塞进 KV。

已安装 CLI 的版本不证明 pro-1 服务端版本。Task 8 的批准部署记录需明确绑定
目标 workspace、control SHA 与已验证的 mutation contract，包括评论/metadata/status/
child 创建与启动的副作用。缺乏证明时仅开放只读计划，不启用写入。契约验证不得借用
PRO-122/123 做未经授权的试写；需要测试环境时另行批准。

## 8. 实现衔接与验收

书面补充获准后，先在原计划 Task 3/4 与 Task 5 之间补基线契约的 TDD 步骤；
继续当前会话顺序执行，不重新询问并行方式。修改范围仍为 candidate_refresh、
refresh_executor、workflow 及对应 tests / 角色说明，不改业务代码或 canonical knowledge。

验收至少覆盖以下独立行为，不把合成字典的任意数值当作已验证服务端契约：

- 全部合法步骤按真实评论和 KV revision 语义推进；重复同值写不额外计数。
- request 摘要绑定两份基线，缺失/伪造/非规范/旧版请求零写入拒绝。
- 同样的 revision 差额但不同任务内容、评论集合或线程不能通过。
- request/grant UUID 互换、重复、基线中已有、被编辑、错作者、缺分页均拒绝。
- 每笔写前、写后、checkpoint 更新和读回失败均注入故障；只恢复唯一合法前缀。
- extra comment、删除后重建评论、外部字段改回原值后的额外 revision 均停止。
- source 证据不变、唯一 refresh child、启动不重复、未知 run 阻断。
- 初始和峰值 metadata 预算、Unicode/转义、已有无关键均参与容量检查。
- 状态排序副作用、缺失部署契约、普通 v2 回归、采纳后正常门禁评论均有覆盖。

本文件的自检不是独立 Review/QA，也不代表 Tasks 5/6 或线上知识闭环已完成。
后续仍按原计划完成全部本地验证、独立审阅、控制面发布及新的 live 授权。

## 9. 本次设计上下文

代码基线 `b9d259cdb65ef72173038cfa271903229efbfcce`，分支
`codex/eventra-candidate-refresh`。本轮只修改设计与计划状态记录。
知识索引校验通过（12 项）；边界与关键约束经 AGENTS、package.json、API 默认值、
Smoke 脚本及原交付契约核对。不运行 Smoke，不自动提升 canonical knowledge。

文档自检核对了基线无自引用、前缀顺序、revision 算术、采纳后的适用范围及部署边界；
`git diff --check` 和本节 Context Receipt JSON 校验通过。文档修改后执行
`python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'`：exit 0，
584 项测试，17.986 秒。它只证明现有代码回归未坏，不证明本补充已实现。

```json
{"candidate_shas":{"frontend":"b9d259cdb65ef72173038cfa271903229efbfcce"},"conflicts":[],"knowledge_digests":{"eventra-dependency-graph":"ff17272e7abd2dac2b2abf94c63f50deb9f3a61c8f9228782dfb7f5f3fc16121","eventra-system-map":"4d5804dde75cdbc11558d315444baf4ebbe5956903cdf2048721376818caf903","frontend-invariants":"48f430289212e9e320701dd67d763a9ca98dca5aeb9e54dc65da6aa0f9858051","frontend-repository-map":"b8012180e7ceeab8b1cd865d07fedccb0b5d03a00cba8234876f0cba2ac9394f"},"knowledge_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-repository-map"],"match_reasons":{"eventra-dependency-graph":"repository+task_type+path","eventra-system-map":"repository+task_type+path","frontend-invariants":"repository+task_type+path","frontend-repository-map":"repository+task_type+path"},"repository":"frontend","schema_version":1,"task_id":"PRO-122-refresh-protocol-addendum","task_type":"planning","verified_ids":["eventra-dependency-graph","eventra-system-map","frontend-invariants","frontend-repository-map"]}
```
