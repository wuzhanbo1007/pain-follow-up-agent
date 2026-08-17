# backend/infrastructure/repositories/plan_repository.py
"""计划仓储（独立文件，复用 FollowupRepository 的计划方法）。"""
from infrastructure.repositories.followup_repository import PlanRepository

__all__ = ["PlanRepository"]
