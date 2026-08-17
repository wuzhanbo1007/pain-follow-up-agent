# backend/infrastructure/repositories/errors.py
"""仓储错误约定（Agent-centric 重构 §11）。

禁止 ``except Exception: return None`` 掩盖失败；显式区分三种失败：
  - NotFoundError      找不到（查询/恢复目标不存在）
  - ConflictError      冲突（唯一键/幂等键已存在，或状态不允许当前操作）
  - PersistenceError   持久化失败（数据库/存储写入异常）
"""
from __future__ import annotations


class NotFoundError(Exception):
    """找不到目标记录。"""


class ConflictError(Exception):
    """冲突：唯一键/幂等键已存在，或目标状态不允许当前操作。"""


class PersistenceError(Exception):
    """持久化失败：底层存储写入/读取异常。"""
