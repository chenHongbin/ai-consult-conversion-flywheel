# v2.0 管理数据契约

四类机器记录统一保存在：

`咨询转化工作区/_系统/管理工作台/`

它们是主管工作台的内部真源。原始录音、微信截图、分析报告和团队表格继续保存在原有目录，不迁移、不删除。

## communication-samples.jsonl

一行代表一份可独立分析的沟通片段。最小输入可以是一通录音、一段微信或一张截图。

必需字段：

`sample_id、source_hash、employee_id、date、medium、stage、patient_facts、uncertainties、breakpoint、consultant_actions、evidence_refs、patient_next_action、employee_gap、team_pattern_candidate、outcome、outcome_source、created_at、updated_at`

- 相同 `source_hash` 只保留更新时间最新的一条；没有哈希时按稳定 `sample_id` 归并。
- `patient_facts` 只记录患者已表达事实；推断放在 `uncertainties`。
- 不知道的预约、到院、成交或字段值写 `unknown` 或 `missing`，不能写数字 0。
- `evidence_refs` 必须能定位到本地文件、报告位置、录音时间点或聊天位置。

## management-events.jsonl

管理任务使用追加式事件，不覆盖历史：

`task_id、event、priority、type、target、reason、action、due_date、source_refs、status、note、review_sample、duration_minutes、created_at`

事件按时间依次归并：

`create → pending`、`start → in_progress`、`complete → completed`、`review → reviewed`、`reject → rejected`。

同一个 `task_id` 的最新事件决定当前状态，旧事件保留用于日清和主管复核耗时统计。

## training-actions.jsonl

训练任务字段：

`action_id、scope、target_id、topic、champion_example、failure_example、key_action、pass_criteria、review_method、review_samples、source_employee、adopted_employees、passed_employees、status、created_at、updated_at`

- `scope` 只使用 `team` 或 `employee`。
- 每名员工每周期只能显示一个当前主要训练动作。
- `source_employee` 标记销冠动作来源；普通咨询师的采用和通过名单用于计算团队复制率。

## dashboard-data.json

该文件由 `generate_management_dashboard.py` 聚合生成，不手工编辑，是本地 HTML 的唯一数据源。包含：

- 机构、团队、周期和更新时间；
- 今日管理结论、任务、训练、员工和证据；
- 经营漏斗、管理指标与数据完整性；
- 女娲五级能力数量；
- 夜间待处理、失败项和当前能力版本。

## 稳定 ID 与缺失值规则

- 样本 ID、任务 ID、训练 ID、能力 ID 一旦生成不得换义复用。
- 追加记录重复出现时，按稳定 ID 和 `updated_at/created_at` 取最新版。
- `unknown` 表示当前不知道，`missing` 表示要求的数据源缺失；两者都不能参与数值计算。
- 分母缺失或为零时，指标状态为 `missing`，百分比值为 `null`。

## 兼容规则

v2.0 延续 v1.9 工作区目录契约。旧工作区第一次记录管理数据或生成工作台时，才按需创建 `_系统/管理工作台/`；不要求用户迁移旧资料。
