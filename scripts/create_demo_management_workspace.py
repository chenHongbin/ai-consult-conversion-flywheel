#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create an isolated synthetic v2.0 manager workbench demo."""

import argparse
import datetime
import io
import json
import subprocess
import sys
from pathlib import Path

from compat import ensure_dir, expand_path
from management_data import CAPABILITY_FILE, EVENTS_FILE, SAMPLES_FILE, TRAINING_FILE, append_jsonl, load_json, management_root, now_iso, save_json


ROOT = Path(__file__).resolve().parents[1]


def run(script, args):
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts" / script)] + args,
                               cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8") or stdout.decode("utf-8"))
    return json.loads(stdout.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Create a synthetic AI consultation management workspace demo.")
    parser.add_argument("target_directory")
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    args = parser.parse_args()
    target = expand_path(args.target_directory)
    demo_container = target / "AI咨询转化飞轮_v2.0_合成演示"
    ensure_dir(demo_container)
    result = run("init_consult_workspace.py", [str(demo_container), "--manager-name", "咨询主管_林老师",
                                                 "--members", "A001_张宁,A002_李明,A003_王芳"])
    workspace = Path(result["workspace"])
    profile_path = workspace / "_系统" / "来源配置.json"
    profile = load_json(profile_path, {}) or {}
    profile["institution"] = "安心医疗示范机构（合成）"
    profile.setdefault("team", {})["team_name"] = "在线咨询一组（合成）"
    save_json(profile_path, profile)
    state_path = workspace / "_系统" / "状态.json"
    state = load_json(state_path, {}) or {}
    state.update({"institution": "安心医疗示范机构（合成）", "stage": "演示就绪", "team_management_ready": True})
    save_json(state_path, state)
    save_json(workspace / "_系统" / "演示标记.json", {"synthetic_demo": True, "created_at": now_iso(),
                                                       "warning": "全部人物、对话、结果与机构均为合成演示，不得当作真实医疗案例。"})
    store = management_root(workspace)
    samples = [
        {"sample_id":"S-DEMO-001","source_hash":"demo-hash-001","employee_id":"A001","employee_name":"张宁","date":args.date,"medium":"audio","stage":"攻破抗拒","patient_facts":["患者说需要考虑"],"patient_uncertainty":"不知道到院能解决什么","breakpoint":"患者说考虑后，没有把顾虑问具体","consultant_actions":["继续介绍项目"],"evidence_refs":["合成录音-001@00:03:12"],"next_patient_service_action":"先确认具体顾虑","employee_gap":"顾虑验证不足","verified_strength":"开场亲和","team_candidate_pattern":"把考虑内容问具体","outcome":"未预约","outcome_provenance":"synthetic","updated_at":now_iso()},
        {"sample_id":"S-DEMO-002","source_hash":"demo-hash-002","employee_id":"A002","employee_name":"李明","date":args.date,"medium":"wechat","stage":"攻破抗拒","patient_facts":["患者询问费用"],"patient_uncertainty":"担心花钱不值","breakpoint":"患者说考虑后，没有把顾虑问具体","consultant_actions":["连续发送价格说明"],"evidence_refs":["合成微信-002#第8条"],"next_patient_service_action":"确认患者是在意总费用还是价值","employee_gap":"顾虑验证不足","verified_strength":"回复及时","team_candidate_pattern":"把考虑内容问具体","outcome":"unknown","outcome_provenance":"missing","updated_at":now_iso()},
        {"sample_id":"S-DEMO-003","source_hash":"demo-hash-003","employee_id":"A003","employee_name":"王芳","date":args.date,"medium":"audio","stage":"攻破抗拒","patient_facts":["患者需要和家人商量"],"patient_uncertainty":"家庭决策信息不足","breakpoint":"患者说考虑后，没有把顾虑问具体","consultant_actions":["尊重选择","询问需要和家人确认什么"],"evidence_refs":["合成录音-003@00:04:21"],"next_patient_service_action":"整理一份家庭决策要点","employee_gap":"","verified_strength":"能复述患者具体情况","team_candidate_pattern":"把考虑内容问具体","outcome":"已到院","outcome_provenance":"synthetic","updated_at":now_iso()},
        {"sample_id":"S-DEMO-004","source_hash":"demo-hash-004","employee_id":"A001","employee_name":"张宁","date":args.date,"medium":"wechat","stage":"攻破抗拒","patient_facts":["患者担心以前治疗无效"],"patient_uncertainty":"不确定此次评估的差异","breakpoint":"效果顾虑承接不足","consultant_actions":["复述既往经历","说明先评估再决定"],"evidence_refs":["合成微信-004#第12条"],"next_patient_service_action":"发送评估流程卡","employee_gap":"顾虑验证不足","verified_strength":"开场亲和","team_candidate_pattern":"先承接既往经历","outcome":"已预约","outcome_provenance":"synthetic","updated_at":now_iso()},
    ]
    for row in samples:
        row["schema_version"] = "2.0-communication-sample"
        append_jsonl(store / SAMPLES_FILE, row)
    training = {"schema_version":"2.0-training-action","action_id":"T-DEMO-001","scope":"team","target_id":"team","title":"把“我再考虑”问具体","reason":"3名咨询师中有2名在患者说考虑后继续介绍项目","key_action":"先尊重患者，再用一个开放问题确认费用、时间、效果或家庭决策中的具体顾虑","pass_criteria":"每人新提交1条样本，目标动作出现且没有连续发送三段项目介绍","review_method":"次日复查真实或合成练习样本","champion_refs":["S-DEMO-003"],"failure_refs":["S-DEMO-001","S-DEMO-002"],"review_samples":["S-DEMO-004"],"source_employee":"A003","adopted_employees":["A001","A002"],"passed_employees":["A001"],"status":"awaiting_review","created_at":now_iso(),"updated_at":now_iso()}
    append_jsonl(store / TRAINING_FILE, training)
    tasks = [
        {"task_id":"M-DEMO-001","event":"create","priority":"P0","type":"review","target":"A001","reason":"顾虑验证不足重复出现","action":"复核A001未预约录音并完成单动作辅导","due_date":args.date,"source_refs":["S-DEMO-001"],"created_at":now_iso()},
        {"task_id":"M-DEMO-002","event":"create","priority":"P0","type":"training","target":"team","reason":"团队共性断点","action":"用销冠与失败对照完成10分钟晨会训练","due_date":args.date,"source_refs":["S-DEMO-001","S-DEMO-003"],"created_at":now_iso()},
        {"task_id":"M-DEMO-003","event":"create","priority":"P1","type":"capability_review","target":"team","reason":"发现可复制动作","action":"审核女娲候选：把考虑内容问具体","due_date":args.date,"source_refs":["S-DEMO-003"],"created_at":now_iso()},
    ]
    for row in tasks:
        row.update({"schema_version":"2.0-management-event","command":"处理管理任务 {0}".format(row["task_id"])})
        append_jsonl(store / EVENTS_FILE, row)
    append_jsonl(store / EVENTS_FILE, {"schema_version":"2.0-management-event","task_id":"M-DEMO-002","event":"complete","note":"已完成10分钟合成晨会训练","review_sample":"S-DEMO-004","duration_minutes":12,"created_at":now_iso()})
    capability = {"schema_version":"2.0-capability-progression","capability_id":"CAP-DEMO-001","name":"把考虑内容问具体","stage":"trainable_action","requested_stage":"trainable_action","support_case_ids":["S-DEMO-003","S-DEMO-004"],"counterexample_ids":["S-DEMO-001"],"applicable_conditions":["患者使用模糊推迟表达"],"manager_reviewed":True,"personalization_chain":"患者表达考虑→咨询师确认具体事项→提供对应材料","reassurance_chain":"模糊不安→具体顾虑→可选择的下一步","replication_chain":"销冠样本→晨会训练→普通咨询师复查","updated_at":now_iso()}
    append_jsonl(store / CAPABILITY_FILE, capability)
    data_dir = workspace / "08_团队管理" / "03_团队数据" / "01_今天放这里"
    ensure_dir(data_dir)
    process_csv = data_dir / "合成演示过程量.csv"
    with io.open(str(process_csv), "w", encoding="utf-8") as handle:
        handle.write("date,employee_id,employee_name,effective_consultations,appointments,arrivals,paid_cases,process_minutes\n" +
                     "{0},A001,张宁,12,4,3,2,180\n{0},A002,李明,10,3,2,1,165\n{0},A003,王芳,11,6,5,3,170\n".format(args.date))
    dashboard = run("generate_management_dashboard.py", [str(workspace), "--date", args.date])
    print(json.dumps({"status":"created","demo_root":str(demo_container),"workspace":str(workspace),
                      "dashboard":dashboard["dashboard"],"synthetic_demo":True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
