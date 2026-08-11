"""
WebSocket 事件处理函数（从 app.py 迁出，纯搬家，行为不变）

通过 register_ws_events(sio) 挂载到 Socket.IO 服务；共享状态从 core.runtime 读取，
避免反向 import app（app 以 __main__ 运行时 import app 会二次加载）。
"""
import asyncio
from datetime import datetime

from core import runtime, config
from core.logging_config import get_logger

log = get_logger("painsmart.ws")


def register_ws_events(sio):
    @sio.event
    async def connect(sid, environ):
        from core.realtime import capture_loop
        capture_loop()  # ★ 捕获主事件循环，使后续后台线程 emit 可用
        log.debug("WebSocket 客户端已连接: %s", sid)
        await sio.emit("agent:connected", {
            "status": "ok",
            "total_patients": len(runtime.PATIENTS),
            "state": runtime.followup_service.state,
            "db_type": "mysql", "db_name": config.DB_NAME,
        }, to=sid)

    @sio.on("chat:request_sync")
    async def handle_chat_sync(sid):
        """ChatPage 连接后主动请求同步：补发已错过的 demo 患者信息 + 历史消息"""
        if runtime.demo_patient_state:
            await sio.emit("demo:patients_selected", runtime.demo_patient_state, to=sid)

        for msg in runtime.wechat_msg_cache:
            await sio.emit("agent:wechat_sent", msg, to=sid)

    @sio.on("demo:start_followup")
    async def handle_start_followup(sid, data):
        runtime.wechat_msg_cache.clear()
        runtime.demo_patient_state = None
        log.info("开始今日随访演示")
        # run_daily_followup 内部通过 asyncio.create_task 启动后台随访任务，立即返回
        return runtime.followup_service.run_daily_followup()

    @sio.on("demo:simulate_reply")
    async def handle_simulate_reply(sid, data):
        patient_id = data.get("patient_id")
        patient = runtime.db.get_patient(patient_id) or {}

        explicit_text = data.get("reply_text")
        if explicit_text:
            reply_text = explicit_text
        else:
            reply_data = runtime.REPLIES.get(patient_id) or runtime.db.get_reply(patient_id)
            reply_text = reply_data.get("reply_text", "今天还好") if reply_data else "今天还好"

        await sio.emit("patient:reply", {
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "text": reply_text,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })
        # on_patient_reply 已是全链路 async（await LLM / await asyncio.sleep），
        # 作为独立 asyncio.Task 在事件循环上并发执行，不会冻结其他 WebSocket 推送。
        asyncio.create_task(
            runtime.followup_service.on_patient_reply(patient_id, reply_text)
        )

    @sio.on("demo:trigger_error")
    def handle_trigger_error(sid, data):
        error_type = data.get("type", "no_reply_3days")
        runtime.followup_service.trigger_exception(error_type)

    @sio.on("demo:skip_step")
    def handle_skip_step(sid):
        states = ["FETCHING", "FILTERING", "GENERATING", "SENDING", "WAITING_REPLY", "PARSING", "SCORING", "IDLE"]
        current = runtime.followup_service.state
        idx = states.index(current) if current in states else 0
        next_state = states[min(idx + 1, len(states) - 1)]
        runtime.followup_service.transition_to(next_state)
        runtime.followup_service.event_bus.log("手动跳过当前步骤")

    @sio.on("demo:reset")
    async def handle_reset(sid):
        runtime.wechat_msg_cache.clear()
        runtime.demo_patient_state = None
        runtime.followup_service.reset()
        await sio.emit("agent:reset", {"timestamp": datetime.now().strftime("%H:%M:%S")})

    @sio.on("demo:get_patient_list")
    async def handle_get_patient_list(sid):
        reply_patients = []
        for pid in runtime.REPLIES:
            p = runtime.db.get_patient(pid)
            if p:
                reply_patients.append({
                    "patient_id": pid,
                    "name": p["name"],
                    "diagnosis": p.get("diagnosis", ""),
                    "reply_preview": runtime.REPLIES[pid].get("reply_text", "")[:20] + "...",
                    "risk_tendency": runtime.REPLIES[pid].get("risk_tendency", ""),
                })
        await sio.emit("demo:patient_list", reply_patients, to=sid)
