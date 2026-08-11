# `backend/data/` —— 数据层说明

本目录是 PainSmart 9.0 后端的数据层，负责 **两件事**：

1. **定义数据模型与连接**（运行时服务与播种脚本共用）：`models_mysql.py` + `db_config.py`。
2. **生成并写入基础病例数据**（一次性播种）：`constants.py` → `factories_mysql.py` → `clinical_llm.py` → `seed_mysql.py`。

另有运行时数据访问层 `database.py`（提供 `PatientDB` 等查询接口，被 A/B/C Agent 调用）。

---

## 一、文件清单与职责

| 文件 | 角色 | 主要职责 |
|---|---|---|
| `__init__.py` | 包标记 | 仅含文档字符串，标识本目录为「演示种子数据 — 患者定义 / Demo 数据初始化」包。 |
| `db_config.py` | 配置 | 从 `backend/.env` 读取 `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME`，拼出 SQLAlchemy 连接串 `mysql+pymysql://...`。改库连信息改 `.env` 即可，无需动代码。 |
| `constants.py` | 共享常量 | **所有「枚举 + 映射」的唯一真源**，被 `factories_mysql.py`、`database.py` 共用，保证口径一致。含：24 类诊断池 `DIAGNOSES`、4 类疼痛机制 `PAIN_TYPES`、疼痛部位模板 `PAIN_LOCATION`、诊断→疼痛类型/手术/带药 三张映射表、科室/职称/医师风格/渠道池，以及固定随机种子 `SEED_RANDOM=20240618`。 |
| `models_mysql.py` | ORM 模型 | 21 张 SQLAlchemy 表的声明式定义（见下表），是播种器、运行时服务、Agent 共同的表结构来源。 |
| `factories_mysql.py` | 工厂（底层基础信息） | 用 `Faker('zh_CN')` + `PolyFactory` + **固定种子** 声明式生成「底层基础信息」：人口学、枚举、分数、日期、ID 等。枚举/JSON 字段用 `Use(...)` 约束；姓名按性别生成（PostGenerated）。外键列由播种器在 `build(kw=...)` 时覆盖。 |
| `clinical_llm.py` | 生成器（上层病例叙述） | 用 LLM（经 `llm.model.achat_completion`）生成「基础病例叙述」：主诉/现病史/既往史/诊断详述/治疗小结/出院小结/出院医嘱/疼痛部位，Pydantic schema 约束结构化输出。**LLM 不可用或出错时自动降级**到 `factories_mysql` 的模板生成器，保证离线也能播种。 |
| `seed_mysql.py` | 播种编排器 | 入口脚本。按 FK 依赖顺序把基础数据写入 MySQL：`python -m data.seed_mysql [--patients N] [--reset]`。下层用 `factories_mysql`，上层用 `clinical_llm`，并发生成全部患者叙述后落库。 |
| `database.py` | 运行时数据访问层 | 提供 `PatientDB` 等查询接口（被 A/B/C Agent 调用）。`_patient_to_dict` 把多表行映射成前端/Planner 用的患者字典（含出院叙述字段）。`init_db()` 建全部 21 张表，并在 Patient 表为空时自动触发基础数据播种。 |

---

## 二、分层生成架构（参照 virtual-hospital-agents 的设计）

```
                ┌─────────────────────────────────────────────┐
   底层基础信息  │  factories_mysql.py  (Faker + PolyFactory)   │
   (枚举+随机选) │  人口学 / 枚举 / 分数 / 日期 / ID —— 确定可复现 │
                └───────────────┬─────────────────────────────┘
                                │  _build_profile() 汇总成「底层上下文」
                                ▼
                ┌─────────────────────────────────────────────┐
   上层病例叙述  │  clinical_llm.py  (LLM 生成 + 模板降级)        │
   (交给大模型)  │  主诉/现病史/既往史/诊断详述/治疗小结/         │
                │  出院小结/出院医嘱/疼痛部位 —— 结构化 JSON      │
                └───────────────┬─────────────────────────────┘
                                │  generate_patient_clinical()
                                ▼
                ┌─────────────────────────────────────────────┐
   播种落库      │  seed_mysql.py  (FK 依赖顺序写入)             │
                │  科室/医生/人设/患者/入出院/疼痛日记/用药/     │
                │  渠道/HIS同步 —— **仅基础病例数据**            │
                └─────────────────────────────────────────────┘
```

---

## 三、⚠️ 职责边界（播种器 vs 运行时 Agent）

> 2026-07-30 与用户确认：**播种器只生成「基础病例数据」，不碰随访生命周期。**

| 数据类别 | 由谁生成 | 落库表 |
|---|---|---|
| 科室 / 医生 / 医师人设 | 播种器 | `departments` / `doctors` / `physician_personas` |
| 患者 / 入出院记录 | 播种器 | `patients` / `admissions` / `discharge_records` |
| 疼痛日记 / 用药记录 / 患者渠道 / HIS 同步日志 | 播种器（patient 级，不依赖会话） | `pain_diaries` / `medication_records` / `patient_channels` / `his_sync_logs` |
| 随访计划 | **运行时 A-planner**（前端点击 `POST /api/plans/generate`） | `followup_plans` |
| 随访会话 / 对话记录 | **运行时 C-Agent（执行）** | `followup_sessions` / `followup_records` |
| 风险评估 | **运行时 B/R-Agent** | `risk_assessments` |
| 评审 / 预警 / 转人工 / 情绪 / 多模态 / 通话 / 护栏 | 运行时各 Agent | `doctor_reviews` / `alerts` / `human_handoffs` / `emotion_records` / `multimodal_assets` / `call_records` / `guardrail_events` |

播种器通过 `seed_mysql.py` 的 `Base.metadata.create_all` **仍会建全部 21 张表**（schema 给运行时 Agent 用），但**不预置**任何随访生命周期行，避免与运行时 Agent 重复造数。

---

## 四、`models_mysql.py` 中的 21 张表

| 表名 | 说明 |
|---|---|
| `departments` | 科室 |
| `doctors` | 医生（含科室、职称） |
| `patients` | 患者（人口学、免随访标记） |
| `admissions` | 入院记录（主诉/现病史/既往史、FK 医生/科室） |
| `discharge_records` | 出院记录（诊断/出院小结/出院医嘱/带药/疼痛部位，FK 患者/入院/医生） |
| `followup_plans` | 随访计划（A-planner 生成，FK 患者/出院） |
| `followup_sessions` | 随访会话（FK 计划） |
| `followup_records` | 随访对话/记录（FK 会话） |
| `risk_assessments` | 风险评估（FK 会话/患者） |
| `doctor_reviews` | 医生评审（FK 计划） |
| `alerts` | 预警事件（FK 患者/会话） |
| `pain_diaries` | 疼痛日记（FK 患者） |
| `medication_records` | 用药记录（FK 患者/出院） |
| `patient_channels` | 患者触达渠道（FK 患者） |
| `human_handoffs` | 转人工工单（FK 患者/会话） |
| `emotion_records` | 情绪识别记录（FK 会话） |
| `multimodal_assets` | 多模态素材（FK 会话/患者） |
| `call_records` | 通话记录（FK 会话） |
| `physician_personas` | 医师人设/风格（FK 医生） |
| `guardrail_events` | 安全护栏事件（FK 会话/患者） |
| `his_sync_logs` | HIS 同步日志（FK 患者） |

---

## 五、关键设计点

- **可复现**：`constants.SEED_RANDOM = 20240618` 同时喂给 `Faker.seed` 与 `random.seed`，每次播种姓名/诊断/风险分布一致。
- **姓名随性别**：`PatientFactory` 用 `PostGenerated` 在性别确定后选 `name_male()` / `name_female()`，保证姓名与性别一致。
- **LLM 降级**：`clinical_llm.py` 在 `is_llm_available()` 为 False 或调用异常/非法 JSON 时，自动回退模板生成，Demo 永不中断。
- **出院叙述闭环**：`database.py._patient_to_dict` 把 `discharge_records` 的叙述字段（`discharge_summary` / `discharge_instructions` / `diagnosis_detail` / `treatment_summary`）导出到患者字典，使 `agents/planner.py` 的 A-planner 在生成随访计划时能真正用到出院小结；`_retrieve`（B-Agent）的 RAG 检索串也拼入了出院小结。
- **外键覆盖**：工厂本身不保证外键引用存在；所有 `patient_id/doctor_id/admission_id/...` 均在 `seed_mysql.py` 用 `build(kw=...)` 显式覆盖，按依赖顺序写入。

---

## 六、常用命令

```bash
# 默认播种 50 名患者的基础病例数据（含 LLM 病例叙述；LLM 不可用时降级）
python -m data.seed_mysql

# 自定义患者数
python -m data.seed_mysql --patients 200

# 先清空全部 21 张表再写入（慎用）
python -m data.seed_mysql --reset
```

> 注：运行前需在 `backend/.env` 配置好 `DB_*` 与 `LLM_*`；播种脚本依赖 `sqlalchemy / polyfactory / faker / pymysql / python-dotenv / openai`。
