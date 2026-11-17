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
