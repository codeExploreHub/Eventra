# Eventra Controlled Candidate Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Eventra 试点中安全接入已合并前置版本，保留原实现证据，以独立刷新阶段产生新候选并重新走质量门。

**Architecture:** 纯协议与状态转换放在独立模块；Git 和 Multica I/O 分离且可注入。Engineer 只准备与测试暂存候选，Lead 登记后发布并采纳；durable reservation 解释唯一允许的 head 变化窗口。既有 v2 任务默认行为不变，刷新任务始终保留 merge hold。

**Tech Stack:** Python 3 标准库 / unittest、Git、现有 Multica CLI 与 GitHub CLI、Next.js Webpack；不新增第三方依赖。

**Spec:** `docs/superpowers/specs/2026-09-04-eventra-candidate-refresh-design.md`（用户已确认）。

## Global Constraints

- “version-2、`frontend-only`、attempt=0 的 Eventra 父任务。”
- “唯一 Stage 1 implementation 已 done/pass；next_stage=2；尚无后续子任务。”
- “一个父任务最多一次刷新；不支持刷新后再次刷新、跨仓刷新、评审中插入刷新。”
- “仅自动无冲突的 merge：不得夹带业务修复、手动冲突解决或新知识正文。”
- “原 Stage 1 不变 → 唯一 Stage 2 refresh → target 被采纳 → Stage 3 两个新门禁。”
- “attempt=0；旧 PASS 不转移”；“不使用 force、force-with-lease 或重写历史”。
- “consumed/adoption receipt 永久保留，reservation 最后清除。”
- “不假设 Multica KV 写入具备 CAS/事务。” 单一 Lead；本机锁不是分布式锁。
- “runtime workspace 保持启动时的 branch/commit，不执行 detach/reset/clean；它不是被测候选。”
- “不修改前后端业务代码、知识正文/种子摘要、通用 skill 或 PRO-116/120/121 历史。”
- 本计划的完成只到独立控制面变更经测试并交付审阅；任何 live grant、角色配置修改、PR #14 更新或合并、部署均另行批准。
- 不运行生产端点、历史 Smoke 或真实重试；代码测试使用临时本地仓库与严格假 API。
- 不修改或提交用户未跟踪文件 `docs/multica/eventra-multica-automation-overview.md`。

---

## 执行基线与文件边界

计划依据本地 `6b8249a53404d3267b282fa7c1ed8ba7d76d3afe`，其父基线是已合并 PR #15 的 `d7af5a5c12e006024d3d71a7d3faf210c1a96df5`。以包含本计划的最终提交开始工作；执行时重读 Git 状态，不将本文 SHA 当作永久最新版本。

PR #14 的 `runtime_guard.py` **不在本计划基线中**。不得导入未合并 PR 的文件，或先合并 PR #14 来让刷新工具可用。刷新锁放在自身执行模块，未来整合才另行去重。

| 文件 | 责任 |
|---|---|
| 新 `tools/multica/candidate_refresh.py` | 严格请求、grant、prepared evidence、reservation/receipt 模型与纯状态转换 |
| 新 `tools/multica/refresh_git.py` | 固定 Git 策略、真实对象验证、暂存 ref 与正常快进发布 |
| 新 `tools/multica/refresh_executor.py` | 严格 I/O 适配、单写者锁、登记/初始化/采纳的可恢复执行 |
| `tools/multica/workflow.py` | 类型与快照、planner、完成入口、Watcher、其他 executor 的互斥、merge hold |
| 新 `tools/multica/tests/test_candidate_refresh.py` | 纯协议与状态机测试 |
| 新 `tools/multica/tests/test_refresh_git.py` | 真实临时 Git 仓库测试 |
| 新 `tools/multica/tests/test_refresh_executor.py` | 严格 fake I/O、故障注入、零副作用负例 |
| `tools/multica/tests/test_workflow.py` | 既有 planner、完成、恢复、阶段历史的兼容回归 |
| `tools/multica/tests/test_operator_docs.py` | 角色指令与 CLI 契约回归 |
| 四个角色指令、README、`docs/multica/pilot-issues.md` | 准备/发布职责分离、工作区分离、上线与停止边界 |

模块依赖为 `workflow → refresh_executor → candidate_refresh / refresh_git`；底层不反向 import workflow。workflow 现有快照先转换成刷新专用规范快照再调用纯函数，避免循环依赖。`eventra_adapter.py` 仅在其实际渲染 handoff 需要刷新分支时局部修改，并增加现有 adapter 测试；不重构通用 manifest。

下列任务 Files 中的模块、`tests/`、`instructions/` 和 README 简写均相对
`tools/multica/`；pilot runbook 唯一指 `docs/multica/pilot-issues.md`。

### 编码细化：消除摘要自引用

请求 payload 中绑定固定 `staging_ref_prefix="refs/heads/eventra-refresh/"`；先对 payload 求摘要，再派生完整 ref。外层 envelope 保存 `{payload,digest,staging_ref}`，解析时必须重新推导并匹配。不能让 request_digest 的计算包含带 request_digest 的完整 ref。该细化保持设计中的确定性 ref 与授权边界不变。

### 每个任务共同执行的基线步骤

- [ ] 执行时使用 using-git-worktrees 检查已有隔离；需要创建时先遵守该技能的同意与工具选择规则。不得占用运行器保留的 PRO-126 工作区。
- [ ] 读取 AGENTS、设计和计划；检查已跟踪/未跟踪改动。只 stage 当前任务的明确文件。
- [ ] 生成 full-SHA Context Receipt。先执行 `git rev-parse HEAD` 获取完整值，再传给 `--sha frontend=...`；短 SHA 被知识契约拒绝。
- [ ] 运行基线 Python 测试，记录实际数量及失败；任何已有失败先报告，不宣称基线通过。

```bash
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'
python3 -B -m tools.multica.knowledge verify --frontend-root . --backend-root /Users/didi/Eventra-workspace/Eventra-Backend
```

不把上述本机 Backend 路径带入新测试夹具。新测试的仓库全部来自 TemporaryDirectory；既有主机依赖保留为明确限制。

## Task 1: 固定请求、摘要和证据协议

**执行状态（2026-09-04）：本地实现与回归完成。** 提交 `ebb0ac63e7b7bc15cc8a4f67164b34d57321c14c`；16 项协议测试先因缺接口 RED，再 GREEN。下列条目保留原计划；实测执行细节见同目录 `2026-09-04-eventra-candidate-refresh-batch-1.md`。

**Files:** Create `candidate_refresh.py`, `tests/test_candidate_refresh.py`。

**Interfaces:** 均在 `tools.multica.candidate_refresh` 中定义：

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class RefreshRequest:
    canonical_payload: str
    digest: str
    staging_ref: str

    def payload(self) -> dict[str, Any]:
        import json
        return json.loads(self.canonical_payload)

@dataclass(frozen=True)
class RefreshComment:
    issue_id: str
    comment_uuid: str
    author_id: str
    author_type: str
    revision: int
    content: str

@dataclass(frozen=True)
class PreparedCandidate:
    request_digest: str
    child_id: str
    source_sha: str
    prerequisite_sha: str
    target_sha: str
    tree_sha: str
    evidence_uuid: str
    evidence_digest: str
    staging_ref: str
```

产生 `canonical_json(value: object) -> str`、`parse_request(raw: object) -> RefreshRequest`、`build_request(payload: dict[str, object]) -> RefreshRequest`、`validate_grant(comment: RefreshComment, request: RefreshRequest) -> None`、`parse_prepared(comment: RefreshComment, request: RefreshRequest, child_id: str) -> PreparedCandidate`。

- [ ] 增加 `RequestTests`，首个失败测试直接使用下面的完整 payload 工厂（它只创建测试身份，不访问 live）：

```python
def request_payload():
    return {
        "schema_version": 1,
        "workspace_id": "00000000-0000-4000-8000-000000000001",
        "parent": {"id": "00000000-0000-4000-8000-000000000002",
                   "identifier": "PRO-900", "revision": 7,
                   "stage": 1, "attempt": 0, "next_stage": 2,
                   "status": "blocked", "merge_state": "not_ready",
                   "last_action": "2:PRO-900:create_implementation_stage:0:frontend:"
                                  + "a" * 40 + ":-:next-stage:1"},
        "source": {"child_id": "00000000-0000-4000-8000-000000000003",
                   "child_identifier": "PRO-901", "sha": "b" * 40,
                   "evidence_uuid": "00000000-0000-4000-8000-000000000004",
                   "evidence_revision": 1, "evidence_digest": "c" * 64},
        "pr": {"url": "https://github.com/codeExploreHub/Eventra/pull/90",
               "repository": "codeExploreHub/Eventra",
               "head_ref": "agent/eventra-frontend-engineer/pro-901",
               "base_ref": "master"},
        "prerequisite": {"pr_url": "https://github.com/codeExploreHub/Eventra/pull/91",
                         "merge_sha": "d" * 40, "base_sha": "d" * 40},
        "assignment": {"project_id": "00000000-0000-4000-8000-000000000005",
                       "squad_id": "00000000-0000-4000-8000-000000000006",
                       "lead_id": "00000000-0000-4000-8000-000000000007",
                       "engineer_id": "00000000-0000-4000-8000-000000000008"},
        "refresh_stage": 2, "refresh_generation": 1,
        "staging_ref_prefix": "refs/heads/eventra-refresh/",
        "tree_transform": "clean-two-parent-merge-v1",
        "merge_permission": "hold", "control_tool_sha": "e" * 40,
        "git_version": "git version 2.50.1",
    }

class RequestTests(unittest.TestCase):
    def test_ref_is_derived_without_digest_recursion(self):
        request = build_request(request_payload())
        self.assertEqual(request.staging_ref,
                         "refs/heads/eventra-refresh/" + request.digest)
        self.assertEqual(parse_request({"payload": request.payload(),
                                       "digest": request.digest,
                                       "staging_ref": request.staging_ref}), request)
```

- [ ] Run `python3 -B -m unittest tools.multica.tests.test_candidate_refresh.RequestTests -v`，确认因新接口不存在而 RED。
- [ ] 实现规范编码 `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` 和 SHA256；所有层级精确 key 集合；JSON 文本拒绝重复 key；严格 `type(value) is int`，拒绝 bool、null、NaN、超长 payload（16 KiB）、错误 UUID/40 位 SHA/64 位 digest、非个人 fork URL、额外路径/ref 注入。`canonical_payload` 保证冻结对象不含可变 dict。

```python
def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)

# build_request 中先完成上述逐层验证，再执行以下构造；不是验证的替代品。
encoded = canonical_json(payload)
digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
request = RefreshRequest(encoded, digest,
                         "refs/heads/eventra-refresh/" + digest)
```
- [ ] grant 正文固定为一个 `eventra-candidate-refresh-grant-v1` fenced block，JSON 字段严格 `{schema_version:1,request_digest:...,granted_refresh:1}`，不接受混合文字或多个 block。comment 必须来自 parent UUID、member、revision=1；已消费检查由 Task 3 的快照执行。prepared 正文允许普通说明，但恰好一个 `eventra-candidate-refresh-prepared-v1` block，作者必须是 Engineer、issue 必须是新 child、revision=1。
- [ ] prepared block 固定字段：schema_version/request_digest/child_id/source_sha/prerequisite_sha/target_sha/tree_sha/staging_ref/control_tool_sha/git_version/context_receipt/commands。commands 为固定必需检查名到 `{argv:[str],exit_code:0}` 的映射，context_receipt 复用知识契约；缺检查或非零 exit 拒绝 PASS。证据摘要对完整原评论正文 UTF-8 求 SHA256。没有 prepared 的 FAIL/BLOCKED 使用 Task 6 的 outcome 封套。
- [ ] 增加参数化负例，分别更改每个身份、每层新增字段、修改正文/revision、多个 block、错作者；验证拒绝。Run 同模块全部测试 GREEN。
- [ ] Commit 明确的两个文件：`feat(multica): define candidate refresh contracts`。

## Task 2: 真实 Git 合并对象与发布边界

**执行状态（2026-09-04）：本地实现与回归完成。** 提交 `3f7d571a14af1e225593c04335100bba2a2e86fa`；16 项真实 Git 测试先因缺接口 RED，再 GREEN。尚未接入 live 执行入口；Task 3–9 待完成。

**Files:** Create `refresh_git.py`, `tests/test_refresh_git.py`。

**Interfaces:** `RefreshGit(repository_dir: pathlib.Path)`；方法
`expected_tree(source: str, prerequisite: str, git_version: str) -> str`、
`verify_candidate(request: RefreshRequest, target_sha: str) -> str`（返回 tree SHA）、
`read_ref(ref: str) -> str | None`、
`publish_staging(request: RefreshRequest, target_sha: str) -> bool`、
`publish_candidate(request: RefreshRequest, prepared: PreparedCandidate) -> bool`。
bool 表示远端本次是否发生变化，不代替 executor 的持久凭证。

- [ ] 写 `GitTests`，用 TemporaryDirectory 建 bare origin 与两个 clone，创建共同祖先及两个分别改不同文件的分支；用 `subprocess.run([...], check=True, capture_output=True, text=True)` 调 Git，不使用 shell 字符串。测试产物必须有不同真实 SHA。

```python
def test_reversed_parents_are_rejected(self):
    # self.repo/self.request/self.source/self.prerequisite 由真实仓库 setUp 创建。
    tree = self.git.expected_tree(self.source, self.prerequisite, self.version)
    swapped = self.run_git("commit-tree", tree, "-p", self.prerequisite,
                          "-p", self.source, "-m", "wrong order")
    with self.assertRaisesRegex(RuntimeError, "parents"):
        self.git.verify_candidate(self.request, swapped)
```

- [ ] Run `python3 -B -m unittest tools.multica.tests.test_refresh_git -v`，记录缺接口 RED。
- [ ] `expected_tree` 固定执行 `git --no-replace-objects merge-tree --write-tree SOURCE PREREQUISITE`；非零 exit 即冲突/失败，不从 stdout 猜成功；读取 `git version` 必须完全匹配 request。两输入必须是真实 commit，祖先关系不得是 source 已包含 prerequisite 的无意义刷新。
- [ ] 禁用系统/全局 Git 配置、hooks、attributes、外部 merge drivers 的不确定影响；验证端在自己拥有的隔离 object checkout 中重算，设置 `GIT_CONFIG_NOSYSTEM=1`、空 global 配置和空 hooks/attributes 文件。仓库 `.gitattributes` 的自定义 merge driver 无可信定义则拒绝。不得改用户全局配置；对象 shallow/缺失则补安全 fetch 或拒绝，不接受不完整祖先结论。
- [ ] `verify_candidate` 从明确同一 fork fetch target 后，用 raw commit 获取父顺序、tree 并与 expected_tree 比较。禁止从调用者返回的 `verified=true` 取信；拒绝 replace refs、额外父、缺父、任意额外内容、不可解析对象、git 版本漂移。
- [ ] 发布限定为 request 已验证的 fork URL/ref。先 read-ref；staging 只接受不存在或等于 target；原分支只接受 source 或 target。使用正常 `git push ORIGIN TARGET:REF`，绝不加 `+`/force/delete。返回异常后重新 read-ref：等于 target 才视为已发生。不同 SHA 即拒绝，不再次猜测推送。
- [ ] 增加裸仓库测试：正常 merge、冲突、dirty worktree 不影响 verifier、错误树、版本错、ref 注入、暂存冲突、原 ref 被并发更新、push 成功 ACK 丢失；录制 argv 确认无危险参数。Run GREEN。
- [ ] Commit 两文件：`feat(multica): verify and publish exact refresh merge objects`。

## Task 3: 读取完整 authority 与精确请求预检

**Files:** Create `refresh_executor.py`, `tests/test_refresh_executor.py`；Modify `candidate_refresh.py`。

**Interfaces:** 在 candidate_refresh 定义
`RefreshSnapshot(canonical_state: str)`，`state() -> dict[str, object]` 返回副本；
`admit_refresh(request: RefreshRequest, snapshot: RefreshSnapshot, grant: RefreshComment) -> None`。
在 refresh_executor 定义 `load_refresh_snapshot(runner: MulticaRunner, github: GitHubRunner, parent: str) -> RefreshSnapshot`，仅调用只读 API；`GitHubRunner` 是现有 workflow 类型，通过注入参数使用，不在底层 import workflow。

快照顶层精确包含：parent、metadata、children、runs、comments、pr、prerequisite、assignment、tool。parent 保留 id/identifier/status/revision/project_id/assignee_type/assignee_id；children 包含每个 child 的原 detail/metadata/证据身份与正文 digest；runs 保留子任务活动身份及 Lead 唯一执行身份。tool 由操作侧已批准固定 checkout 的实际 HEAD 和 Git 版本读取，不从请求字段反填。

- [ ] 写 `SnapshotTests`：原 `parse_authorizing_comment` 丢 revision/author_id，原 evidence parser 只取身份，不能作为新封套的全部信任依据。使用测试内显式 dict 响应，缺 revision 的 grant 或修改后的正文均必须拒绝。

```python
def test_no_api_revision_is_not_assumed_immutable(self):
    response = {"id": "00000000-0000-4000-8000-000000000010",
                "author_type": "member", "author_id": "member-1",
                "type": "comment", "content": "grant"}
    with self.assertRaisesRegex(RuntimeError, "revision"):
        self.adapter.parse_scoped_comment([response], response["id"], self.parent_id)
```

`RefreshAPI.parse_scoped_comment(records, comment_uuid, scoped_issue_id) -> RefreshComment` 在本任务定义：scoped_issue_id 来自实际请求路由；若响应也含 issue_id，必须相同。接受已观察 API 的辅助 timestamp/reply_count，不把任意无来源的 caller dict 当读取结果。未知必要语义字段或分页不完整则拒绝。

- [ ] Run `python3 -B -m unittest tools.multica.tests.test_refresh_executor.SnapshotTests -v` 记录 RED。
- [ ] 定义 `RefreshAPI(runner, github, control_root)`，只读方法 `snapshot(parent)`, `comment(issue, uuid)`；通过 runner 的 exact parent/child read 证明关系，完整分页/线程读取，验证现有蓝图的 Project/Squad/角色映射。新增纯解析器放本模块，不改变旧 evidence parser 的返回结构。
- [ ] `admit_refresh` 对照设计每项入口条件逐一拒绝：非 Eventra/frontend-only/v2，非 Stage1唯一完成，源证据或 PR 身份不等，无 grant，已 consumed/generation，repair/smoke/refresh reservation，后续 child，活动 child，非唯一 Lead；前置必须已 merged 且是当前 base 历史中的 commit，base tip 与冻结值相同。
- [ ] 双读请求前后 authority。起始 parent revision 必须相同才冻结；grant 发布后如果 API 推动 parent revision，只允许适配器依据已观察的、仅评论活动变化的字段差异证明同一业务 authority。无可验证依据时拒绝并重新冻结，禁止 `revision >= old` 代替验证。自写入推进在 Task 5 用确定性的 expected projection 处理。
- [ ] 测试所有入口负例和跨 scope grant；记录 reads-only fake 的 writes 始终为空。Run 模块 GREEN；Commit：`feat(multica): bind refresh admission to authoritative snapshots`。

## Task 4: 纯状态机和旧 workflow 的防护接入

**Files:** Modify `candidate_refresh.py`, `workflow.py`, `tests/test_candidate_refresh.py`, `tests/test_workflow.py`。

**Interfaces:** 定义 `RefreshDecision(kind: str, action_key: str | None, reason: str)`；
`plan_refresh(request: RefreshRequest, snapshot: RefreshSnapshot) -> RefreshDecision`。
kind 限于 create_refresh_stage/resume_refresh/publish_refresh/create_gate_stage/wait/block。
action_key 为 `2:PARENT:create_refresh_stage:0:frontend:SOURCE:next-stage:2:refresh:1:DIGEST`；后续恢复保留该身份。

持久键固定：`eventra.refresh.version`, `.request_comment`, `.request_digest`, `.authorization_comment`, `.reservation`, `.consumed`, `.adoption`, `.merge_permission`；generation 在 request/receipts 中固定 1。初始配置只能由新 executor 写；每个 key 的值都是字符串，复杂值为 canonical JSON。

reservation 精确字段：version/request_digest/authorization_uuid/action_key/state/child_id/child_identifier/prepared/parent_projection_digest；prepared 在登记前为 null，登记后为 Task 1 PreparedCandidate 的完整固定字段。consumed 为 version/request_digest/authorization_uuid/child_id/target_sha；adoption 再绑定 source_sha/prerequisite_sha/evidence_uuid/evidence_digest/stage/control_tool_sha。

- [ ] 先在 `ParentDecisionTests` 加“存在有效刷新请求仍直接进入旧 gate”的 RED 用例，以及 legacy 不带 refresh keys 完全维持原决策的对照。新 snapshot 用专用 `refresh_state: RefreshSnapshot | None` 字段，PhaseSnapshot 新增 `refresh_provenance: str | None`，不挪用 repair 字段。

```python
def test_unadopted_refresh_cannot_open_gate(self):
    state = self.refresh_snapshot(state="candidate_registered", adopted=False)
    decision = plan_refresh(self.request, state)
    self.assertNotEqual(decision.kind, "create_gate_stage")
    self.assertEqual(decision.kind, "publish_refresh")
```

本任务在测试类定义 `refresh_snapshot(state, adopted)` 工厂：从 Task 3 的完整合法 snapshot 派生，仅修改 reservation 状态及 source/target/receipt 组合，不默默补非法字段。

- [ ] Run `python3 -B -m unittest tools.multica.tests.test_candidate_refresh tools.multica.tests.test_workflow -v`，确认新路径 RED、旧对照保持通过。
- [ ] 在 `_parent_metadata`/`_phase_snapshot`/`load_parent_snapshot` 加严格新字段读取，刷新 kind 只有 feature marker 与完整 provenance 才合法；`PhaseCompletion` 的普通 `finish-phase` 显式拒绝 refresh，专用完成入口由 Task 6 实现。
- [ ] 在 `decide_parent_action` 的普通 Stage/head 校验之前处理已验证刷新分支；未登记 target 不豁免任何 head drift。adoption 完整且 reservation 已清除时，验证旧 Stage1+新 Stage2身份和新候选后返回正常 create_gate_stage，交给既有 Lead 创建 Stage3。
- [ ] 刷新 FAIL/BLOCKED 返回 block_parent，不调用 `_repair_or_block`。`_attempt_history_is_consistent` 不把 refresh 算 repair，但验证至多一次且 Stage2/attempt0。新 Gate 后真实失败继续原 repair，历史中 refresh 的 adoption 仍须可验证。
- [ ] merge_permission=hold 在 merge-ready 分支返回稳定 noop/reason=human merge approval required，而不是 merge；在 `finish_parent`、Smoke executor 等旁路同样拒绝未经确认的后续变更。v1 本计划不实现解锁 API，避免把 metadata 手改成 allow 当成员批准。
- [ ] Run GREEN；Commit：`feat(multica): model refresh stages without replacing historical evidence`。

## Task 5: 建立并恢复唯一刷新阶段

**Files:** Modify `refresh_executor.py`, `candidate_refresh.py`, `workflow.py`, `tests/test_refresh_executor.py`。

**Interfaces:** `RefreshExecutionResult(action_key: str, status: str, mutation_count: int, child_identifier: str)`；
`execute_refresh(api: RefreshAPI, git: RefreshGit, parent: str, request_uuid: str, grant_uuid: str, expected_action_key: str) -> RefreshExecutionResult`。
本任务实现 reserved/child_initialized/child_dispatched；后续 publish 路径 Task7 接上。

另定义 `stage_refresh_request(api: RefreshAPI, parent: str, request: RefreshRequest) -> RefreshExecutionResult`，仅用于显式获准的暂停登记：父任务保持 blocked，写入 version/merge_permission=hold/request_digest 与规范 request envelope，不创建 child、消费 grant 或更新 PR。新增持久键 `eventra.refresh.request` 保存 envelope；这是待授权意图，不是 reservation。写入前提必须验证 source/assignment/Stage1 状态及无其他运行；缺 grant 只允许此暂停登记，不允许执行刷新。Core 对完整等待授权意图返回 wait，对登记半途字段组合保持 fail-closed，不能回退旧 gate 路径。

新增 API 写方法：`set_metadata(issue,key,value)`, `set_status(issue,status)`,
`create_child(parent,stage,title,project_id,assignee_id,description) -> str`。
它们只封装已支持 Multica CLI，无隐式重试；每次外部写都由 executor 明确读回。

- [ ] 定义严格 `MemoryRefreshAPI` 测试替身（仅在 test_refresh_executor）：字典保存 parent/child metadata、comments、run；`writes` 记录 `(operation,args)`；`fail_at: int | None` 和 `fail_after: bool` 控制第 N 次写前/写后抛 RuntimeError；写后失败保留真实效果；snapshot 返回深拷贝且读取未知 ID 拒绝。

```python
def mutate(self, operation, args, apply):
    self.write_index += 1
    if self.fail_at == self.write_index and not self.fail_after:
        raise RuntimeError("injected before effect")
    apply()
    self.writes.append((operation, args))
    if self.fail_at == self.write_index and self.fail_after:
        raise RuntimeError("injected after effect")
```

- [ ] 首测 `test_duplicate_create_ack_loss_recovers_same_child`：create 成功后抛错，第二次执行必须找到原 child 且 create 总数=1；旧 source evidence JSON 与原值完全相等。Run `python3 -B -m unittest tools.multica.tests.test_refresh_executor -v` 记录 RED。
- [ ] 执行本机锁用 `fcntl.flock(LOCK_EX|LOCK_NB)`，锁文件放 Git common-dir 的刷新专用目录，key 为 workspace+parent 的摘要；锁覆盖整个外部写序列，不依赖未合并 runtime_guard。不同 action 同 parent 也互斥。锁描述符随进程退出释放；不可由清理文件伪造 stale 解锁。
- [ ] 先测试暂停登记和缺 grant 时的唤醒：Core/Watcher 不能创建普通 gate 或恢复普通 Lead 动作。暂停登记各写入前缀只能由相同 request 恢复；不同 digest 不可覆盖。request/grant 评论 UUID 后续由实际读回绑定，不能预先猜测。
- [ ] 先校验精确 request/grant 与单一 Lead，再写并读回 reserved；初始化 feature/request/grant/hold 字段仅允许已知写入前缀。无活动 child 前提只用于首次入口，恢复时改为检查恰好一个当前 child/允许的 run，而非拒绝自身已创建的 child。
- [ ] child 必须 `--status backlog --stage 2` 并赋 Engineer/Frontend Project；写 kind=refresh、attempt=0、target=repository:frontend、role=frontend_engineer、creation_action、source SHA、managed PR 与 request digest。每个字段读回；未知额外 phase/refresh 字段阻断。完整初始化后写父 next_stage=3/last_action 和 in_progress，最后才启动 child。
- [ ] 恢复时不能使用“仅标题相同”的 child。按 parent/stage/project/assignee/request/action/body digest/初始化字段前缀比对；重复或未知活跃 run 阻断。写入步骤维护上次已读回 authority projection；状态预期变化之外的 metadata、assignment、PR 或 evidence 漂移均终止。
- [ ] 增加循环故障注入，覆盖每一写入前后；写后读回失败时保持 reservation，第二次只补合法前缀。测试不得改原 implementation metadata/evidence，不能提前登记 consumption。
- [ ] Run GREEN；Commit：`feat(multica): initialize refresh stages with durable recovery`。

## Task 6: Engineer 准备与专用完成证据

**Files:** Modify `candidate_refresh.py`, `refresh_executor.py`, `workflow.py`, `tests/test_refresh_executor.py`, `instructions/frontend_engineer.md`。

**Interfaces:** `finish_refresh(api: RefreshAPI, git: RefreshGit, child: str, evidence_uuid: str, result: str) -> RefreshExecutionResult`，result=pass/fail/blocked。
失败封套 `eventra-candidate-refresh-outcome-v1` 固定 schema_version/request_digest/child_id/source_sha/prerequisite_sha/result/commands/reason，不允许 target 或 PASS；作者和 comment 身份检查与 prepared 相同。

- [ ] 新测：准备 PASS 在原 PR head 仍为 source、staging ref 为 target 时允许完成；若误用现有 finish-phase 或提交普通 QA PASS 必须失败。

```python
def test_prepared_pass_does_not_publish_managed_pr(self):
    before = self.api.managed_head
    finish_refresh(self.api, self.git, "PRO-902", self.prepared_uuid, "pass")
    self.assertEqual(self.api.managed_head, before)
    self.assertEqual(self.api.child_metadata["eventra.phase.kind"], "refresh")
    self.assertEqual(self.api.child_status, "done")
    self.assertNotIn("eventra.refresh.adoption", self.api.parent_metadata)
```

- [ ] Run `python3 -B -m unittest tools.multica.tests.test_refresh_executor -v` RED。
- [ ] 指令给出真实操作顺序：专属命名 integration worktree 基于 source；禁用不受信任配置；`git merge --no-ff --no-commit PREREQUISITE`；先确认无冲突与 expected tree、再 commit（两父顺序如设计）；任何手改内容拒绝。commit 后 exact SHA 上重跑必需检查，读取知识与生成 receipt，再推唯一 staging ref。
- [ ] 必需检查名固定 python/local_contract/footer/hydration/dashboard/lint/build/knowledge；argv 对应 Task9 的真实命令，knowledge/context 的显式 repo 路径允许 task-owned roots。测试数量记录实际值不硬编码 496。Context Receipt 必须 task_id=child、candidate frontend=target，material verified_ids 由 agent 实际核验，解析器不能自动填入。
- [ ] prepared 正文只证明准备和测试。`finish_refresh` 重新验证 request、child assignment、grant、reservation、staging SHA/Git tree、evidence 内容；PASS 只改当前 refresh 的完成字段并 done，不改 parent candidate/PR。FAIL/BLOCKED 保留 source SHA 和原 PR，合法结果写 done/non-PASS，父任务后续阻断，不强造 target。
- [ ] 普通 phase 完成的旧终态冲突保护保持；refresh 同一 immutable evidence 重放为 noop，换证据/换 SHA 终态重放拒绝。登记前 source 漂移、暂存不存在、错作者、缺命令、被改评论全部拒绝且 writes=0。
- [ ] Run GREEN；Commit：`feat(multica): record refresh preparation without publishing the candidate`。

## Task 7: 登记、发布与采纳恢复

**Files:** Modify `refresh_executor.py`, `candidate_refresh.py`, `tests/test_refresh_executor.py`, `tests/test_refresh_git.py`。

**Interfaces:** 复用 `execute_refresh(...)`，增加 child_dispatched 后的分支；`register_candidate(api, request, prepared) -> None` 和 `adopt_candidate(api, request, prepared) -> None` 是模块内函数，不对 agent 暴露绕过前置校验的 CLI。

- [ ] 首测 publication ACK 丢失后再次执行，只读回相同 target、采纳一次、child 总数仍=1；第二个测试在 adoption 半写时重启必须只补齐凭证而不再次 push。

```python
def test_publish_ack_loss_does_not_duplicate_delivery(self):
    self.git.fail_after_push = True
    self.execute_until_interruption()
    self.git.fail_after_push = False
    result = self.execute_again()
    self.assertEqual(result.status, "adopted")
    self.assertEqual(self.git.managed_push_effects, 1)
    self.assertEqual(self.api.child_create_effects, 1)
    self.assertEqual(self.api.parent_metadata["eventra.workflow.frontend_sha"],
                     self.prepared.target_sha)
    self.assertNotIn("eventra.refresh.reservation", self.api.parent_metadata)
```

测试替身本任务增加 managed_push_effects 与发布后抛错开关；`execute_until_interruption()` 调用 execute_refresh 并容许预期 RuntimeError；`execute_again()` 使用完全相同 request/grant/action。

- [ ] Run executor 模块 RED。
- [ ] 先重新验证 source历史、current PR/base、prepared身份与 full Git tree；把 prepared 全部字段写 reservation state=candidate_registered 并读回后才可 publish。未登记的任何 head=target 也按 drift 拒绝，不自动追认。
- [ ] 推送前再次检查 source/登记target、base tip 和 grant/证据；正常快进，随后读 GitHub PR 身份及 head。source 不变代表未发生可恢复；target 精确相同代表效果已发生；其他 SHA 冲突。不得只根据 git exit0 判定业务发布成功。
- [ ] 状态 published 后持久化 adoption、父 frontend SHA=target、merge_state=not_ready、consumed、hold，逐个读回；最后 state=adopted 后删除 reservation。初始化 last_action 和 next_stage=3 保留，planner 以 adoption 解释旧实现不再是最新候选。
- [ ] 每个边界的故障注入都比较原 child/evidence 深拷贝与最终状态；同时验证跨 parent/grant复用、不同 prepared、父候选半写非法组合、base 漂移、评论被编辑、重复 child 等情况绝不继续。完成后重放返回 adopted/noop，不再次消费。
- [ ] Run executor + Git 模块 GREEN；Commit：`feat(multica): publish and adopt refresh candidates idempotently`。

## Task 8: CLI、Watcher 互斥与精确工作区 handoff

**Files:** Modify `workflow.py`, `tests/test_workflow.py`, `tests/test_operator_docs.py`，四角色指令、README 和 pilot runbook；必要时 `eventra_adapter.py` / `tests/test_eventra_adapter.py` 的 handoff 分支。

**Interfaces:** 新 CLI 固定为以下形式（大写名是 shell 用法中的必填参数名，不是预填 live 值）：

```text
python3 -B -m tools.multica.workflow plan-refresh PARENT --prerequisite-pr URL --control-tool-sha SHA
python3 -B -m tools.multica.workflow stage-refresh-request PARENT --request-file FILE
python3 -B -m tools.multica.workflow execute-parent-refresh PARENT --request-comment UUID --authorization-comment UUID --expected-action-key KEY
python3 -B -m tools.multica.workflow finish-refresh CHILD --result pass|fail|blocked --evidence-comment UUID
```

plan-refresh 只读，输出 request envelope、需要人发布的规范 grant 文本，不发布评论或写 metadata。运行时 workspace/profile 从明确配置适配，不把默认 CLI profile 当 pro-1。control tool SHA 需与执行 checkout 及已批准部署记录一致；参数自身不构成批准。

stage-refresh-request 是单独获准的 live 写操作，不能因 plan-refresh 生成文件而自动调用；Task5 已定义实现。请求文件在严格解析并与 freshly loaded authority 比对后才能登记，不信任文件里的身份断言。request 评论使用恰好一个 `eventra-candidate-refresh-request-v1` block 包装 Task1 envelope；正文修订会使原 grant 失效。首次 executor 只接受已登记的同一 request 及实际 parent-scoped request/grant 评论；登记后的自身 metadata revision 推进必须有相符持久前缀，不能把原始冻结 revision 简单替换成当前值。

- [ ] 写 argparse tests：plan-refresh 必须 mutation_count=0，旧 finish-phase --kind refresh 必须拒绝，新的 finish-refresh 必须路由到 Task6；错误参数不能触发网络写。
- [ ] 写 Watcher/repair/smoke/finish-parent 测试：合法或 malformed refresh reservation 一律不进入其他写流程；合法只返回恢复提示，malformed 是 block。refresh done/pass 但未 adopted 不属于普通 gate-ready。
- [ ] Run `python3 -B -m unittest tools.multica.tests.test_workflow tools.multica.tests.test_operator_docs -v` RED。
- [ ] 接 CLI 与 Task3/5/6/7；按规范 JSON 输出结果。普通 planner 遇到 refresh等待时输出稳定原因，不制造高频重试。Watcher 配置不在本轮 live 修改，代码层保留 hold 即使无人运行新角色也不越权。
- [ ] 增加回归：暂停意图尚未获 grant、request 评论唤醒、grant 评论唤醒、stage 初始化中断、prepared 完成唤醒，分别只返回该状态允许的动作；不允许留言的副作用抢先创建 Stage2 gate。
- [ ] 将角色 handoff 模板明确分成 `runtime_workspace`、`inspection_workspace`、`candidate_sha`、`control_tool_sha`。QA/Reviewer 在 task-owned inspection worktree 执行 fetch+detach；runtime workspace branch/HEAD 全程不变。替换旧测试中“任何工作区均 detach”的断言，保留 exact SHA / FETCH_HEAD / 禁止清理用户改动的要求。

```python
def test_role_docs_separate_runtime_and_inspection(self):
    for role in ("integration_qa", "independent_reviewer"):
        text = (Path("tools/multica/instructions") / (role + ".md")).read_text()
        for required in ("runtime workspace", "inspection worktree",
                         "control_tool_sha", "FETCH_HEAD"):
            self.assertIn(required, text)
        self.assertIn("never detach the runtime workspace", text)
```

- [ ] 增加本地双 worktree contract test：一个保留命名 runtime baseline，另一个 detached target；inspection 测试与 helper 完成模拟后检查 runtime branch/HEAD 相等。该测试只证明文档/本地 Git 契约，不宣称模拟了完整 Multica runtime。
- [ ] 文档明确 Stage3 fresh gates、hold在repair后仍有效、成员合并授权独立、prepared PASS不是QA、独立控制面先发布后live。记录 Python后串行lint和既有夹具主机依赖。
- [ ] Run GREEN；Commit：`feat(multica): expose guarded refresh commands and isolated gate handoffs`。

## Task 9: 全链路验证与独立交付

**Files:** 测试文件中的端到端故障注入用例；README / pilot runbook 中实际验证结果。禁止用文档“通过”替代运行。

**Interfaces:** 组合前八任务已有入口；不新增权限、phase kind 或 API。

- [ ] 补一个真实临时 Git + MemoryRefreshAPI 的端到端 RED 测试：源 stage1 done/pass → 冻结grant → stage2准备 → publish/adopt → stage3 review+qa → 两门通过但 merge hold。测试必须断言从未写旧 stage1、从未修改远端 master、没有部署/Smoke 调用。
- [ ] 使用测试替身已记录的总写次数 N，逐个 `1..N` × before/after 注入中断，重试后与无中断基准结果比较；不硬编码少数“代表性”断点。外来不相容写不应收敛为成功，应零后续写地阻断。
- [ ] 跑全量安全回归，按此顺序串行执行（不启动服务）：

```bash
python3 -B -m unittest discover -s tools/multica/tests -p 'test_*.py'
npm run test:local-contract
npm run test:footer-meta
npm run test:layout-hydration
npm run test:dashboard-profile
npm run lint -- --ignore-pattern '.worktrees/**'
npm run build
git diff --check
```

- [ ] 新 inspection worktree 缺依赖时按 lockfile `npm ci`；安装脚本提示不能自动全部批准。不要复制私有 .env；如果构建受环境阻断，报告 blocked，而不是修改 builder 或宣称 PASS。
- [ ] 运行知识 verify 和 full-SHA Context Receipt；确认无 canonical知识正文改动。报告实际测试数、命令 exit、lint既有警告、主机限制；不借用 PRO-126 或 PR #14 旧结果。
- [ ] 做逐项 spec→测试审计：request/grant/target身份、所有reservation中断点、未知kind、旧流程、runtime工作区、hold、无历史Smoke；发现行为问题用新RED回归修复，不直接调整断言迎合实现。
- [ ] 最终代码 commit 后在 exact SHA 复核；独立 Reviewer 与 Integration QA 审核这个控制面变更，不是测试它自动刷新自己。按用户明确的工作方式交付独立 PR；若尚未获推送授权，留本地提交并请求授权，不自行推送。
- [ ] checkpoint：返回精确 SHA、变更范围、测试、残余风险，等待人的控制面 PR 合并/启用决定。不要继续执行下面的 live runbook。

## 上线后的独立 live runbook（本计划不执行）

1. 获人批准合并控制面 PR，并核验实际 merge/tool SHA；用固定工具 checkout 和经批准角色 handoff 启用，不用未审阅工作树。
2. 重新读取 PRO-122/123、PR #14、实际 master、前置 PR 与所有运行；前提有漂移则停止。
3. 冻结新的实际 prerequisite/base/tool 身份，渲染 request/grant，展示给人；先获准执行 stage-refresh-request 并读回等待授权状态，再发布请求/授权评论。成员发 grant 或明确批准代发，不能把本对话“符合”复制成运行时授权。发布期间需协调暂停同一 PR 分支的其他写入者；普通 fast-forward push 不提供服务器端 expected-old-SHA CAS，不宣称检测所有瞬时外部 ref 变化。
4. Lead 唯一执行刷新，保留原证据；新 Gate 都完成且 PASS 后仍停在 merge hold。
5. 人另行批准精确候选合并。没有该批准，不合并 PR #14、不进入 Smoke或部署；不手动删除 hold 字段绕过。

## 计划自检映射

| 设计要求 | 任务 |
|---|---|
| 单仓、一次性、Stage/attempt限制与源证据不变 | 1、3、4、9 |
| 成员精确授权、摘要、评论revision/作者、工具身份 | 1、3、8 |
| 无冲突双父纯merge、完整树、同fork正常push | 2、6、7 |
| reservation前缀、单写者、所有中断点/丢ACK | 5、7、9 |
| prepared PASS与发布/采纳/QA分开 | 4、6、7 |
| 历史不可改、fresh gates、merge hold与repair兼容 | 4、8、9 |
| runtime/inspection分离、串行lint、本机限制 | 8、9 |
| 旧v2/Watcher/其他executor fail-closed | 4、8、9 |
| 独立工具先审阅、live再授权、无通用skill变更 | Global Constraints、9、live runbook |

以上是待执行计划，不是已实现接口清单。任务 checkbox 只能在执行证据产生后勾选。

计划审阅补充：所有 Python 测试块放在 `unittest.TestCase` 测试模块中，明确导入
`unittest`、所用标准库与本任务定义的新接口；先确认缺接口造成的 RED，再完成实现。
暂停登记是授权之前的独立非交付写操作，已经列入 Task5/8 和 live 边界，不将它
误记为已消费的刷新 grant。所有新 API 名称在所属任务 Interfaces 中定义。
