# backend/run_tests.py
"""轻量测试运行器（无 pytest 依赖）。

发现 tests/test_*.py 中的 test_* 函数（同步与 async），执行并汇总。
async 测试用 asyncio.run 执行；fixture offline_ctx 手动构造。
"""
import asyncio
import importlib
import pkgutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tests


def make_offline_ctx():
    from infrastructure.runtime_context import AppContext, set_context
    from domain.models.patient import PatientSnapshot

    class OfflineGW:
        @staticmethod
        def is_llm_available(): return False
        async def chat(self, *a, **k): raise RuntimeError("offline")
        async def chat_json(self, *a, **k): return {}

    class FakeRepo:
        def __init__(self):
            self._p = {
                "7": {"patient_id": "7", "name": "李叔叔", "age": 65, "diagnosis": "癌痛",
                      "pain_type": "神经痛", "followup_mode": "auto",
                      "discharge_date": "2026-07-01", "duration_days": 90,
                      "follow_up_plan": {"frequency": "每日"}, "history": [{"nrs_score": 5}],
                      "consecutive_no_reply_days": 0},
                "8": {"patient_id": "8", "name": "张阿姨", "age": 58, "diagnosis": "带状疱疹后神经痛",
                      "pain_type": "神经痛", "followup_mode": "auto",
                      "discharge_date": "2026-07-01", "duration_days": 90,
                      "follow_up_plan": {"frequency": "每日"}, "history": [{"nrs_score": 3}],
                      "consecutive_no_reply_days": 3},
            }
        def get_all_patients(self): return [dict(p) for p in self._p.values()]
        def get_patient(self, pid): return dict(self._p.get(str(pid))) if str(pid) in self._p else None
        def get_history(self, pid, d): return self._p.get(str(pid), {}).get("history", [])
        def freeze_patient_snapshot(self, p, *, input_source="simulator"):
            p = dict(p); p.setdefault("history", self.get_history(p.get("patient_id"), 7))
            return PatientSnapshot(patient_id=str(p.get("patient_id")), name=p.get("name", "患者"),
                                   age=p.get("age"), diagnosis=p.get("diagnosis", ""),
                                   pain_type=p.get("pain_type", ""),
                                   input_source="human" if input_source == "human" else "simulator",
                                   discharge_date=p.get("discharge_date"),
                                   duration_days=p.get("duration_days", 90),
                                   follow_up_plan=p.get("follow_up_plan") or {},
                                   history=p.get("history", []))
        def snapshot_to_runtime_dict(self, s): return s.model_dump()

    ctx = AppContext().ensure_defaults()
    ctx.llm_gateway = OfflineGW()
    ctx.patient_repository = FakeRepo()
    set_context(ctx)
    return ctx


def run_module(mod_name):
    mod = importlib.import_module(mod_name)
    passed = failed = 0
    failures = []
    for name in dir(mod):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        # 简单 fixture：asyncio 标记的 async 函数；offline_ctx fixture 手动注入
        import inspect
        sig = inspect.signature(fn)
        kwargs = {}
        for p in sig.parameters:
            if p == "offline_ctx":
                kwargs[p] = make_offline_ctx()
        try:
            if inspect.iscoroutinefunction(fn):
                asyncio.run(fn(**kwargs))
            else:
                fn(**kwargs)
            passed += 1
            print(f"  PASS {mod_name}.{name}")
        except Exception as e:
            failed += 1
            failures.append((mod_name, name, traceback.format_exc()))
            print(f"  FAIL {mod_name}.{name}: {e}")
    return passed, failed, failures


def main():
    test_files = [
        "tests.test_callback_policy",
        "tests.test_roster_decider",
        "tests.test_risk_evaluator",
        "tests.test_prompt_registry",
        "tests.test_graphs",
    ]
    total_pass = total_fail = 0
    all_failures = []
    for m in test_files:
        print(f"\n=== {m} ===")
        p, f, fails = run_module(m)
        total_pass += p; total_fail += f
        all_failures.extend(fails)
    print(f"\n{'='*50}")
    print(f"合计: {total_pass} 通过, {total_fail} 失败")
    if all_failures:
        print("\n失败详情:")
        for mod, name, tb in all_failures:
            print(f"\n--- {mod}.{name} ---\n{tb}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
