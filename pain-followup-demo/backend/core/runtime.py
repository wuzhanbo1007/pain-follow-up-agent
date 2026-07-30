"""
运行时共享状态（从 app.py 抽离）

app.py 以 `python app.py` 运行时，其模块名是 `__main__`；若子模块用
`import app` / `from app import ...` 反向引用，会触发 app.py 二次加载，
造成状态分裂与循环依赖。因此把所有跨模块共享的可变状态集中到本模块，
由 app.py（bootstrap）在启动时填充，routes / ws 等子模块统一从这里读取。
"""
from typing import Any, Optional

# ===== 由 core.bootstrap.bootstrap() 在启动时填充 =====
db = None
PATIENTS: list = []
REPLIES: dict = {}
followup_service: Any = None
risk_engine: Any = None
reply_parser: Any = None
message_generator: Any = None
auto_reply_engine: Any = None

# ===== WebSocket 消息缓存（broadcast 与 ws 事件处理函数共享同一可变对象）=====
wechat_msg_cache: list = []
demo_patient_state: Any = None

_cache_max = 100


def broadcast(envelope):
    """通过 WebSocket 广播类型化事件包，同时缓存微信消息供 ChatPage 同步。

    envelope: {"type": <事件名>, "data": <负载>}
    作为 EventBus 的 emit_callback 注入；传输相关逻辑（缓存 / 推送）集中在此处。
    """
    global wechat_msg_cache, demo_patient_state
    event = envelope.get("type")
    data = envelope.get("data", {})
    if event == "agent:wechat_sent":
        wechat_msg_cache.append(data)
        if len(wechat_msg_cache) > _cache_max:
            wechat_msg_cache = wechat_msg_cache[-_cache_max:]
    elif event == "demo:patients_selected":
        demo_patient_state = data
    from core.realtime import emit as _emit
    _emit(envelope)
