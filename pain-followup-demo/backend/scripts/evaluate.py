"""
RAG 质量评测脚本 — PainSmart 9.0

评估指标：
  1. RAG 检索召回率     — 成功检索到证据的计划占比（等于命中知识库的比率）
  2. 引用完整率         — 计划中 [n] 标记在 evidence_basis 中有定义的占比（防 LLM 幻觉）
  3. 字段覆盖度         — 非占位字段的覆盖率（计划完整度）
  4. 降级检测           — 因 LLM 失败或无证据回退模板的计划数
  5. 多样性             — 随访周期是否因病情而异

用法：
  cd backend
  python evaluate.py                           # 评测现有存量计划
  python evaluate.py --live --patients 10      # 新生成 N 份计划并评测
  python evaluate.py --live --all              # 为全部患者生成并评测
"""
import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path
from collections import Counter

# 确保 backend 在 sys.path
_BACKEND = str(Path(__file__).resolve().parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db.followup_db import _db_path


# ===================== 占位文本识别 =====================
PLACEHOLDER_PATTERNS = [
    "未检索到", "建议医生人工", "请医生", "待定", "需医生",
    "已回退", "LLM 生成失败", "LLM 未配置",
]

def is_placeholder(text: str) -> bool:
    if not text:
        return True
    for pat in PLACEHOLDER_PATTERNS:
        if pat in text:
            return True
    return False

def is_list_placeholder(items: list) -> bool:
    if not items:
        return True
    return all(is_placeholder(str(i)) for i in items)


# ===================== 字段定义 =====================
# 必须检查的字段
TEXT_FIELDS = [
    "frequency",
    "pain_type",
    "medication_adjustment",
    "warning_threshold",
]
LIST_FIELDS = [
    "recheck_items",
    "health_education",
    "lifestyle",
]
# Duration 和 note 单独处理


# ===================== 评测核心 =====================

def load_plans(limit=None):
    """从 SQLite 读取所有 plan"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM followup_plan ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    plans = []
    for r in rows:
        d = dict(r)
        for key in ("plan_json", "guideline_citations"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        plans.append(d)
    if limit:
        plans = plans[:limit]
    return plans


def evaluate_plan(plan: dict) -> dict:
    """
    对单份计划计算各项质量指标
    """
    pj = plan.get("plan_json") or {}
    citations = plan.get("guideline_citations") or []
    text = json.dumps(pj, ensure_ascii=False)

    # ── 指标 1：RAG 检索召回率 ──
    # 是否成功从知识库检索到证据
    has_rag = len(citations) > 0
    citation_count = len(citations)

    # ── 指标 2：引用完整率 ──
    # 计划文本中所有 [n] 标记是否都有对应的 evidence_basis 定义
    plan_refs = set(re.findall(r'\[(\d+)\]', text))
    defined_refs = set()
    for c in citations:
        ref = c.get("ref", "")
        defined_refs.add(re.sub(r'[\[\]]', "", ref))
    for i, c in enumerate(citations):
        defined_refs.add(str(i + 1))

    missing_refs = plan_refs - defined_refs
    citation_precision = (
        len(plan_refs - missing_refs) / len(plan_refs)
        if plan_refs
        else (1.0 if not has_rag else 0.0)
    )

    # ── 指标 3：字段覆盖度 ──
    populated = 0
    total_fields = 0
    for f in TEXT_FIELDS:
        total_fields += 1
        val = pj.get(f, "")
        if val and not is_placeholder(str(val)):
            populated += 1
    for f in LIST_FIELDS:
        total_fields += 1
        val = pj.get(f, [])
        if val and not is_list_placeholder(val):
            populated += 1
    # duration_days
    total_fields += 1
    if pj.get("duration_days") is not None and pj.get("duration_days") != "":
        populated += 1
    # note
    total_fields += 1
    if pj.get("note") and not is_placeholder(pj.get("note", "")):
        populated += 1

    field_coverage = round(populated / total_fields, 3) if total_fields else 0

    # ── 指标 4：降级检测 ──
    is_fallback = (not has_rag) or is_placeholder(pj.get("medication_adjustment", ""))

    # ── 证据来源多样性 ──
    guide_names = [c.get("guide", "") for c in citations if c.get("guide")]
    unique_guides = len(set(guide_names))

    # ── 随访周期 ──
    duration_days = pj.get("duration_days")

    # ── 每个字段的详细状态（给字段级报告用） ──
    field_detail = {}
    for f in TEXT_FIELDS:
        val = pj.get(f, "")
        field_detail[f] = "✅" if (val and not is_placeholder(str(val))) else "❌"
    for f in LIST_FIELDS:
        val = pj.get(f, [])
        field_detail[f] = "✅" if (val and not is_list_placeholder(val)) else "❌"
    field_detail["duration_days"] = "✅" if duration_days is not None else "❌"
    field_detail["note"] = "✅" if (pj.get("note") and not is_placeholder(pj.get("note", ""))) else "❌"

    return {
        "patient_id": plan.get("patient_id", "?"),
        "status": plan.get("status", "?"),
        "has_rag": has_rag,
        "citation_count": citation_count,
        "unique_guides": unique_guides,
        "citation_precision": citation_precision,
        "field_coverage": field_coverage,
        "is_fallback": is_fallback,
        "missing_refs": sorted(missing_refs) if missing_refs else [],
        "plan_refs_count": len(plan_refs),
        "duration_days": duration_days,
        "field_detail": field_detail,
    }


async def run_live_evaluation(num_patients=10):
    """新生成 N 份计划并实时评测（generate_draft 已为 async）"""
    from agents.planner import generate_draft
    from data.database import PatientDB

    db = PatientDB()
    all_patients = db.get_all_patients()

    if num_patients > len(all_patients):
        num_patients = len(all_patients)

    patients = all_patients[:num_patients]
    results = []

    print(f"正在为 {num_patients} 位患者生成计划...")
    for i, p in enumerate(patients):
        pid = p["patient_id"]
        try:
            draft = await generate_draft(pid, persist=False)
            plan_dict = {
                "patient_id": pid,
                "plan_json": draft.get("plan_json", {}),
                "guideline_citations": draft.get("citations", []),
                "status": "draft",
            }
            eval_result = evaluate_plan(plan_dict)
            results.append(eval_result)

            mark = "✅" if eval_result["has_rag"] else "❌"
            missing = f" 缺引用{ eval_result['missing_refs']}" if eval_result['missing_refs'] else ""
            print(f"  [{i+1}/{num_patients}] {mark} {pid} "
                  f"RAG={eval_result['citation_count']}条 "
                  f"字段={eval_result['field_coverage']:.0%}"
                  f"{' ⚠️降级' if eval_result['is_fallback'] else ''}"
                  f"{missing}")
        except Exception as e:
            print(f"  [{i+1}/{num_patients}] ❌ {pid} 失败: {e}")

    return results


def print_report(results, title="📊 RAG 质量评测报告"):
    """打印聚合报告"""
    n = len(results)
    if n == 0:
        print("无数据")
        return

    rag_count = sum(1 for r in results if r["has_rag"])
    fallback_count = sum(1 for r in results if r["is_fallback"])
    avg_precision = sum(r["citation_precision"] for r in results) / n
    avg_coverage = sum(r["field_coverage"] for r in results) / n
    avg_citations = sum(r["citation_count"] for r in results) / n
    avg_unique_guides = sum(r["unique_guides"] for r in results) / n
    total_missing = sum(len(r["missing_refs"]) for r in results)

    # 字段级覆盖明细（所有计划各字段的覆盖率）
    field_stats = Counter()
    for r in results:
        for f, ok in r["field_detail"].items():
            if ok == "✅":
                field_stats[f] += 1

    # 随访周期多样性
    durations = [r["duration_days"] for r in results if r.get("duration_days") is not None]

    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)
    print(f"  评估样本数:             {n}")
    print(f"  ───────────────────────────────────────")
    print(f"  🔍 RAG 检索召回率       {rag_count}/{n} = {rag_count/n:.1%}")
    print(f"      → 知识库命中率，100% = 所有计划都检索到了证据")
    print(f"  🎯 引用完整率           {avg_precision:.1%}")
    print(f"      → 计划中 [n] 都有定义，越低幻觉风险越高")
    print(f"  📎 平均引用数           {avg_citations:.1f} 条/计划")
    print(f"  📚 平均证据来源         {avg_unique_guides:.1f} 份指南/共识")
    print(f"  📋 字段覆盖度           {avg_coverage:.1%}")
    print(f"      → 计划中非占位字段占比，越低说明越依赖人工")
    print(f"  ⚠️  降级/占位计划       {fallback_count}/{n} = {fallback_count/n:.1%}")
    print(f"      → 因 LLM 失败或无证据回退模板的计划比例")
    print(f"  🚫 缺失引用标记总数     {total_missing}")
    print()

    # 字段级报告
    print(f"  ─── 各字段覆盖详情 ───")
    for f in TEXT_FIELDS + LIST_FIELDS + ["duration_days", "note"]:
        hit = field_stats.get(f, 0)
        pct = hit / n * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        label = {"frequency": "随访频次", "pain_type": "疼痛类型",
                 "medication_adjustment": "用药调整", "warning_threshold": "预警阈值",
                 "recheck_items": "复查项目", "health_education": "健康教育",
                 "lifestyle": "生活方式", "duration_days": "随访周期",
                 "note": "备注"}.get(f, f)
        print(f"    {label:12s} {bar} {hit}/{n}")

    print()

    # 随访周期多样性
    if durations:
        unique_dur = set(durations)
        print(f"  ─── 随访周期多样性 ───")
        print(f"  不同周期值: {len(unique_dur)} 种")
        print(f"  分布: {sorted(unique_dur)}")
        if len(unique_dur) <= 1:
            print(f"  ⚠️  所有计划周期相同，可能存在个性化不足")
        else:
            print(f"  ✅ 计划呈现差异化")
    else:
        print(f"  ⚠️  所有计划均无随访周期数据")
    print()

    # 降级计划明细
    fallback_patients = [r["patient_id"] for r in results if r["is_fallback"]]
    if fallback_patients:
        print(f"  ─── 降级计划明细 ───")
        for pid in fallback_patients[:15]:
            print(f"    · {pid}")
        if len(fallback_patients) > 15:
            print(f"    ... 共 {len(fallback_patients)} 份")
        print()

    print("=" * 60)
    print()


def main():
    parser = argparse.ArgumentParser(description="PainSmart RAG 质量评测")
    parser.add_argument("--live", action="store_true",
                        help="实时生成并评测（默认只评测存量数据）")
    parser.add_argument("--patients", type=int, default=10,
                        help="实时评测的患者数（默认 10）")
    parser.add_argument("--all", dest="all_patients", action="store_true",
                        help="为全部患者生成并评测（覆盖 --patients）")
    parser.add_argument("--limit", type=int, default=0,
                        help="评测存量计划时最多读取条数")
    args = parser.parse_args()

    if args.live:
        num = len(PatientDB().get_all_patients()) if args.all_patients else args.patients
        from data.database import PatientDB
        results = asyncio.run(run_live_evaluation(num))
        print_report(results, "📊 实时 RAG 质量评测报告")
    else:
        plans = load_plans(args.limit if args.limit else None)
        if not plans:
            print("数据库中没有计划。运行 python evaluate.py --live --patients 10 生成并评测。")
            return
        results = [evaluate_plan(p) for p in plans]
        print_report(results, f"📊 存量计划评测报告（{len(plans)} 份）")
        print("💡 运行 python evaluate.py --live --patients 10 可实时生成并评测")
        print()


if __name__ == "__main__":
    main()
