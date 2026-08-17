# 日期提供与 `DEMO_TODAY` 切换说明

## 1. 结论

当前项目处于演示阶段，随访业务日期应统一使用配置项 `DEMO_TODAY`。真实生产日期暂时不需要接入，保留 `DateProvider` 作为未来的替换接口即可。

需要区分“设计目标”和“当前代码现状”：

- 设计方向是正确的：业务代码不应直接调用系统日期，而应依赖 `DateProvider`。
- 当前代码还没有完全做到“只有接口”：部分代码已经包含真实日期回退逻辑，旧调度器和旧仓储中仍可能直接调用 `date.today()`。
- 因此，当前阶段可以继续使用 `DEMO_TODAY` 演示，但上线前必须完成日期调用收口。

## 2. 演示阶段的日期规则

演示环境配置：

```env
DEMO_TODAY=2026-07-15
```

在演示阶段，以下所有业务日期都必须使用 `2026-07-15`：

- 当日应随访名单计算
- 免随访名单计算
- 连续未回复天数计算
- 随访计划频次命中判断
- 随访会话创建日期
- 患者随访报告日期
- AI 审阅和医生审阅关联日期

日期流转应保持为：

```text
DEMO_TODAY
    ↓
DateProvider.get_business_date()
    ↓
DispatchState.business_date
    ↓
名单判定、患者会话、风险评估、报告和审阅
```

调度开始时只解析一次业务日期，并写入 `DispatchState.business_date`。同一次调度中的所有节点都读取该字段，不能在不同节点重新获取系统日期。

## 3. 当前代码状态

### 3.1 已有的正确设计

`infrastructure/date_provider.py` 已提供日期抽象：

```python
class DateProvider:
    def get_business_date(self) -> date:
        ...
```

`AppContext` 也已经注入 `clock`/`DateProvider`，这为后续生产环境替换实现提供了接口。

### 3.2 当前仍存在的真实日期实现

目前代码中仍有以下行为：

| 位置 | 当前行为 | 问题 |
|---|---|---|
| `core/config.py` | `DEMO_TODAY` 为空时允许继续运行 | 演示环境可能意外使用系统日期 |
| `infrastructure/date_provider.py` | 配置为空或格式错误时回退到 `datetime.now()` | 这已经是实际的真实日期实现，不只是接口 |
| `engine/followup_scheduler.py` | 仍存在 `date.today()` | 绕过了 `DateProvider` |
| 旧仓储/旧服务 | 部分日期字段直接使用系统日期 | 可能造成同一调度日期不一致 |
| `date_provider.py` | 顶部导入 `DEMO_TODAY` | 进程运行期间修改配置后不会动态刷新 |

所以当前准确状态是：

> 项目已经具备 `DateProvider` 接口，演示日期由 `DEMO_TODAY` 控制，但真实日期回退逻辑和旧代码直取日期的路径还没有完全清理。

## 4. 推荐的阶段性实现

### 4.1 当前演示阶段

当前先保持演示可复现：

```text
必须配置 DEMO_TODAY
        ↓
DateProvider 返回 DEMO_TODAY
        ↓
调度图写入 business_date
        ↓
所有业务节点使用 business_date
```

建议演示环境启动时校验：

```python
if not DEMO_TODAY:
    raise RuntimeError("演示环境必须配置 DEMO_TODAY")
```

这样可以避免配置遗漏后悄悄使用服务器当天日期。

### 4.2 未来生产阶段

生产环境只需要替换 `DateProvider` 的实现，不需要修改调度图和患者随访图：

```python
class DateProvider:
    def get_business_date(self) -> date:
        # 未来实现：读取系统业务日期、医院业务日历或日期服务
        ...
```

未来可以根据环境选择实现：

```text
DemoDateProvider       → 读取 DEMO_TODAY
SystemDateProvider     → 读取业务时区下的系统日期
HospitalCalendarProvider → 读取医院工作日/节假日规则
```

业务代码只依赖 `DateProvider` 接口，不直接依赖上述具体实现。

## 5. 必须统一修改的代码位置

### 5.1 `DateProvider` 动态读取配置

不要在模块导入时固定：

```python
from core.config import DEMO_TODAY
```

建议在 `get_business_date()` 内动态读取配置，或通过配置对象注入，避免进程启动后配置变化不生效。

### 5.2 调度入口解析一次业务日期

在 `DispatcherAgent` 或 `dispatcher_graph` 的入口：

```python
business_date = context.clock.get_business_date()
state["business_date"] = business_date.isoformat()
```

之后的节点禁止再次调用：

```python
date.today()
datetime.now()
```

### 5.3 迁移旧调度器

`engine/followup_scheduler.py` 当前仍有自己的 `get_today()` 和 `date.today()` 逻辑。迁移后应改为接收明确日期：

```python
build_today_send_list(
    patients,
    business_date=state["business_date"],
)
```

最终由 `domain/services/roster_decider.py` 或新的名单服务调用 `DateProvider`，旧 `engine` 只作为过渡兼容层。

### 5.4 迁移数据库写入

患者历史、随访会话、报告和审阅记录的日期都必须由调用方传入：

```python
repository.save_episode(
    episode=episode,
    business_date=state["business_date"],
)
```

仓储层不应自行调用系统日期。

### 5.5 清理旧代码路径

以下旧路径需要逐步禁止直接获取日期：

- `engine/followup_scheduler.py`
- 旧版 `followup_service.py`
- `auto_runner.py`
- `manual_runner.py`
- 旧数据库仓储和历史记录写入函数
- 其他包含 `date.today()`、`datetime.now()` 的业务模块

## 6. 与当前患者调度规则的关系

日期提供器只负责确定业务日期，不负责决定患者是自动演示还是手动演示。

当天患者流程应为：

```text
DateProvider 确定 business_date
        ↓
RosterDecider 根据数据库字段、随访计划和 business_date
        ↓
得到 send_roster / skip_roster
        ↓
DispatcherAgent 读取外部 manual_patient_ids
        ↓
send_roster ∩ manual_patient_ids → 手动演示患者
send_roster - manual_patient_ids → 自动演示患者
skip_roster → 不创建患者智能体
```

手动患者配置示例：

```yaml
# config/followup_runtime.yaml
manual_patient_ids:
  - 15
  - 27
```

这里的患者 ID 只影响输入来源，不影响当天是否应随访。配置中的 ID 如果不在当天 `send_roster` 中，应忽略并记录日志，不能强制加入当天名单。

## 7. 测试要求

### 7.1 演示日期测试

- `DEMO_TODAY=2026-07-15` 时，所有节点得到同一个业务日期。
- 跨系统日期变化时，演示结果仍保持不变。
- `DEMO_TODAY` 格式错误时，演示环境应直接报配置错误，而不是静默使用系统日期。

### 7.2 调度一致性测试

- `DispatchState.business_date` 与会话、报告、审阅记录日期一致。
- 同一调度内不能出现两个不同日期。
- 重试节点不会重新计算日期。

### 7.3 迁移扫描

上线前对后端执行搜索：

```bash
rg "date\.today|datetime\.now|datetime\.utcnow|from datetime import date" backend/
```

搜索结果中，业务代码不应再直接使用系统日期；仅允许 `DateProvider` 的具体实现使用系统时钟。

## 8. 最终目标

### 现在

```text
演示环境：DEMO_TODAY=2026-07-15
真实日期：暂不接入，只保留 DateProvider 接口
```

### 上线后

```text
生产环境：DateProvider 获取真实业务日期
调度图、患者图、审阅图：无需修改
所有业务节点：继续读取 DispatchState.business_date
```

最终原则：

> 日期的获取由 `DateProvider` 负责，日期的冻结由调度入口负责，业务节点只消费 `business_date`，任何业务模块都不能自行决定“今天是哪一天”。

## 9. 总体架构判断与待修改项

日期和患者输入来源之外，当前整体重构方向是正确的：已经具备总调度、单患者随访、患者模拟、对话解析、风险评估、问题生成和审阅等职责拆分，也已经开始使用 LangGraph、Repository、Runtime Context 和 Prompt Registry。

但是，当前代码还不能视为一条完整、稳定的生产执行链路。主要问题不是“缺少更多 Agent”，而是图之间的状态契约、持久化、事件一致性和旧路径清理还没有完全收口。

### 9.1 P0：必须优先修复的问题

#### 1. 补齐 LangGraph 状态契约

当前 `states.py` 主要定义了调度状态和患者状态，但以下 Agent 已引用尚未统一定义的状态类型：

- `ConversationState`
- `SimState`
- `PlanState`
- `ReviewState`

建议在 `graphs/states.py` 或统一的 `agents/state.py` 中定义所有图状态，并明确：

- 字段类型
- 初始值
- Reducer 合并规则
- 可序列化要求
- 哪些字段属于业务状态，哪些属于运行时依赖

数据库连接、LLM Client、EventBus、Repository 等对象不能放入 checkpoint State。

#### 2. 统一 Agent 导入路径

当前新工作流可能从 `agents.capability_agents` 导入能力 Agent，但已有文件又位于 `agents/` 根目录，容易导致启动时导入失败。

需要二选一并全量统一：

```text
agents/
├── workflows/
└── capability_agents/
```

或全部能力 Agent 直接放在 `agents/` 根目录。推荐保留 `capability_agents/`，并为每个目录补齐 `__init__.py`。

#### 3. 重新设计人工患者的挂起与恢复

人工患者会在患者图中调用 `interrupt`。如果总调度图通过 `Send` 同步等待子图完成，人工患者挂起可能导致父图整体暂停，自动患者完成后的汇总也无法按预期立即执行。

推荐改为：

```text
DispatcherAgent
    ├── 创建 dispatch 记录和 episode 记录
    ├── 为每个患者启动独立 PatientFollowupGraph
    └── 立即返回调度状态

PatientFollowupGraph
    ├── 自动患者：继续运行模拟对话
    └── 手动患者：interrupt，等待 resume

episode 完成事件
    └── 更新 episode projection，供总调度查询和汇总
```

也就是说，总调度不应依赖所有患者同步返回，而应通过独立 episode 状态和事件聚合结果。

#### 4. 使用真正持久化的 Checkpointer

人工患者挂起后，系统必须支持：

- WebSocket 断开后恢复
- 服务重启后恢复
- 多进程/多实例恢复
- 通过 `episode_id` 精确恢复

LangGraph Checkpointer 不能只使用内存实现。生产前需要接入数据库或 Redis 等持久化存储，并统一使用：

```text
thread_id = episode_id
```

同时保留 `dispatch_id`、`episode_id`、`checkpoint_id` 的关联关系。

#### 5. 修复 `AppContext` 装配顺序

`AppContext` 已声明 `policy_repository`，但默认依赖初始化和 Bootstrap 装配需要再次核对。Outbox 如果在 EventBus 注入前创建，也可能持有空的 EventBus。

推荐顺序：

```text
创建 EventBus
    ↓
创建 DateProvider / LLMGateway / Repositories
    ↓
创建 Outbox
    ↓
组装 AppContext
    ↓
注入 LangGraph Runtime
```

不要在上下文尚未完整装配前调用 `ensure_defaults()`。

#### 6. 图节点使用 Runtime Context，不依赖全局单例

图已经声明 `context_schema=AppContext`，但部分节点仍使用全局 `get_context()`。这会导致测试隔离困难，也会在多租户或多调度并发时串用依赖。

应统一为：

```python
async def node(state: State, runtime: Runtime[AppContext]):
    repo = runtime.context.patient_repository
```

全局上下文只能作为旧代码过渡兼容，不应成为新图的依赖来源。

#### 7. 修复 State Reducer 的数据形状和幂等性

`reports` 等字段的类型声明和节点返回值存在列表/字典不一致的风险。需要统一约定，例如：

```python
reports: dict[str, PatientReport]
```

并按 `episode_id` 合并。

消息、事件、报告在图重试时必须去重，不能简单使用列表拼接。建议为每条消息和事件生成稳定的 `message_id`/`event_id`，Reducer 按 ID 去重。

#### 8. 分离回调策略版本和对话策略版本

调度器传递的 `callback_policy_version` 不能被写入 `conversation_policy_version` 或报告的错误字段。建议在 State 和 Report 中分别保留：

- `callback_policy_version`
- `conversation_policy_version`
- `business_date`

所有版本都要随 episode 固化，避免策略更新后重试得到不同结果。

### 9.2 P1：应在演示链路稳定后完成

#### 1. 风险评估与 TurnRouter 解耦

流程必须保持：

```text
ReplyUnderstandingAgent → 只解析回复
RiskEvaluator            → 只计算风险
TurnRouter               → 只决定继续、结束或转人工
QuestionComposerAgent    → 只生成追问文案
```

ReAct 或普通 LLM 不应直接调用：

- `escalate_alert`
- `finalize_followup`
- `database_write`

`TurnRouter` 必须接收结构化的 `risk_result`，不能只根据回复理解和覆盖率判断是否结束。

#### 2. 修复风险规则边界

- `all([])` 不能被当作连续三天都存在某风险，应先判断数据长度。
- 年龄计算和风险解释文本必须使用同一阈值。
- 高风险结果必须明确映射到“转人工/告警/继续观察”等确定动作。

#### 3. 固化患者模拟场景

患者模拟 Agent 的场景事实应在 episode 开始时生成一次，并在所有轮次复用。不能每一轮使用 `episode_id + round_num` 重新生成，否则同一患者的症状、用药和情绪可能前后矛盾。

缓存键应使用 `episode_id`，不能只使用 `patient_id`，避免不同日期的 episode 相互污染。

#### 4. AI 审阅采用独立且幂等的生命周期

单患者随访结束后触发 AI 审阅是合理的，但必须保证审阅不会覆盖医生审阅记录。建议状态为：

```text
episode_completed
    → ai_review_pending
    → ai_review_ready
    → doctor_reviewed
```

AI 审阅结果使用 `episode_id` 或 `review_id` 做唯一键，重复触发时只更新同一条 AI 审阅记录。

#### 5. 统一计划生成入口

新的 `planner_agent.py` 与旧的 `planner.py`/`planner(1).py` 不能长期并存。路由、服务和测试应统一导入一个计划 Agent，旧文件只保留兼容包装，完成迁移后删除。

#### 6. 将名单判定迁移到领域服务

`RosterDecider` 不应继续依赖旧的 `engine.followup_scheduler` 作为核心实现。自然语言电话回访策略可以由 `CallbackPolicyCompilerAgent` 编译，但数据库字段、随访计划、日期窗口和免随访状态仍应由结构化领域服务判断。

#### 7. 完善回调策略的编译、预览和审批

推荐流程：

```text
医护输入自然语言
    → 编译为结构化策略
    → 预览命中人数和患者列表
    → 医护审批
    → 固化 policy_version / policy_hash
    → Dispatcher 按版本执行
```

预览和正式执行不能每次重新编译原始文本，否则同一文本可能得到不同策略。策略应持久化到数据库，而不是只放在内存字典中。

#### 8. 数据模型补充调度维度

随访会话、消息、风险、报告和审阅不能只依赖 `patient_id`。至少需要：

- `dispatch_id`
- `episode_id`
- `business_date`
- `input_source`
- `review_id`
- `idempotency_key`

否则同一患者不同日期的随访可能互相覆盖。

#### 9. Repository 不要吞掉所有异常

当前 Repository 中把异常统一转换成 `None`，容易把数据库故障误判为“没有数据”。应区分：

- 记录不存在
- 数据库连接失败
- 唯一键冲突
- 事务失败
- 数据校验失败

关键写入必须使用事务和数据库唯一约束。

#### 10. 禁止运行时自动建库和自动 Seed

`PatientDB.__init__()` 不应在生产请求路径中自动初始化数据库并写入种子数据。建表、迁移和演示 Seed 应分别由部署脚本执行。

#### 11. 完善调度和 episode 查询持久化

当前进程内的 `_running_dispatches`、最近一次报告等变量无法支持服务重启和多实例。应持久化：

- dispatch 状态
- episode 状态
- 完成数量/失败数量
- 最后错误
- 汇总报告

`/api/followups/episodes/{episode_id}` 不能返回占位文本，应返回真实的状态、消息、风险、审阅和报告投影。

#### 12. 统一 REST 和 WebSocket 的恢复入口

`episode:resume` 不应只启动后台任务后立即返回。需要发送：

- `resume_started`
- `resume_succeeded`
- `resume_failed`

并将异常写入 episode 状态。REST 和 WebSocket 应调用同一个 `EpisodeService`。

### 9.3 P2：整理和质量提升

#### 1. Prompt 参数真正传递到 LLM Gateway

`PromptSpec` 中声明的 `temperature`、`max_tokens`、`response_format` 不能只作为元数据保存，Agent 调用时应传递给 Gateway，并记录实际使用的 prompt 版本。

#### 2. 统一审阅数据模型

审阅提示词输出的风险项和建议包含严重等级、优先级等结构化字段，不应被转换成简单字符串。建议定义：

```text
RiskFlag { code, severity, evidence }
ReviewSuggestion { action, priority, reason }
```

#### 3. 修正用药状态模型

如果提示词允许 `partial`，模型不能只定义 `bool | None` 并将其丢失。应使用明确枚举，例如：

```text
medication_status = taken | not_taken | partial | unknown
```

#### 4. 删除硬编码患者 ID

Bootstrap 或其他代码中的固定跳过患者 ID必须删除，或者迁移到外部配置。任何患者 ID 都不能写死在业务代码中。

#### 5. 统一 Prompt 文件，清理重复实现

新旧计划提示词、系统护栏提示词不能重复维护。每个 Prompt 只保留一个文件，由唯一 Agent 引用，并由 `PromptRegistry` 启动时校验。

#### 6. 完善事件和 Outbox

EventBus 的回调签名、事件载荷和 Runtime 广播参数需要统一。生产环境应使用持久化 Outbox 和后台投递器，不能只依赖进程内去重。

## 10. 推荐实施顺序

```text
第一阶段：状态契约、导入路径、AppContext、DateProvider 收口
    ↓
第二阶段：独立 episode、Checkpointer、人工 interrupt/resume
    ↓
第三阶段：Reducer 幂等、Repository 事务、dispatch/episode 持久化
    ↓
第四阶段：风险路由、模拟场景稳定、AI 审阅生命周期
    ↓
第五阶段：Prompt Registry、回调策略版本化、旧路径清理
    ↓
第六阶段：生产 DateProvider、Outbox Worker、数据库迁移和多实例验证
```

在第一至第三阶段完成前，不建议把系统判断为“已完成 LangGraph 生产化重构”。当前更准确的定位是：

> Agent 职责拆分和图编排方向已经建立，但仍处于执行链路收口和可靠性加固阶段。
