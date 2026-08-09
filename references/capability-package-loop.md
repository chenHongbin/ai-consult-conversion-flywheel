# 机构专属能力包闭环

## 一句话定义

基础 Skill 不改；每个用户工作空间维护一份“机构专属咨询能力包”。日常回答优先读取这份能力包，所以录音、微信、机构资料和管理者反馈会逐步改变回答的八步法阶段判断、顾虑判断、话术结构和陪练内容。

这不是训练模型权重，也不是把原始患者资料塞进公共 Skill，而是把经过脱敏、证据绑定和测试的经验写成当前工作空间的版本化运行时资料。

## 四层数据

```text
基础 Skill                 公共、稳定、跨机构复用
机构事实                   医生、项目、价格、流程、地址、合规边界
团队经验                   销冠逻辑、阶段动作、顾虑处理、反例
持续记忆                   管理者反馈、训练结果、版本和评估结论
```

机构事实和团队经验必须分开。患者个案不能直接进入通用知识。原始音频、原图、HTML 和未脱敏文本只作为受控证据，不直接进入运行时上下文。

## 能力包目录

```text
咨询转化工作区/_系统/当前能力包/
├── active.json                    当前运行时指针
├── feedback.jsonl                 管理者反馈
└── versions/
    ├── v0.1/
    │   ├── package.json            结构化能力包
    │   └── runtime-context.md      运行时简明上下文
    └── v0.2/

咨询转化工作区/_系统/蒸馏候选/
└── v0.2/
    ├── candidate.json              本轮新增/修改候选
    └── merged-preview.json         合并后的预览
```

`07_我的产出/03_销冠蒸馏能力包/` 保存给管理者看的版本说明；`_系统/当前能力包/` 才是运行时读取的真源。

## 每轮 Dream Cycle 的闭环

1. **资料增量**：扫描工作空间，按哈希找到新增或变化资料；
2. **证据标准化**：转写、OCR、HTML 解析、脱敏、案例卡和结果标签；
3. **候选蒸馏**：模型读取当前能力包和本轮增量，输出结构化 `candidate.json`；
4. **合并**：按规则 ID、事实 ID、阶段和问题 ID 合并，不覆盖没有变化的旧规则；
5. **验证**：检查证据、反例、授权、隐私、医疗边界和固定测试；
6. **发布**：生成新版本，保留旧版本，把 `active.json` 指向新版本；
7. **运行**：下一次咨询分析先读取 `active.json` 指向的 `runtime-context.md`；
8. **反馈**：管理者说“保留、修改或废弃这条规则”，写入 `feedback.jsonl`，成为下一轮增量蒸馏输入。

## 候选 JSON 最小格式

模型或 Agent 必须输出结构化候选，不要只输出一篇散文报告：

```json
{
  "schema_version": "1.0-candidate",
  "source_run_id": "run-2026-08-09-001",
  "scope": {
    "institution": "当前机构",
    "department": "当前科室",
    "disease_or_project": "当前病种或项目",
    "channel": "电话/微信/通用"
  },
  "promotion": {
    "requested": true,
    "coverage_gate_passed": true,
    "evaluation_passed": true
  },
  "delta": {
    "facts_upsert": [],
    "sales_logic_upsert": [],
    "rules_upsert": [],
    "objections_upsert": [],
    "faq_100_upsert": [],
    "training_200_upsert": [],
    "practice_scenarios_upsert": [],
    "counterexamples_upsert": [],
    "deprecate_rule_ids": []
  },
  "change_summary": []
}
```

每条销冠逻辑、规则和顾虑必须带 `evidence_refs`；没有证据的内容只能作为待确认候选。医生、价格、地址、疗效和治疗周期不能从转化结果推断。

## 运行时加载规则

每次分析、回复、陪练和团队复盘前：

1. 读取 `active.json`；
2. 如果是 `base_only`，使用基础 Skill，并告知机构能力还未建立；
3. 如果是 `active`，读取对应 `runtime-context.md`；
4. 绑定机构、科室、病种/项目、渠道；
5. 默认按咨询转化八步法分析；优先使用已确认机构事实和当前能力包，再读取原始证据核对；
6. 不把某个个案的姓名、电话、病历和未经确认事实带入回答。

## 自动发布边界

结构化写回可以自动执行；自动切换 active 版本必须同时满足：覆盖率通过、候选无隐私泄露、规则有证据、至少有反例或适用边界、固定测试通过。事实类内容只有在机构资料已确认或管理者明确确认后才能成为 active。失败时保留候选和日志，不影响旧版本运行。

## 回滚

回滚只改变 `active.json` 的指针，不删除任何版本和原始证据。这样新一轮能力变差时，可以立即恢复上一版：

```bash
python3 scripts/rollback_capability.py <工作空间根目录> --previous
```

## 团队发布

主管端能力包通过测试后，使用：

```bash
python3 scripts/build_team_skill_package.py <工作空间根目录> \
  --output-dir <团队发布目录> \
  --institution <机构名称> \
  --department <科室名称>
```

生成的 `.skill` 包含基础 Skill 和 `institution-pack/`，团队成员只安装这一个包即可使用机构最新能力。包内只放已发布的结构化能力和运行时上下文，不放原始录音、微信、患者身份信息、管理者私有资料或候选规则。
