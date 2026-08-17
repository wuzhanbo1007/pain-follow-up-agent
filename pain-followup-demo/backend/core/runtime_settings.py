# backend/core/runtime_settings.py
"""运行时配置（说明书 5.2 / §6：手动演示患者来自外部结构化配置）。

从 config/followup_runtime.yaml 读取医护可调参数，注入 AppContext.settings：
  - manual_patient_ids：手动演示患者 ID 列表。分流规则（§11-11 / §12.2）：
      当天 send_roster ∩ manual_patient_ids → 手动演示（input_source=human）
      当天 send_roster - manual_patient_ids → 自动演示（input_source=simulator）
      配置中的 ID 不在当天 send_roster 中 → 忽略并记录日志，不强制纳入名单。
  - prefill_skip_ids：启动预填充随访计划草稿时跳过的患者（默认 = manual_patient_ids）。

遵循"配置文件只承载规则/参数、名单由规则引擎判定"的原则（§6），
本模块只读参数列表，不决定任何名单。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@dataclass
class RuntimeSettings:
    """演示环境运行时参数（全部可从 config/followup_runtime.yaml 覆盖）。"""
    manual_patient_ids: list[int] = field(default_factory=list)
    prefill_skip_ids: list[int] = field(default_factory=list)
    # 从 phone_callback_policy.natural_language 解析出的连续未回复天数
    phone_callback_threshold_days: int = 3
    # Checkpointer 后端（§6）：memory（进程内演示）| postgres（跨进程恢复）
    checkpointer_backend: str = "memory"
    checkpointer_dsn: str = ""


def _config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "followup_runtime.yaml"


def _as_int_list(values) -> list[int]:
    out: list[int] = []
    for v in values or []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def load_runtime_settings(config_path: str | Path | None = None) -> RuntimeSettings:
    """读取 followup_runtime.yaml 中的运行时参数；文件缺失或解析失败时返回空默认值。"""
    path = Path(config_path) if config_path else _config_path()
    if not path.exists() or yaml is None:
        return RuntimeSettings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return RuntimeSettings()
    if not isinstance(data, dict):
        return RuntimeSettings()
    manual = _as_int_list(data.get("manual_patient_ids"))
    prefill = _as_int_list(data.get("prefill_skip_ids")) or list(manual)
    callback = data.get("phone_callback_policy") or {}
    natural_language = str(callback.get("natural_language") or "")
    threshold_match = re.search(
        r"(\d+)\s*天[^\n]{0,20}(?:未|没)(?:有)?回复", natural_language)
    threshold = int(threshold_match.group(1)) if threshold_match else 3
    threshold = max(1, threshold)
    cp = data.get("checkpointer") or {}
    return RuntimeSettings(
        manual_patient_ids=manual,
        prefill_skip_ids=prefill,
        phone_callback_threshold_days=threshold,
        checkpointer_backend=str(cp.get("backend") or "memory"),
        checkpointer_dsn=str(cp.get("dsn") or ""),
    )
