# backend/infrastructure/__init__.py
"""基础设施层（infrastructure/）—— 存储、恢复、事件与模型调用，不含业务路由。

依赖方向：api → graphs → nodes → agents → prompts / infrastructure
（domain 被所有层读取但不依赖上层）。
"""
