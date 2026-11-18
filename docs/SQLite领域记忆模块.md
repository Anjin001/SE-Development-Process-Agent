# SQLite 领域记忆服务（QLM）

## 依据与边界

依据仓库 docs/接口契约-v2.md 的版本、隔离和恢复原则，以及 TeamWork 提供的《领域数字人记忆系统设计_AutoGen0.2与SQLite》§3–§9。检查过 origin/ZXQ 的 memory.py/schema.sql：该分支已有经验服务及运行存储，故本模块使用 se_agent/domain_memory.py 与 dm_ 表前缀，避免覆盖同学的实现。可使用独立数据库路径，也可在同一数据库建立这些独立表。

这是标准库 SQLite 服务，未接入现有 v1 HTTP、HTML 模拟界面或 AutoGen。调用者应是可信的服务端控制器；身份、阶段、发布复查者必须来自服务端授权结果，不能直接接受 Agent 自报。无需模型密钥或额外依赖。

## 接口

- DomainMemory(path) 初始化持久化数据库；close() 关闭。
- join(project, agent, group_id) 注册稳定身份的项目成员关系。
- save(id, kind, project, owner, body, visibility, expected_version) 保存不可覆盖的版本，返回 id/version。kind 支持 agent/message/artifact/decision/case/experience；通用 JSON 正文保存履历、原文、取舍、结果级别等业务字段。版本检查及来源验证与插入在同一事务中完成。
- read(id, version, project, agent, stage) 读取精确版本并重新检查来源。未授权抛 PermissionError，不存在抛 KeyError，版本冲突抛 ValueError。
- freeze_baseline(project, agent, refs) 一次固定项目共享基线，拒绝再次覆盖。引用须为公开版本。后续经验修订不会悄悄改变 A/B 共用基线。
- alias(domain, alias, tag) 保存领域同义映射，允许同词多个标签。
- search(project, agent, stage, domain, terms, limit) 从固定基线按标签、短语和别名检索，结果携带精确版本、证据等级、命中项及 trigger_matched。required_tags 全部满足才触发检查；部分匹配仅供参考。场景字段由控制器提供，不能仅凭“设备”断言已有预约受影响。
- checkpoint(project, run, agent, body, expected_revision) 原子保存阶段恢复位置，revision 防止旧写覆盖。
- resume(project, run, agent) 恢复已完成、待处理任务及引用，并按当前身份和阶段重新校验 record_refs。
- usage(project, run, agent, ref, decision, reason, evidence) 追加采用/拒用与观察证据，旧使用记录保留。

## 权限与阶段

private 仅作者；group 在组内出案等非独立阶段允许同组读取；project 仅 sharing/integration/retrospective 允许项目成员读取；published 可跨项目读取。独立阶段使用 ideation/intake/race，独立评审使用 review。私有聊天不会因公开其他产物而自动开放。公开产物不得间接引用私有来源，直接读取也递归核验来源。

检查点是同一项目控制器共享状态，只保存阶段、completed、pending 和 record_refs 等引用，不得存放私有原文；原文应保存到受权限约束的 message 记录。join、save 的发布操作、alias 和 checkpoint 均是可信控制器接口，不是面向任意客户端的鉴权 HTTP API。

## 经验正文

experience 必填 domain/trigger/correction/pass_condition/evidence_state/sources；发布须有 reviewed_by。sources 是已有记录的 {id,version} 列表。证据等级独立于可见范围：pending/simulated/walkthrough/pilot/production。可增加 tags/keywords/required_tags、scope/exceptions、反例、修改理由和反馈字段。修订使用原 ID 和最新 expected_version，生成下一版本，旧版本与项目基线仍可追溯。

发布或归纳由控制器组织独立复查；本模块不自动生成经验或把课堂模拟升级为生产验证。旧偏好是否适用、预约时段是否重叠仍需业务判断。state 非 published 的经验不进入推荐。当前没有撤权管理、匿名评审作者脱敏、模型归纳、检索缓存或浏览页面，这些需后续与对应模块联调。

## 验收

```bash
python -m unittest discover -s tests -v
```

本次 15 项全部通过，其中新增四组真实临时 SQLite 文件测试：

1. B 组私有草案在独立构思、评审、共享阶段均不能被 A 读取，跨项目不可读，公开摘要不能引用私有来源。
2. “送修”别名命中停用经验；改名仅部分匹配、不触发；新版本不改变旧项目基线；拒用理由可记录。
3. 关闭并重新打开数据库后恢复待办与修订号，拒绝旧修订和非项目成员。
4. 内容版本冲突拒绝覆盖，悬空来源导致事务回滚，不留半条记录。

运行编排的 attempts/events/lease/idempotency 继续由朱新棋的 Store 负责，本服务不替代该存储。

## 完善版增量（本节替代前述已过时的能力限制）

### 新增能力

- bootstrap/grant：服务端初始化项目和 admin/member/reviewer 角色；公开复查与状态变更校验角色，独立审核拒绝作者自己批准。
- publish：保留候选原稿，审核生成下一内容版本并记录审核人和理由；set_state 管理 published/review/retired/superseded 放行元数据；revoke 撤回所有版本来源，关联经验读取及检索会重新检查来源，历史基线不会绕过撤权。
- commit_stage：阶段产物与 checkpoint 在一个 BEGIN IMMEDIATE 事务内提交；按项目和 key 记录请求摘要，原请求重试直接返回原结果，不重复生成版本；同 key 不同内容拒绝。checkpoint 冲突会回滚前面写入的全部产物。
- MemorySession：固定项目、身份、阶段的 Agent 工具适配器，提供 read/search/propose；Agent 只能提交私有候选，不能通过参数伪造他人或审核者。DomainMemory 原始接口是可信控制器边界，不允许直接暴露给 Agent 或任意客户端。
- context/history/list_records：按访问权限重新组装上下文和浏览版本；checklist 记录 pending/passed/failed/not_applicable 与依据；usage 保留采用、拒用与证据；audit 为审核管理者提供带游标的操作记录。
- WAL、busy_timeout、多连接版本冲突、索引、加法迁移、SQLite backup API 备份。每个线程/请求使用独立连接；事务内部的嵌套调用参加外层事务。
- 检索按完整触发、命中数量、证据质量及稳定编号排序。短中文词与同义词走显式标签，不要求额外 FTS/向量库。业务反例用 required_tags、scope/exceptions 及 not_applicable 理由保留，不能让旧项目偏好覆盖当前需求。

### 演示与浏览

```bash
python -m se_agent.memory_demo
python -m se_agent.memory_server --db memory.sqlite --project PROJECT-01 --agent AGENT-01 --stage review --port 8004
```

memory_demo 在临时数据库中演示：座位预约案例 → 经验候选 → 独立审核发布 → 设备项目固定基线 → “周五送修”召回 → 检查项 → 产物和检查点原子提交 → 同请求重放 → 恢复待人工确认位置。演示结束清理临时数据。

浏览器打开 http://127.0.0.1:8004。浏览现有数据库之前需由控制器 join/bootstrap 创建对应成员。页面提供受权限过滤的记录列表、领域标签检索；GET /api/memory/record?id=...&version=... 精确读取。项目/身份/阶段由启动参数固定，请求传入 agent 不会覆盖。该本机浏览接口只读；生产环境需要统一应用认证后构造 MemorySession，不能把本地演示当成互联网多用户认证系统。

### AutoGen 接入

不强制安装 AutoGen。控制器在每次切换阶段时创建新的 MemorySession，注册 read/search/propose 作为受限工具，将 context 返回的事实及经验附到新的会话上下文；按消息原有 sender/phase/conversation_id 将正文保存在 message 记录中，checkpoint 只保存引用、阶段、已完成和待处理事项。恢复时先 resume 校验引用，再按 read 取出允许的消息，将转换后的会话消息传给实际 AutoGen GroupChatManager.resume。模块不反序列化不可信 Python 对象，也不自动重放外部模型调用。

### 迁移与集成

旧版 QLM 数据库重新打开时自动新增 dm_roles/status/revoked/audit/operations/checks 表和索引，不覆盖 dm_records，不改动朱新棋数据库的 PRAGMA user_version。旧系统的 join/save 是控制器兼容入口，需由统一服务认证保护；新 Agent 一律使用 MemorySession。ZXQ 的 memory.py 仍独立存在于其分支，组长可选择此模块并对其 Store 增加适配，不应同时把两份经验库作为事实来源。

### 学习参考

实现为本项目自行编写，未复制第三方源码，也未增加第三方运行依赖。参考：

- LangGraph SQLite Checkpointer：检查点存储、线程作用域和生命周期分离设计。https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-sqlite
- SQLite WAL 官方说明：读写并发、WAL 文件生命周期与备份注意事项。https://sqlite.org/wal.html

### 验收范围

测试覆盖角色审核、间接来源撤回、退役经验不再推荐但保留历史、固定基线、不适用边界、别名、跨组隔离、原子回滚、幂等冲突、重启恢复、双连接旧版本拒绝、备份可读、固定身份适配和 HTTP 身份不可覆盖。实际模型输出质量、AutoGen 特定版本集成以及全项目统一身份系统需在对应集成环境验证。

完善版最终验收：20 项完整回归测试全部通过（已去除继承造成的重复用例），memory_demo 生命周期演示通过，git diff --check 通过。
