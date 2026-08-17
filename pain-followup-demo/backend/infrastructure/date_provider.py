# backend/infrastructure/date_provider.py
"""DateProvider —— 业务日期提供方（说明书 5.2 / 4.2）。

演示阶段固定 DEMO_TODAY；生产环境由 DateProvider 在运行时一次性解析业务日期，
写入 DispatchState.business_date，保证整次调度一致、可审计，不散落 new Date() / date.today()。

§4.1 / §12.1：演示环境必须配置 DEMO_TODAY。缺失或格式错误直接抛错，
不允许静默回退到服务器当天日期（避免配置遗漏后悄悄使用系统日期）。
真实生产日期暂不接入，DateProvider 仅作为后续生产环境的替换接口。
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

try:
    from core.config import DEMO_TODAY
except Exception:
    DEMO_TODAY = ""

# 业务时区（说明书 4.1 evaluation_time / timezone）
BUSINESS_TIMEZONE = "Asia/Shanghai"
# 东八区固定偏移（避免依赖 zoneinfo 在受限环境下的可用性；仅用于事件时间戳 now()）
_TZ_OFFSET = timezone(timedelta(hours=8))


class DateProvider:
    """业务日期提供方。"""

    def get_business_date(self) -> date:
        """返回业务日期。演示阶段只读 DEMO_TODAY，不接入系统真实日期。

        §4.1 启动校验：DEMO_TODAY 缺失或格式错误时直接报配置错误，
        而不是静默使用服务器当天日期。
        """
        if not DEMO_TODAY:
            raise RuntimeError("演示环境必须配置 DEMO_TODAY")
        try:
            return date.fromisoformat(DEMO_TODAY)
        except ValueError as exc:
            raise RuntimeError(
                "DEMO_TODAY 必须使用 YYYY-MM-DD 格式"
            ) from exc

    def get_today(self) -> date:
        return self.get_business_date()

    def now(self) -> datetime:
        """事件/审阅时间戳（业务时区墙钟）。仅用于审计时间戳，不是业务日期。"""
        return datetime.now(_TZ_OFFSET)

    def iso(self) -> str:
        return self.get_business_date().isoformat()


# 模块级便捷函数（与实例方法同语义：缺失 DEMO_TODAY 直接抛错）
def get_business_date() -> date:
    return DateProvider().get_business_date()


def get_today() -> date:
    return get_business_date()
