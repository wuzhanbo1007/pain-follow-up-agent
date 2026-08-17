# backend/core/realtime.py
"""
实时通信桥 — 让 Agent 包在不循环依赖 app.py 的前提下，
通过 WebSocket 向外推送事件（plan:drafted / review:session_ready 等）。

双上下文安全：
  - 主 event loop 在 WebSocket connect 时懒捕获，存为 _main_loop
  - emit() 同时支持两种调用方：
      * 跨线程（如旧版守护线程）：用 run_coroutine_threadsafe 投递到主 loop
      * 主 loop 内（当前 async 随访管线直接 produce 事件）：用 ensure_future 就地排期
    二者都不阻塞调用方。
"""
import asyncio

_sio = None
_main_loop = None


def set_socketio(s):
    global _sio
    _sio = s


def capture_loop():
    """在事件循环上下文中调用（如 WebSocket connect），捕获主 event loop。"""
    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass


def emit(envelope):
    """双上下文安全的 emit 封装。入参为事件总线产出的类型化事件包
    ``{"type": <事件名>, "data": <负载>}``；此处是唯一与 Socket.IO 耦合的地方。

    调用方可能来自：
      1) 主 event loop 内的 asyncio.Task（当前 async 随访管线）——直接 ensure_future 排期，不阻塞；
      2) 其他线程——run_coroutine_threadsafe 投递到主 loop。
    """
    if _sio is None or _main_loop is None:
        return
    try:
        event = envelope.get("type")
        data = envelope.get("data", {})
        if not event:
            return
        coro = _sio.emit(event, data)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop is _main_loop:
            # 调用方就在主 event loop 内：就地排期，避免 run_coroutine_threadsafe 的跨线程假设
            asyncio.ensure_future(coro)
        else:
            # 跨线程：安全投递到主 loop
            asyncio.run_coroutine_threadsafe(coro, _main_loop)
    except Exception:
        pass
