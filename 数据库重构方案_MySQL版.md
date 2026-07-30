# PainSmart 9.0 数据库重构方案 —— MySQL 版

> **目标**：SQLite → MySQL 迁移，规范化表结构，支持 RAG 驱动的随访计划生成，使用 Faker 批量生成仿真数据。
> **日期**：2026-07-29（初版）/ 2026-07-30（v6.0 产品需求对齐升级）
> **参考基线**：`架构设计_v5.0_多Agent架构.md` / `Demo架构设计.md` / `README.md` / `产品设计优化思路_v6.0.docx`

> **v6.0 升级说明**：对照《产品设计优化思路 v6.0》九项需求，原有 13 张表新增 8 张表（共 21 张），5 张现有表追加字段或修改枚举，覆盖多渠道接入、语音/图像多模态、情绪感知、转人工协同、电话外呼、安全护栏、医师风格画像、HIS 对接等全部新需求。

---

## 一、现版 SQLite 的问题分析

| # | 问题 | 影响 |
|---|------|------|
| 1 | **`patients` 表臃肿** — 人口学、临床诊断、随访计划、运行时状态全塞一张表 | 职责不清，字段越多越难维护 |
| 2 | **出院小结不是独立实体** — `discharge_summary` 作为 `followup_plan` 的一个字段，出院后无法独立查询 | RAG 检索的输入源没有落脚点 |
| 3 | **无入院记录** — 诊断直接挂在患者表，缺少入院→出院的临床路径追溯 | 无法区分入院诊断和出院诊断 |
| 4 | **医生/科室硬编码** — `doctor_id`/`doctor_name` 是字符串常量，无关联表 | 医生信息无法管理、无法扩展 |
| 5 | **`preset_replies` 是 Demo 产物** — 生产环境不需要预置回复 | 混淆了 Demo 逻辑和业务数据 |
| 6 | **`followup_history` 维度单一** — 只有 NRS/睡眠/用药，缺少副作用、情绪、活动能力 | 风险评估缺乏足够数据支撑 |
| 7 | **缺少疼痛日记** — 慢痛管理核心数据源缺失 | 无法做长期疼痛趋势分析 |
| 8 | **缺少用药记录表** — 用药信息散落在各处 | 用药依从性分析无数据基础 |
| 9 | **缺少预警闭环表** — 高风险事件没有追踪 | 医生处理了还是没处理无法审计 |
| 10 | **所有"结构化数据"以 JSON TEXT 存** — SQLite 无法利用 JSON 索引，MySQL 同样浪费 JSON 列优势 | 查询效率低，无法做聚合统计 |
| 11 | **缺少多渠道接入** — v6.0 需求微信/企微/电话/APP/H5 多渠道，无渠道绑定表 | 渠道决策无数据基础 |
| 12 | **缺少多模态资源** — 语音/图片无处存储，ASR/VLM 分析结果无落脚点 | 语音和图像功能无法落地 |
| 13 | **缺少转人工记录** — 专业问题转人工事件无独立表 | 上下文移交包无处存储，无法审计 |
| 14 | **缺少情绪感知** — `mood_status` 仅一个 VARCHAR，无法存多标签打分+危机等级 | 情绪轨迹分析无数据支撑 |
| 15 | **缺少安全护栏审计** — 越界检测/紧急事件/护栏拦截无独立表 | AI 行为边界无法审计 |
| 16 | **`medication_adherence` 类型硬伤** — TINYINT(1) 只能存 0/1，无法表达 V11.0 prompt 的 `"partial"` | 用药依从性数据丢失 |

---

## 二、重构目标

1. **领域建模规范化** — 一张表只做一件事，关系通过 FK 表达
2. **RAG 工作流原生支持** — 出院小结作为独立实体，上下游数据链路清晰
3. **Faker 友好** — 字段类型简单，便于批量生成中文仿真数据
4. **MySQL 适配** — InnoDB 引擎、合理索引、外键约束
5. **v6.0 全需求覆盖** — 多渠道、多模态、情绪、转人工、护栏、HIS 对接
6. **Prompt V11.0 对齐** — `followup_records` 字段完全对齐 V11.0 解析输出
7. **可扩展** — 后续可加入评估量表、患者自报结局等模块

---

## 三、ER 关系总览

```
┌──────────┐       ┌──────────────┐       ┌────────────────────┐
│departments│◄──────│   doctors    │──────►│ physician_personas │
│  科室     │ 1:N   │   医生       │ 1:1   │   医师风格画像 ★新增│
└──────────┘       └──────┬───────┘       └────────────────────┘
                          │ 1:N
                          ▼
┌──────────┐       ┌──────────────┐       ┌──────────────────┐
│ patients │──────►│  admissions  │──────►│ discharge_records│
│  患者    │ 1:N   │  入院记录    │ 1:1   │  出院记录 ★核心   │
└────┬─────┘       └──────────────┘       └────────┬─────────┘
     │                                              │ 1:1
     │                         ┌────────────────────┘
     │                         ▼
     │              ┌──────────────────┐
     │              │  followup_plans  │──── RAG 检索 ────► ChromaDB 向量库
     │              │   随访计划       │
     │              └────────┬─────────┘
     │                       │ 1:N
     │                       ▼
     │  ┌────────────────────────────────────────────────────────┐
     │  │              followup_sessions  随访会话                 │
     │  │  +channel +voice_mode +escalation_status                 │
     │  └──┬──────────┬──────────┬──────────┬──────────┬──────────┘
     │     │ 1:N      │ 1:N      │ 1:N      │ 1:N      │ 1:N
     │     ▼          ▼          ▼          ▼          ▼
     │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐
     │  │followup│ │  risk  │ │ doctor │ │emotion │ │multimodal  │
     │  │records │ │ assess │ │reviews │ │records │ │  assets    │
     │  │+V11.0  │ │  ments │ │        │ │ ★新增  │ │  ★新增     │
     │  └───┬────┘ └────────┘ └────────┘ └────────┘ └────────────┘
     │      │
     │      ├─────────────► human_handoffs (转人工 ★新增)
     │      └─────────────► guardrail_events (护栏 ★新增)
     │
     ├─────────────► patient_channels (渠道绑定 ★新增)
     ├─────────────► call_records (电话外呼 ★新增)
     ├─────────────► pain_diaries (疼痛日记)
     ├─────────────► medication_records (用药记录)
     ├─────────────► alerts (预警记录 +5枚举)
     └─────────────► his_sync_logs (HIS同步 ★新增)
```

---

## 四、完整表结构设计（21 张表）

> **表结构总览**：
>
> | # | 表名 | 状态 | v6.0需求 |
> |---|------|------|---------|
> | 4.1 | `departments` | 已有 | — |
> | 4.2 | `doctors` | **+3字段** | 需求7 医师风格 |
> | 4.3 | `patients` | **+6字段** | 需求4/6 渠道+外呼 |
> | 4.4 | `admissions` | 已有 | — |
> | 4.5 | `discharge_records` | 已有 | — |
> | 4.6 | `followup_plans` | 已有 | — |
> | 4.7 | `followup_sessions` | **+3字段** | 需求1/2/4 渠道+语音+转人工 |
> | 4.8 | `followup_records` | **+8字段/改1枚举** | Prompt V11.0 + 需求2/5/9 |
> | 4.9 | `risk_assessments` | 已有 | — |
> | 4.10 | `doctor_reviews` | 已有 | — |
> | 4.11 | `alerts` | **+5枚举值** | 需求3/6/8/9 |
> | 4.12 | `pain_diaries` | 已有 | — |
> | 4.13 | `medication_records` | 已有 | — |
> | 4.14 | `patient_channels` | **★新增** | 需求4/6 渠道网关 |
> | 4.15 | `human_handoffs` | **★新增** | 需求1 转人工 |
> | 4.16 | `emotion_records` | **★新增** | 需求3 情绪感知 |
> | 4.17 | `multimodal_assets` | **★新增** | 需求2/5 语音+图像 |
> | 4.18 | `call_records` | **★新增** | 需求6 电话外呼 |
> | 4.19 | `physician_personas` | **★新增** | 需求7 医师风格画像 |
> | 4.20 | `guardrail_events` | **★新增** | 需求9 安全护栏 |
> | 4.21 | `his_sync_logs` | **★新增** | 需求8 HIS对接 |

### 4.1 `departments` — 科室

```sql
CREATE TABLE departments (
    department_id   INT AUTO_INCREMENT PRIMARY KEY,
    name            VARCHAR(64)  NOT NULL COMMENT '科室名称',
    description     TEXT         COMMENT '科室描述',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科室';
```

| 字段 | 类型 | 说明 | Faker 策略 |
|------|------|------|-----------|
| `department_id` | INT PK | 自增主键 | — |
| `name` | VARCHAR(64) | 如"疼痛科""骨科""神经内科""肿瘤科""康复科" | 从预定义列表随机抽取 |
| `description` | TEXT | 科室简介 | Faker `text()` |

---

### 4.2 `doctors` — 医生

```sql
CREATE TABLE doctors (
    doctor_id            INT AUTO_INCREMENT PRIMARY KEY,
    department_id        INT          NOT NULL COMMENT '所属科室',
    name                 VARCHAR(32)  NOT NULL COMMENT '医生姓名',
    title                VARCHAR(32)  COMMENT '职称（主治/副主任/主任医师）',
    specialty            VARCHAR(128) COMMENT '专长',
    phone                VARCHAR(20)  COMMENT '联系电话',
    email                VARCHAR(64)  COMMENT '邮箱',
    is_active            TINYINT(1)   DEFAULT 1 COMMENT '是否在职',
    -- v6.0 新增：医师风格画像 + 声纹克隆
    persona_id           INT          COMMENT '关联医师风格画像',
    voice_print_consent  TINYINT(1)   DEFAULT 0 COMMENT '声纹克隆书面授权',
    voice_print_status   ENUM('none','cloned','disabled') DEFAULT 'none' COMMENT '声纹克隆状态',
    created_at           DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (department_id) REFERENCES departments(department_id),
    FOREIGN KEY (persona_id) REFERENCES physician_personas(persona_id),
    INDEX idx_doctor_persona (persona_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `name` | 中文姓名 | `faker.name()` |
| `title` | 职称 | 从 `['主治医师','副主任医师','主任医师']` 随机抽取 |
| `specialty` | 专长 | 如"慢性疼痛""神经阻滞""癌痛管理" |
| `phone` | 电话 | `faker.phone_number()` |
| `email` | 邮箱 | `faker.email()` |
| `persona_id` | 关联风格画像 | 可空，P3 阶段填充 |
| `voice_print_consent` | 声纹授权 | 默认 0 |
| `voice_print_status` | 声纹状态 | 默认 'none' |

---

### 4.3 `patients` — 患者

```sql
CREATE TABLE patients (
    patient_id             INT AUTO_INCREMENT PRIMARY KEY,
    name                   VARCHAR(32)  NOT NULL COMMENT '姓名',
    gender                 ENUM('男','女') NOT NULL COMMENT '性别',
    birth_date             DATE         NOT NULL COMMENT '出生日期',
    phone                  VARCHAR(20)  COMMENT '联系电话',
    id_card_hash           VARCHAR(64)  COMMENT '身份证号哈希（脱敏）',
    address                VARCHAR(256) COMMENT '住址',
    emergency_contact      VARCHAR(32)  COMMENT '紧急联系人',
    emergency_phone        VARCHAR(20)  COMMENT '紧急联系电话',
    -- v6.0 新增：渠道 + 外呼 + 授权
    preferred_channel      ENUM('wechat','work_wechat','phone','app','h5') COMMENT '患者偏好渠道',
    wechat_openid          VARCHAR(64)  COMMENT '微信 OpenID（绑定后才有）',
    wechat_binding_status  ENUM('bound','unbound','expired') DEFAULT 'unbound' COMMENT '微信绑定状态',
    phone_callable         TINYINT(1)   DEFAULT 1 COMMENT '是否可电话外呼',
    emergency_contact_role VARCHAR(32)  COMMENT '紧急联系人关系（配偶/子女/父母）',
    consent_flags          JSON         COMMENT '各项授权标记 {voice_consent, image_consent, emotion_consent, his_consent}',
    created_at             DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at             DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='患者基本信息';
```

> **设计要点**：`patients` 表**只保留人口学基本信息 + 渠道偏好 + 授权标记**，所有临床数据（诊断、手术、出院小结等）全部拆分到入院/出院表中。

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `name` | 中文姓名 | `faker.name()` |
| `gender` | 性别 | `random.choice(['男','女'])` |
| `birth_date` | 出生日期（由此计算年龄） | `faker.date_of_birth(minimum_age=25, maximum_age=85)` |
| `phone` | 手机号 | `faker.phone_number()` |
| `address` | 地址 | `faker.address()` |
| `emergency_contact` | 紧急联系人 | `faker.name()` |
| `emergency_phone` | 紧急联系人电话 | `faker.phone_number()` |
| `preferred_channel` | 偏好渠道 | 按年龄分布：>65岁 60% phone，其余随机 |
| `wechat_binding_status` | 微信绑定 | <65岁 80% bound，>65岁 30% bound |
| `phone_callable` | 可外呼 | 85% true |
| `emergency_contact_role` | 联系人关系 | `random.choice(['配偶','子女','父母'])` |
| `consent_flags` | 授权标记 | `{"voice_consent": random_bool, "image_consent": random_bool, ...}` |

---

### 4.4 `admissions` — 入院记录

```sql
CREATE TABLE admissions (
    admission_id      INT AUTO_INCREMENT PRIMARY KEY,
    patient_id        INT          NOT NULL COMMENT '患者',
    doctor_id         INT          NOT NULL COMMENT '主治医生',
    department_id     INT          NOT NULL COMMENT '入院科室',
    admission_number  VARCHAR(32)  COMMENT '住院号',
    admission_date    DATE         NOT NULL COMMENT '入院日期',
    chief_complaint   TEXT         COMMENT '主诉',
    present_illness   TEXT         COMMENT '现病史',
    past_history      TEXT         COMMENT '既往史',
    admission_diagnosis TEXT       COMMENT '入院诊断',
    created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    FOREIGN KEY (department_id) REFERENCES departments(department_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='入院记录';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `admission_number` | 住院号 | `faker.bothify(text='ZY######')` |
| `admission_date` | 入院日期 | 出院日期前 3-30 天 |
| `chief_complaint` | 主诉 | 模板生成："{部位}疼痛{X}天，加重{Y}天" |
| `present_illness` | 现病史 | 模板段落（2-3句） |
| `past_history` | 既往史 | 模板段落 |
| `admission_diagnosis` | 入院诊断 | 从诊断列表选 1-2 个 |

---

### 4.5 `discharge_records` — 出院记录 ★ 核心表

> **这是整个 RAG 工作流的输入源。** Agent A（计划 Agent）读取 `discharge_summary` → 提取关键信息 → 构造 RAG 查询 → 检索指南 → 生成随访计划。

```sql
CREATE TABLE discharge_records (
    discharge_id           INT AUTO_INCREMENT PRIMARY KEY,
    patient_id             INT          NOT NULL COMMENT '患者',
    admission_id           INT          NOT NULL COMMENT '入院记录',
    doctor_id              INT          NOT NULL COMMENT '出院医生',
    discharge_date         DATE         NOT NULL COMMENT '出院日期',
    discharge_diagnosis    VARCHAR(256) NOT NULL COMMENT '出院诊断（结构化）',
    diagnosis_detail       TEXT         COMMENT '诊断详情',
    surgery_name           VARCHAR(256) COMMENT '手术名称',
    surgery_date           DATE         COMMENT '手术日期',
    surgery_summary        TEXT         COMMENT '手术经过',
    pain_type              ENUM('神经病理性疼痛','伤害感受性疼痛','混合性疼痛','中枢性疼痛') COMMENT '疼痛类型',
    pain_location          VARCHAR(128) COMMENT '疼痛部位',
    nrs_at_discharge       TINYINT      COMMENT '出院时 NRS 评分（0-10）',
    pain_duration          VARCHAR(64)  COMMENT '疼痛持续时间',
    discharge_medication   JSON         COMMENT '出院带药 [{drug_name,dosage,frequency,days}]',
    treatment_summary      TEXT         COMMENT '治疗经过总结',
    discharge_summary      TEXT         NOT NULL COMMENT '★★ 出院小结全文（RAG 检索输入）',
    discharge_instructions TEXT         COMMENT '出院医嘱',
    follow_up_necessity    ENUM('必须','建议','无需') DEFAULT '必须' COMMENT '是否需要随访',
    follow_up_recommendation TEXT       COMMENT '医生原始随访建议',
    created_at             DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (admission_id) REFERENCES admissions(admission_id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    INDEX idx_discharge_date (discharge_date),
    INDEX idx_pain_type (pain_type),
    INDEX idx_follow_up_necessity (follow_up_necessity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='出院记录（RAG 核心输入）';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `discharge_diagnosis` | 如"带状疱疹后神经痛" | 4 种诊断随机 |
| `surgery_name` | 如"椎间孔镜下髓核摘除术" | 部分患者有，匹配诊断 |
| `pain_type` | 疼痛类型 | 4 选 1 |
| `pain_location` | 如"右侧胸背部" | 模板匹配诊断 |
| `nrs_at_discharge` | 出院时 NRS | `random.randint(2,8)` |
| `discharge_medication` | JSON 数组 | 预定义药品 + 随机剂量 |
| `discharge_summary` | ★ 出院小结全文 | **模板拼接生成**（见第五节详细说明） |
| `discharge_instructions` | 医嘱 | 模板生成 |

---

### 4.6 `followup_plans` — 随访计划（RAG 生成结果）

```sql
CREATE TABLE followup_plans (
    plan_id                INT AUTO_INCREMENT PRIMARY KEY,
    patient_id             INT          NOT NULL COMMENT '患者',
    discharge_id           INT          NOT NULL COMMENT '关联出院记录',
    doctor_id              INT          NOT NULL COMMENT '负责医生',
    plan_json              JSON         NOT NULL COMMENT '结构化随访计划',
    rag_query_text         TEXT         COMMENT 'RAG 检索查询文本',
    rag_retrieval_context  JSON         COMMENT 'RAG 检索到的指南引用 [{ref,guide,year,excerpt}]',
    status                 ENUM('draft','approved','modified','rejected') DEFAULT 'draft',
    created_by             ENUM('AI','doctor') DEFAULT 'AI' COMMENT '创建者',
    original_snapshot      JSON         COMMENT '修改前快照（审计）',
    created_at             DATETIME     DEFAULT CURRENT_TIMESTAMP,
    reviewed_at            DATETIME     COMMENT '审阅时间',
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (discharge_id) REFERENCES discharge_records(discharge_id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    INDEX idx_status (status),
    INDEX idx_patient_plan (patient_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访计划';
```

**`plan_json` 结构**：

```json
{
  "frequency": "每周1次，连续4周；之后每2周1次",
  "duration_days": 90,
  "pain_type": "神经病理性疼痛",
  "recheck_items": ["血常规", "肝肾功能", "NRS评分"],
  "medication_adjustment": "根据NRS评分逐步减量普瑞巴林",
  "warning_threshold": "NRS≥7 或 连续3天未回复",
  "health_education": ["疼痛自我管理", "用药依从性", "康复锻炼"]
}
```

---

### 4.7 `followup_sessions` — 随访会话

```sql
CREATE TABLE followup_sessions (
    session_id         INT AUTO_INCREMENT PRIMARY KEY,
    patient_id         INT          NOT NULL COMMENT '患者',
    plan_id            INT          COMMENT '关联随访计划',
    session_status     ENUM('ongoing','completed','aborted') DEFAULT 'ongoing',
    transcript_json    JSON         COMMENT '对话原文 [{role,content,timestamp}]',
    agent_summary      TEXT         COMMENT 'Agent 会话摘要',
    -- v6.0 新增：渠道 + 语音 + 转人工
    channel            ENUM('wechat','work_wechat','phone','app','h5') COMMENT '本次随访使用的渠道',
    voice_mode         TINYINT(1)   DEFAULT 0 COMMENT '是否语音对话模式',
    escalation_status  ENUM('none','pending','handed_off','resolved') DEFAULT 'none' COMMENT '转人工状态',
    created_at         DATETIME     DEFAULT CURRENT_TIMESTAMP,
    completed_at       DATETIME     COMMENT '完成时间',
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (plan_id) REFERENCES followup_plans(plan_id),
    INDEX idx_patient_session (patient_id, created_at),
    INDEX idx_status (session_status),
    INDEX idx_channel (channel),
    INDEX idx_escalation (escalation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访会话';
```

| 新增字段 | 说明 | Faker 策略 |
|---------|------|-----------|
| `channel` | 随访渠道 | 与 `patients.preferred_channel` 一致 |
| `voice_mode` | 语音模式 | 老年患者 40% true |
| `escalation_status` | 转人工状态 | 90% 'none'，10% 有转人工事件 |

---

### 4.8 `followup_records` — 随访记录

> **⚠ 重要变更**：
> - `medication_adherence TINYINT(1)` → `medication_taken ENUM(...)` —— 对齐 V11.0 prompt 的 `partial` 输出
> - 新增 8 个字段：side_effects_severity / functional_impact / confidence / follow_up_needed / voice_url / image_asset_ids / guardrail_triggered / handoff_id

```sql
CREATE TABLE followup_records (
    record_id              INT AUTO_INCREMENT PRIMARY KEY,
    patient_id             INT          NOT NULL COMMENT '患者',
    plan_id                INT          COMMENT '随访计划',
    session_id             INT          NOT NULL COMMENT '所属会话',
    followup_date          DATE         NOT NULL COMMENT '随访日期',
    followup_round         INT          DEFAULT 1 COMMENT '第几轮随访对话',
    message_sent           TEXT         COMMENT '系统发送给患者的消息',
    reply_text             TEXT         COMMENT '患者回复原文',
    nrs_score              TINYINT      COMMENT 'NRS 疼痛评分 (0-10)',
    sleep_quality          ENUM('好','一般','差','很差') COMMENT '睡眠质量',
    -- ⚠ 类型变更：原 TINYINT(1) → ENUM，对齐 V11.0 prompt 的 partial 值
    medication_taken       ENUM('taken','not_taken','partial','unknown') COMMENT '用药依从性（对齐V11.0 prompt）',
    side_effects           TEXT         COMMENT '药物副作用描述（"无"/"否认"/具体症状）',
    -- V11.0 新增字段
    side_effects_severity  ENUM('轻','中','重') COMMENT '副作用严重度（V11.0新增）',
    functional_impact      TEXT         COMMENT '疼痛对日常生活的影响（V11.0新增）',
    confidence             JSON         COMMENT '各字段解析置信度 {nrs:"high", sleep:"medium", ...}',
    follow_up_needed       JSON         COMMENT '需追问的字段列表',
    mood_status            VARCHAR(32)  COMMENT '情绪状态（好/一般/焦虑/抑郁）',
    activity_level         VARCHAR(32)  COMMENT '活动能力（正常/受限/卧床）',
    additional_notes       TEXT         COMMENT '补充说明 / parse_notes',
    reply_parsed           JSON         COMMENT 'LLM 解析的完整结构化结果',
    -- v6.0 多模态 + 护栏 + 转人工
    voice_url              VARCHAR(512) COMMENT '患者语音回复的音频 URL',
    image_asset_ids        JSON         COMMENT '关联 multimodal_assets 表的 ID 列表',
    guardrail_triggered    TINYINT(1)   DEFAULT 0 COMMENT '本轮是否触发安全护栏',
    handoff_id             INT          COMMENT '关联 human_handoffs 表（如有转人工）',
    created_at             DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (plan_id) REFERENCES followup_plans(plan_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (handoff_id) REFERENCES human_handoffs(handoff_id),
    INDEX idx_record_patient_date (patient_id, followup_date),
    INDEX idx_record_session (session_id),
    INDEX idx_medication (medication_taken),
    INDEX idx_guardrail (guardrail_triggered)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='随访记录';
```

**`followup_records` 与 Reply Parsing V11.0 字段对齐表**：

| V11.0 输出字段 | 对应数据库字段 | 状态 |
|---------------|--------------|------|
| `nrs_score` | `nrs_score` | ✓ 已有 |
| `sleep_quality` | `sleep_quality` | ✓ 已有 |
| `medication_taken` (true/false/"partial") | `medication_taken` ENUM | ✅ 已改型对齐 |
| `side_effects` ("无"/"否认"/具体症状) | `side_effects` TEXT | ✓ 已有 |
| `side_effects_severity` (轻/中/重) | `side_effects_severity` ENUM | ✅ 新增 |
| `functional_impact` | `functional_impact` TEXT | ✅ 新增 |
| `mood` | `mood_status` VARCHAR(32) | ✓ 已有（P2阶段由 emotion_records 表增强） |
| `confidence` | `confidence` JSON | ✅ 新增 |
| `follow_up_needed` | `follow_up_needed` JSON | ✅ 新增 |
| `parse_notes` | `additional_notes` TEXT | ✓ 已有 |
| — | `reply_parsed` JSON | ✓ 完整 JSON 兜底 |

---

### 4.9 `risk_assessments` — 风险评估

```sql
CREATE TABLE risk_assessments (
    assessment_id   INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    session_id      INT          COMMENT '关联会话',
    risk_level      ENUM('low','medium','high','critical') NOT NULL COMMENT '风险等级',
    risk_score      DECIMAL(3,1) NOT NULL COMMENT '风险评分（0.0-10.0）',
    risk_factors    JSON         COMMENT '风险因子 [{factor,weight,detail}]',
    recommendation  TEXT         COMMENT '建议措施',
    assessed_by     ENUM('rule_engine','llm') DEFAULT 'rule_engine' COMMENT '评估方式',
    assessed_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    INDEX idx_risk_level (risk_level),
    INDEX idx_risk_patient_date (patient_id, assessed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='风险评估记录';
```

---

### 4.10 `doctor_reviews` — 医生审阅

```sql
CREATE TABLE doctor_reviews (
    review_id       INT AUTO_INCREMENT PRIMARY KEY,
    session_id      INT          NOT NULL COMMENT '随访会话',
    patient_id      INT          NOT NULL COMMENT '患者',
    doctor_id       INT          NOT NULL COMMENT '审阅医生',
    score           TINYINT      COMMENT '评分 1-5',
    comment         TEXT         COMMENT '医生评论',
    track_status    ENUM('pending_track','tracking','resolved','need_revisit','transferred')
                    DEFAULT 'pending_track' COMMENT '跟踪状态',
    ai_review       JSON         COMMENT 'D 号 Agent 智能审阅结果',
    audit_snapshot  JSON         COMMENT '会话快照（审计留痕）',
    reviewed_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    INDEX idx_track_status (track_status),
    INDEX idx_review_doctor (doctor_id, reviewed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医生审阅记录';
```

---

### 4.11 `alerts` — 预警记录

> **新增 5 个 `alert_type` 枚举值**，覆盖情绪危机、护栏越界、渠道不可达、HIS同步失败、持续低落。

```sql
CREATE TABLE alerts (
    alert_id        INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    doctor_id       INT          NOT NULL COMMENT '接收预警的医生',
    session_id      INT          COMMENT '关联会话',
    alert_level     ENUM('info','warning','critical') NOT NULL COMMENT '预警级别',
    alert_type      VARCHAR(32)  NOT NULL COMMENT '预警类型（见下方枚举表）',
    alert_reason    TEXT         NOT NULL COMMENT '预警原因',
    is_read         TINYINT(1)   DEFAULT 0 COMMENT '是否已读',
    is_resolved     TINYINT(1)   DEFAULT 0 COMMENT '是否已处理',
    resolved_at     DATETIME     COMMENT '处理时间',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    INDEX idx_alert_unread (doctor_id, is_read, created_at),
    INDEX idx_alert_patient (patient_id, created_at),
    INDEX idx_alert_type (alert_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='预警记录';
```

**`alert_type` 枚举**：

| 值 | 说明 | 来源 | v6.0新增 |
|-----|------|------|---------|
| `risk_escalation` | 风险升级（低→中、中→高） | v5.0 | |
| `no_reply_3days` | 连续 3 天未回复 | v5.0 | |
| `nrs_spike` | NRS 评分突增 ≥3 分 | v5.0 | |
| `medication_miss` | 连续多日未按医嘱用药 | v5.0 | |
| `abnormal_indicator` | 异常指标（情绪急剧恶化等） | v5.0 | |
| `emotion_crisis` | 情绪危机（绝望/自伤倾向）— 红色预警 | 需求3 | ★ |
| `guardrail_violation` | 护栏越界（患者要求开方/诊断，AI 拦截） | 需求9 | ★ |
| `channel_unreachable` | 渠道不可达（微信长期未读/电话无人接） | 需求6 | ★ |
| `his_sync_failed` | HIS 数据同步失败 | 需求8 | ★ |
| `emotion_persistent_low` | 持续低落（连续≥3轮情绪低分） | 需求3 | ★ |

---

### 4.12 `pain_diaries` — 疼痛日记

> 患者在两次随访之间自主记录疼痛情况，是慢痛管理的重要补充数据。

```sql
CREATE TABLE pain_diaries (
    diary_id        INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    record_date     DATE         NOT NULL COMMENT '记录日期',
    nrs_score       TINYINT      COMMENT 'NRS 评分（0-10）',
    pain_location   VARCHAR(128) COMMENT '疼痛部位',
    pain_nature     VARCHAR(64)  COMMENT '疼痛性质（刺痛/钝痛/灼烧痛/电击痛）',
    triggers        TEXT         COMMENT '诱发因素',
    relief_factors  TEXT         COMMENT '缓解因素',
    notes           TEXT         COMMENT '备注',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    INDEX idx_diary_patient_date (patient_id, record_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='疼痛日记';
```

---

### 4.13 `medication_records` — 用药记录

> 跟踪患者出院后的实际用药情况，支撑用药依从性分析。

```sql
CREATE TABLE medication_records (
    med_record_id   INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    discharge_id    INT          COMMENT '关联出院记录的带药',
    drug_name       VARCHAR(128) NOT NULL COMMENT '药品名称',
    dosage          VARCHAR(64)  COMMENT '剂量',
    frequency       VARCHAR(64)  COMMENT '用药频次',
    start_date      DATE         NOT NULL COMMENT '开始日期',
    end_date        DATE         COMMENT '结束日期',
    adherence_note  TEXT         COMMENT '依从性备注',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (discharge_id) REFERENCES discharge_records(discharge_id),
    INDEX idx_med_patient (patient_id, start_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用药记录';
```

---

### 4.14 `patient_channels` — 患者渠道绑定 ★ 新增

> **对应 v6.0 需求 4（接入方式）/ 需求 6（电话外呼）**：一个患者可绑定多个渠道（微信/企微/电话/APP/H5），系统按偏好和可达性选择渠道。

```sql
CREATE TABLE patient_channels (
    channel_id      INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    channel_type    ENUM('wechat','work_wechat','phone','app','h5') NOT NULL COMMENT '渠道类型',
    external_id     VARCHAR(128) COMMENT '外部标识（如微信 OpenID / 企微 UserID / 手机号）',
    binding_status  ENUM('bound','unbound','expired','revoked') DEFAULT 'unbound',
    is_preferred    TINYINT(1)   DEFAULT 0 COMMENT '是否首选渠道',
    last_active_at  DATETIME     COMMENT '最近活跃时间',
    metadata        JSON         COMMENT '渠道附加信息（如微信昵称/头像URL）',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    INDEX idx_patient_channel (patient_id, channel_type),
    INDEX idx_preferred (patient_id, is_preferred)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='患者渠道绑定';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `channel_type` | 渠道类型 | 每患者 1-3 个渠道 |
| `external_id` | 外部标识 | 微信生成假 OpenID，电话用 phone |
| `binding_status` | 绑定状态 | 80% bound, 15% unbound, 5% expired |
| `is_preferred` | 首选 | 每患者仅 1 个为 1 |
| `last_active_at` | 最近活跃 | 最近 1-30 天随机 |

---

### 4.15 `human_handoffs` — 人工协同记录 ★ 新增

> **对应 v6.0 需求 1（专业问题转人工）**：当患者提出专业问题（开方/诊断/检查解读）、AI 置信度过低、患者主动要求人工、或触发安全护栏时，记录转人工事件并存储上下文移交包。

```sql
CREATE TABLE human_handoffs (
    handoff_id        INT AUTO_INCREMENT PRIMARY KEY,
    session_id        INT          NOT NULL COMMENT '关联会话',
    patient_id        INT          NOT NULL COMMENT '患者',
    trigger_reason    ENUM('professional_question','low_confidence','patient_request',
                           'guardrail_violation','emotion_crisis','emergency') NOT NULL COMMENT '转人工原因',
    trigger_confidence DECIMAL(3,2) COMMENT '触发时的置信度（0.00-1.00）',
    intent_label      VARCHAR(64)  COMMENT 'C号输出的 intent 标签',
    context_package   JSON         COMMENT '移交上下文包 {patient_summary, dialog_summary, steps_done, pending_questions}',
    target_queue      ENUM('nurse_station','attending_doctor','clinical_pharmacist') DEFAULT 'nurse_station' COMMENT '坐席队列',
    assigned_to       INT          COMMENT '分配给的医生/护士 ID',
    status            ENUM('pending','accepted','in_progress','resolved','rejected') DEFAULT 'pending',
    resolution_note   TEXT         COMMENT '处理备注',
    agent_role        ENUM('active','recorder','standby') DEFAULT 'recorder' COMMENT '转人工后智能体角色',
    resumed_at        DATETIME     COMMENT '人工结束、智能体恢复时间',
    created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (assigned_to) REFERENCES doctors(doctor_id),
    INDEX idx_handoff_status (status, target_queue),
    INDEX idx_handoff_patient (patient_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人工协同转接记录';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `trigger_reason` | 转人工原因 | 60% professional_question, 20% low_confidence, 10% patient_request, 10% 其他 |
| `context_package` | 上下文移交包 | 模板生成 JSON |
| `target_queue` | 坐席队列 | 70% nurse_station, 20% attending_doctor, 10% pharmacist |
| `status` | 状态 | 60% resolved, 25% in_progress, 15% pending |
| `agent_role` | 智能体角色 | 转人工后默认 'recorder' |

---

### 4.16 `emotion_records` — 情绪感知记录 ★ 新增

> **对应 v6.0 需求 3（情绪感知）**：现有 `followup_records.mood_status` 仅一个 VARCHAR，无法存多标签情绪打分+危机等级+文本/语音双模态特征。此表为独立情绪轨迹数据源。

```sql
CREATE TABLE emotion_records (
    emotion_id      INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    session_id      INT          NOT NULL COMMENT '关联会话',
    record_id       INT          COMMENT '关联随访记录（对应某一轮对话）',
    emotion_labels  JSON         NOT NULL COMMENT '情绪多标签打分 [{"label":"平静","score":0.7},{"label":"焦虑","score":0.3},...]',
    dominant_emotion VARCHAR(32) COMMENT '主导情绪（平静/焦虑/低落/绝望/愤怒）',
    crisis_level    ENUM('none','yellow','red') DEFAULT 'none' COMMENT '危机等级（黄=低落, 红=绝望/自伤）',
    text_features   JSON         COMMENT '文本语义特征',
    voice_features  JSON         COMMENT '语音韵律特征（如有）',
    intervention    TEXT         COMMENT '已执行的干预话术',
    assessed_by     ENUM('rule_engine','llm','hybrid') DEFAULT 'hybrid',
    assessed_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (record_id) REFERENCES followup_records(record_id),
    INDEX idx_emotion_patient (patient_id, assessed_at),
    INDEX idx_crisis (crisis_level, assessed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='情绪感知记录';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `emotion_labels` | 多标签打分 | 从 `['平静','焦虑','低落','绝望','愤怒']` 选 1-3 个，归一化分数 |
| `dominant_emotion` | 主导情绪 | 取最高分标签 |
| `crisis_level` | 危机等级 | 85% none, 10% yellow, 5% red |
| `text_features` | 文本特征 | 模板生成 JSON |
| `intervention` | 干预话术 | 有 crisis_level 时生成 |

---

### 4.17 `multimodal_assets` — 多模态资源 ★ 新增

> **对应 v6.0 需求 2（语音功能）/ 需求 5（图像多模态）**：患者上传的语音/图片/视频无处存储，ASR/VLM 分析结果无落脚点。此表统一管理所有非文本资源。

```sql
CREATE TABLE multimodal_assets (
    asset_id        INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    session_id      INT          COMMENT '关联会话',
    record_id       INT          COMMENT '关联随访记录',
    asset_type      ENUM('voice','image','video') NOT NULL COMMENT '资源类型',
    file_path       VARCHAR(512) NOT NULL COMMENT '存储路径（加密后）',
    file_url        VARCHAR(512) COMMENT '访问 URL（有时效）',
    duration_sec    INT          COMMENT '时长（语音/视频，秒）',
    mime_type       VARCHAR(64)  COMMENT 'MIME 类型',
    file_size       INT          COMMENT '文件大小（字节）',
    -- 语音字段
    asr_text        TEXT         COMMENT 'ASR 转写文本',
    asr_confidence  DECIMAL(3,2) COMMENT 'ASR 置信度',
    -- 图像字段
    vlm_analysis    JSON         COMMENT 'VLM 结构化分析结果 {wound_size, redness, dressing_status, ...}',
    vlm_description TEXT         COMMENT 'VLM 文本描述',
    -- 合规
    is_deidentified TINYINT(1)   DEFAULT 0 COMMENT '是否已完成去标识化',
    consent_given   TINYINT(1)   DEFAULT 0 COMMENT '患者是否授权',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (record_id) REFERENCES followup_records(record_id),
    INDEX idx_asset_patient (patient_id, asset_type, created_at),
    INDEX idx_asset_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多模态资源（语音/图像/视频）';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `asset_type` | 资源类型 | 70% voice, 25% image, 5% video |
| `file_path` | 存储路径 | `/data/assets/{patient_id}/{uuid}.ext` |
| `duration_sec` | 时长 | 语音 5-60 秒 |
| `asr_text` | ASR 转写 | 从随访回复模板生成 |
| `asr_confidence` | ASR 置信度 | 0.75-0.99 |
| `vlm_analysis` | VLM 分析 | 图像类型时生成 JSON |
| `is_deidentified` | 去标识化 | 90% true |

---

### 4.18 `call_records` — 电话外呼记录 ★ 新增

> **对应 v6.0 需求 6（电话外呼老人）**：外呼记录（时间/时长/结果/ASR日志）无独立表，需支持 TTS 播报、ASR 转写、DTMF 按键、重试策略、失败兜底。

```sql
CREATE TABLE call_records (
    call_id         INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    session_id      INT          COMMENT '关联会话',
    phone_number    VARCHAR(20)  NOT NULL COMMENT '被叫号码',
    call_direction  ENUM('outbound','inbound') DEFAULT 'outbound',
    call_status     ENUM('initiated','ringing','answered','no_answer','busy','failed','completed') NOT NULL,
    start_time      DATETIME     COMMENT '呼叫开始时间',
    answer_time     DATETIME     COMMENT '接通时间',
    end_time        DATETIME     COMMENT '挂断时间',
    duration_sec    INT          COMMENT '通话时长（秒）',
    tts_segments    JSON         COMMENT 'TTS 播报段 [{text, audio_url, duration}]',
    asr_full_text   TEXT         COMMENT 'ASR 完整转写',
    dtmf_input      VARCHAR(32)  COMMENT '按键输入（如有）',
    retry_count     INT          DEFAULT 0 COMMENT '重试次数',
    fallback_action ENUM('none','sms','family_notify','handoff') DEFAULT 'none' COMMENT '外呼失败后的兜底动作',
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    INDEX idx_call_patient (patient_id, start_time),
    INDEX idx_call_status (call_status, start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电话外呼记录';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `call_status` | 通话状态 | 60% completed, 15% no_answer, 10% busy, 10% failed, 5% 其他 |
| `duration_sec` | 通话时长 | 30-300 秒 |
| `tts_segments` | TTS 播报段 | 模板生成 2-5 段 |
| `asr_full_text` | ASR 转写 | 从随访回复模板生成 |
| `retry_count` | 重试次数 | 0-2 次 |
| `fallback_action` | 兜底动作 | no_answer 时 50% sms, 30% family_notify |

---

### 4.19 `physician_personas` — 医师风格画像 ★ 新增

> **对应 v6.0 需求 7（模拟主诊医师风格）**：医师沟通风格模板/称呼习惯/术语偏好/声纹授权无处存储。此表为可选个性化模块，`tone_intensity` 控制风格强度，医疗准确性始终优先。

```sql
CREATE TABLE physician_personas (
    persona_id        INT AUTO_INCREMENT PRIMARY KEY,
    doctor_id         INT          NOT NULL COMMENT '关联医生',
    style_label       VARCHAR(64)  COMMENT '风格标签（如「温和细致型」「干练果断型」）',
    address_habit     VARCHAR(128) COMMENT '称呼习惯（如「阿姨」「老王」「您」）',
    explanation_style ENUM('detailed','concise','analogy') COMMENT '解释方式',
    terminology_level ENUM('layman','mixed','professional') COMMENT '术语偏好',
    sample_phrases    JSON         COMMENT '高频用语示例 [{scenario, phrase}]',
    tone_intensity    TINYINT      DEFAULT 5 COMMENT '风格强度 1-10（10=最强烈），医疗准确性始终优先',
    voice_model_url   VARCHAR(512) COMMENT 'TTS 个性化音色模型 URL（需声纹授权）',
    voice_consent     TINYINT(1)   DEFAULT 0 COMMENT '声纹克隆授权',
    voice_consent_at  DATETIME     COMMENT '授权时间',
    is_active         TINYINT(1)   DEFAULT 1 COMMENT '是否启用（可一键关闭）',
    created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (doctor_id) REFERENCES doctors(doctor_id),
    INDEX idx_persona_doctor (doctor_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='医师风格画像';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `style_label` | 风格标签 | 从 `['温和细致型','干练果断型','幽默亲切型','严谨学术型']` 随机 |
| `address_habit` | 称呼习惯 | 按风格匹配 |
| `explanation_style` | 解释方式 | detailed/concise/analogy 三选一 |
| `terminology_level` | 术语偏好 | layman/mixed/professional 三选一 |
| `sample_phrases` | 高频用语 | 按风格模板生成 3-5 条 |
| `tone_intensity` | 风格强度 | 1-10 随机 |
| `voice_consent` | 声纹授权 | 20% true |

---

### 4.20 `guardrail_events` — 安全护栏事件 ★ 新增

> **对应 v6.0 需求 9（行为边界与护栏）**：越界检测/紧急事件/护栏拦截无独立审计表。此表记录所有 AI 行为边界事件，支持紧急协议自动触发。

```sql
CREATE TABLE guardrail_events (
    event_id          INT AUTO_INCREMENT PRIMARY KEY,
    session_id        INT          NOT NULL COMMENT '关联会话',
    patient_id        INT          NOT NULL COMMENT '患者',
    record_id         INT          COMMENT '关联随访记录（触发轮次）',
    event_type        ENUM('out_of_scope','diagnosis_request','prescription_request',
                         'emergency','self_harm','professional_question','low_confidence') NOT NULL COMMENT '事件类型',
    severity          ENUM('info','warning','critical','emergency') NOT NULL COMMENT '严重度',
    trigger_text      TEXT         COMMENT '触发文本（患者原话）',
    rule_matched      VARCHAR(128) COMMENT '命中的规则名称',
    llm_moderation    TEXT         COMMENT 'LLM 复核结果',
    action_taken      ENUM('blocked','warned','redirected','escalated','emergency_protocol') NOT NULL COMMENT '执行动作',
    emergency_response TEXT        COMMENT '紧急情况播报内容（如120引导）',
    handoff_id        INT          COMMENT '关联转人工记录（如有）',
    resolved_at       DATETIME     COMMENT '处理完成时间',
    created_at        DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES followup_sessions(session_id),
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    FOREIGN KEY (record_id) REFERENCES followup_records(record_id),
    FOREIGN KEY (handoff_id) REFERENCES human_handoffs(handoff_id),
    INDEX idx_guardrail_severity (severity, created_at),
    INDEX idx_guardrail_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全护栏事件';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `event_type` | 事件类型 | 30% professional_question, 25% out_of_scope, 20% diagnosis_request, 15% prescription_request, 5% emergency, 5% self_harm |
| `severity` | 严重度 | 40% info, 30% warning, 20% critical, 10% emergency |
| `action_taken` | 执行动作 | 与 severity 对应 |
| `emergency_response` | 紧急播报 | emergency/self_harm 类型时生成 |

---

### 4.21 `his_sync_logs` — HIS 同步日志 ★ 新增

> **对应 v6.0 需求 8（HIS 对接）**：HIS 数据拉取/回写同步日志无表，无法审计同步状态。此表记录所有与 HIS 的双向数据交互。

```sql
CREATE TABLE his_sync_logs (
    sync_id         INT AUTO_INCREMENT PRIMARY KEY,
    patient_id      INT          NOT NULL COMMENT '患者',
    sync_direction  ENUM('pull','push') NOT NULL COMMENT '拉取 or 回写',
    sync_type       ENUM('diagnosis','medication','lab_result','allergy',
                         'followup_result','risk_assessment') NOT NULL COMMENT '同步内容类型',
    sync_status     ENUM('success','partial','failed','pending') NOT NULL,
    his_record_id   VARCHAR(128) COMMENT 'HIS 侧记录 ID',
    local_record_id VARCHAR(128) COMMENT '本地记录 ID（表名:主键）',
    field_mapping   JSON         COMMENT '字段映射日志 [{his_field, local_field, value, deidentified}]',
    error_message   TEXT         COMMENT '失败原因',
    synced_at       DATETIME     DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
    INDEX idx_sync_patient (patient_id, sync_direction, synced_at),
    INDEX idx_sync_status (sync_status, synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='HIS 同步日志';
```

| 字段 | 说明 | Faker 策略 |
|------|------|-----------|
| `sync_direction` | 同步方向 | 60% pull, 40% push |
| `sync_type` | 同步内容 | 随机分布 |
| `sync_status` | 同步状态 | 85% success, 10% partial, 5% failed |
| `field_mapping` | 字段映射 | 模板生成 JSON |

---

## 五、RAG 工作流数据链路

### 5.1 核心链路

```
discharge_records.discharge_summary  (出院小结全文)
        │
        ▼
  [LLM 提取关键信息]
  诊断 + 疼痛类型 + 手术 + 用药
        │
        ▼
  [构造 RAG 查询]
  rag_query_text = "慢性疼痛 随访计划 {diagnosis} {pain_type} {surgery}"
        │
        ▼
  [ChromaDB 向量检索]
  返回 Top-K 指南片段 + 引用出处
        │
        ▼
  [LLM 生成随访计划]
  结合 patient context + RAG 引用 → 结构化 plan_json
        │
        ▼
  followup_plans 表写入：
  ├── rag_query_text        ← RAG 查询文本
  ├── rag_retrieval_context ← RAG 检索结果 [{ref, guide, year, excerpt}]
  ├── plan_json             ← 生成的随访计划
  └── status = 'draft'      ← 待医生审阅
```

### 5.2 出院小结文本模板（Faker 生成核心）

`discharge_summary` 是 Faker 生成数据中**最关键的字段**，它直接决定 RAG 检索的质量。建议用模板拼接方式生成，包含以下段落：

```
【出院小结模板】

患者{姓名}，{性别}，{年龄}岁，因"{主诉}"于{入院日期}入院。
入院诊断：{入院诊断}。住院期间完善相关检查，于{手术日期}在{麻醉方式}
下行{手术名称}，手术顺利。术后给予{治疗措施}等对症支持治疗。

出院诊断：{出院诊断}。
出院时患者一般情况可，{疼痛部位}疼痛 NRS {分数}分，
疼痛性质为{疼痛性质}，{睡眠情况}，{饮食情况}。

出院带药：{药品列表}。
出院医嘱：{逐条医嘱}。

建议出院后{随访建议}，如有{预警症状}请及时就诊。
```

### 5.3 诊断与手术对照表

| 出院诊断 | 疼痛类型 | 常见手术 | 典型药品 |
|----------|---------|---------|---------|
| 带状疱疹后神经痛 | 神经病理性疼痛 | 神经阻滞术 | 普瑞巴林、加巴喷丁、利多卡因贴剂 |
| 腰椎术后疼痛 | 混合性疼痛 | 椎间孔镜下髓核摘除术 / 腰椎后路减压融合内固定术 | 塞来昔布、曲马多、乙哌立松 |
| 糖尿病周围神经痛 | 神经病理性疼痛 | — | 普瑞巴林、度洛西汀、甲钴胺 |
| 癌性疼痛 | 混合性疼痛 | 姑息性手术（个体化） | 吗啡缓释片、羟考酮、芬太尼透皮贴 |

### 5.4 v6.0 多渠道随访数据链路

```
patient_channels（渠道绑定）
        │
        ▼
  [渠道决策引擎]
  按 preferred_channel → 可达性 → 备选渠道 排序
        │
        ├── wechat/work_wechat/app/h5 → 微信/企微/APP/H5 消息推送
        │     └── followup_sessions.channel = 对应渠道
        │
        └── phone → 电话外呼
              ├── call_records（外呼记录）
              │     ├── TTS 播报 → 患者接听
              │     ├── ASR 转写 → asr_full_text
              │     └── 失败兜底 → fallback_action (sms/family_notify/handoff)
              └── followup_sessions.channel = 'phone'
                    └── followup_sessions.voice_mode = 1
```

---

## 六、新旧对照：你得到的提升

| 对比维度 | 旧版（SQLite 6 表） | 初版（MySQL 13 表） | v6.0 升级版（MySQL 21 表） |
|---------|-------------------|-------------------|--------------------------|
| 患者信息 | 全塞 `patients` 一张表 | `patients` + `admissions` + `discharge_records` 三表拆分 | + 渠道偏好/微信绑定/授权标记 |
| 出院小结 | 作为 `followup_plan` 的一个 TEXT 字段 | 独立 `discharge_records` 表，RAG 检索的原生输入源 | 不变 |
| 医生管理 | `doctor_id` / `doctor_name` 硬编码字符串 | 独立 `doctors` 表，支持增删改查 | + 风格画像/声纹克隆 |
| 用药跟踪 | 无 | 独立 `medication_records` 表 | + `medication_taken` ENUM 支持 partial |
| 疼痛日记 | 无 | 独立 `pain_diaries` 表 | 不变 |
| 预警闭环 | 无 | `alerts` 表：产生→已读→已处理 | + 5 个新预警类型 |
| 风险评估 | 字符串 `high_risk` / `medium_risk` | 独立 `risk_assessments` 表，含评分、因子、建议 | 不变 |
| RAG 可溯源性 | 引用文献混在 `plan_json` 里 | `rag_query_text` + `rag_retrieval_context` 独立存储 | 不变 |
| 随访记录维度 | NRS + 睡眠 + 用药（3 项） | 新增副作用、情绪、活动能力、LLM 解析结果（7+ 项） | + V11.0 全字段对齐（置信度/追问/严重度/功能影响） |
| 多渠道接入 | 无 | 无 | `patient_channels` 表 + `patients` 渠道字段 |
| 语音/图像 | 无 | 无 | `multimodal_assets` 表 + ASR/VLM 分析 |
| 电话外呼 | 无 | 无 | `call_records` 表 + TTS/ASR/DTMF/兜底 |
| 转人工 | 无 | 无 | `human_handoffs` 表 + 上下文移交包 |
| 情绪感知 | 无 | `mood_status` VARCHAR | `emotion_records` 表 + 多标签/危机等级 |
| 安全护栏 | 无 | 无 | `guardrail_events` 表 + 紧急协议 |
| 医师风格 | 无 | 无 | `physician_personas` 表 + 声纹克隆 |
| HIS 对接 | 无 | 无 | `his_sync_logs` 表 + 双向同步 |
| Demo 假数据 | 固定种子随机，需改代码 | Faker 声明式生成，可配置数据量 | 不变 |
| 存储引擎 | SQLite（文件锁、单写） | MySQL InnoDB（行锁、并发、事务） | 不变 |

---

## 七、Faker 数据生成策略

### 7.1 依赖

```python
# requirements.txt 新增
faker>=30.0.0
pymysql>=1.1.0
```

### 7.2 固定随机种子（每次生成数据完全一致）

Faker 原生支持固定种子，保证每次运行生成的姓名、电话、地址、日期等完全一致：

```python
from faker import Faker
import random

# 方式一：全局种子（推荐，所有 Faker 实例共享）
Faker.seed(20240729)

# 方式二：实例级种子（只影响当前实例）
# fake = Faker('zh_CN')
# fake.seed_instance(20240729)

# Python 原生 random 也要同步种子（random.choice / random.randint 等依赖它）
random.seed(20240729)
```

**验证确定性**：以下代码每次运行输出完全一致。

```python
fake = Faker('zh_CN')
Faker.seed(20240729)

print(fake.name())           # 每次运行同一个名字
print(fake.phone_number())   # 每次运行同一个号码
print(fake.address())        # 每次运行同一个地址
```

> **注意**：`Faker.seed()` 和 `random.seed()` 必须同时设置，因为 `random.choice()`、`random.randint()` 等 Python 原生随机函数依赖 `random` 模块的种子。旧版 SQLite 的 `_seed_data()` 也是用这个模式（种子值 `20240618`）。

### 7.3 生成器架构建议

```python
# scripts/generate_mock_data.py

from faker import Faker
import random

# ====== 固定种子（必须放在最前面） ======
SEED = 20240729
Faker.seed(SEED)
random.seed(SEED)
# =====================================

fake = Faker('zh_CN')          # 中文 locale

# ---- 预定义常量（Faker 不支持中文医学领域数据） ----
DIAGNOSES = [
    '带状疱疹后神经痛',
    '腰椎术后疼痛综合征',
    '糖尿病周围神经痛',
    '癌性疼痛'
]

PAIN_TYPES = ['神经病理性疼痛', '伤害感受性疼痛', '混合性疼痛', '中枢性疼痛']

DOCTOR_TITLES = ['主治医师', '副主任医师', '主任医师']

CHANNELS = ['wechat', 'work_wechat', 'phone', 'app', 'h5']

STYLE_LABELS = ['温和细致型', '干练果断型', '幽默亲切型', '严谨学术型']

DRUGS = {
    '带状疱疹后神经痛': [
        {'name': '普瑞巴林胶囊', 'dosage': '75mg', 'frequency': 'bid'},
        {'name': '加巴喷丁胶囊', 'dosage': '300mg', 'frequency': 'tid'},
    ],
    # ... 其他诊断对应药品
}

# ---- 各表生成函数 ----
def generate_patients(n=200):
    """生成患者"""
    ...

def generate_discharge_summary(patient, admission, doctor):
    """★ 核心函数：模板拼接出院小结"""
    ...

def generate_patient_channels(patients):
    """★ 新增：为每患者生成 1-3 个渠道绑定"""
    ...

def generate_handoffs(sessions):
    """★ 新增：为 10% 的会话生成转人工记录"""
    ...

def generate_emotion_records(sessions, records):
    """★ 新增：为每轮随访生成情绪记录"""
    ...

def generate_multimodal_assets(sessions, records):
    """★ 新增：生成语音/图片资源"""
    ...

def generate_call_records(sessions, patients):
    """★ 新增：为电话渠道会话生成外呼记录"""
    ...

def generate_guardrail_events(sessions, records):
    """★ 新增：生成护栏事件"""
    ...
```

### 7.4 数据量建议

| 表 | 建议数量 | 说明 |
|----|---------|------|
| `departments` | 5 | 疼痛科、骨科、神经内科、肿瘤科、康复科 |
| `doctors` | 15~30 | 每科室 3-6 人 |
| `patients` | 200~500 | 根据 Demo 需要 |
| `admissions` | 200~500 | 与患者 1:1 |
| `discharge_records` | 200~500 | 与入院 1:1 |
| `followup_plans` | 200~500 | 与出院 1:1 |
| `followup_sessions` | 200~500+ | 可一患多会话 |
| `followup_records` | 1000~5000+ | 每次随访一条 |
| `risk_assessments` | 1000~5000+ | 每次随访后评估 |
| `pain_diaries` | 2000~10000+ | 患者自主记录 |
| `medication_records` | 500~2000 | 出院带药记录 |
| `alerts` | 按需 | 高风险事件触发 |
| `patient_channels` | 400~1000 | 每患者 1-3 个渠道 |
| `human_handoffs` | 50~200 | 10% 的会话触发转人工 |
| `emotion_records` | 1000~5000 | 每轮随访一条情绪记录 |
| `multimodal_assets` | 500~2000 | 语音为主，图片次之 |
| `call_records` | 100~500 | 电话渠道会话的外呼记录 |
| `physician_personas` | 10~30 | 每医生 1 个画像 |
| `guardrail_events` | 50~300 | 5-10% 的会话触发护栏 |
| `his_sync_logs` | 200~1000 | 按同步频率生成 |

---

## 八、迁移步骤建议

```
Phase 1: 建表（v5.0 核心 13 表）
  ├── 在 MySQL 中执行建表 SQL（第 4.1-4.13 节）
  ├── 确认字符集 utf8mb4、引擎 InnoDB
  └── 验证外键约束

Phase 2: 建表（v6.0 新增 8 表）
  ├── 注意建表顺序（有 FK 依赖）：
  │   physician_personas → doctors（加字段）
  │   → patients（加字段）→ patient_channels
  │   → followup_sessions（加字段）→ followup_records（加字段）
  │   → human_handoffs → guardrail_events
  │   → emotion_records → multimodal_assets
  │   → call_records → his_sync_logs
  └── 验证所有新增 FK 约束

Phase 3: Faker 造数
  ├── 编写 generate_mock_data.py
  ├── 按依赖顺序生成：
  │   departments → doctors → physician_personas
  │   → patients → patient_channels
  │   → admissions → discharge_records
  │   → followup_plans → followup_sessions
  │   → followup_records → human_handoffs
  │   → emotion_records → multimodal_assets
  │   → call_records → guardrail_events
  │   → risk_assessments → doctor_reviews
  │   → alerts → his_sync_logs
  ├── 出院小结用模板拼接，保证 RAG 检索可用
  └── 插入 MySQL 并验证数据量

Phase 4: 后端适配
  ├── 替换 sqlite3 → pymysql
  ├── 新增各表 DAO（参考现有 followup_db.py 的模式）
  ├── 修改 Agent A/B/C/D 的数据库读写路径
  ├── 对齐 V11.0 prompt 输出与 followup_records 字段映射
  └── 保留 ChromaDB 向量库不变

Phase 5: 验证
  ├── 端到端测试：出院小结 → RAG 检索 → 随访计划生成 → 随访执行 → 审阅
  ├── 多渠道测试：微信/电话/H5 渠道切换
  ├── 护栏测试：越界问题拦截 → 转人工 → 恢复
  ├── 性能测试：500 患者 × 90 天随访数据的查询性能
  └── 数据完整性：FK 约束、索引命中率
```

---

## 九、v6.0 需求覆盖矩阵

| # | 需求 | 现有表覆盖 | 新增表 | 字段变更 | 优先级 |
|---|------|----------|--------|---------|--------|
| 1 | 专业问题转人工 | alerts（部分） | `human_handoffs` | `followup_records.handoff_id` / `followup_sessions.escalation_status` | P2 |
| 2 | 语音功能 | — | `multimodal_assets` | `followup_records.voice_url` / `followup_sessions.voice_mode` | P1 |
| 3 | 情绪识别 | `followup_records.mood_status`（太简单） | `emotion_records` | `alerts` +emotion_crisis/emotion_persistent_low | P2 |
| 4 | 接入方式 | — | `patient_channels` | `patients.preferred_channel` 等 / `followup_sessions.channel` | P1 |
| 5 | 图像多模态 | — | `multimodal_assets` | `followup_records.image_asset_ids` | P3 |
| 6 | 电话外呼 | — | `call_records` / `patient_channels` | `patients.phone_callable` | P1 |
| 7 | 医师风格 | `doctors`（仅基本信息） | `physician_personas` | `doctors.persona_id` / `voice_print_*` | P3 |
| 8 | HIS 对接 | `discharge_records` / `admissions`（可被HIS拉取） | `his_sync_logs` | — | P4 |
| 9 | 行为边界 | `alerts`（部分） | `guardrail_events` | `followup_records.guardrail_triggered` / `alerts` +guardrail_violation | P1 |

> **建议实施顺序**：
> - **P1（基座+合规底座）**：`patient_channels` → `multimodal_assets` → `call_records` → `guardrail_events` → `patients`/`sessions`/`records` 字段追加 → `alerts` 枚举扩展
> - **P2（智能增强）**：`human_handoffs` → `emotion_records`
> - **P3（个性化）**：`physician_personas` → `doctors` 字段追加
> - **P4（系统集成）**：`his_sync_logs`

---

## 十、总结

这次重构的核心思想是 **"出院小结驱动一切"**——出院小结不再是某个表的附属字段，而是独立实体。Agent A 从出院小结中提取关键信息→RAG 检索→生成随访计划，整条链路的输入、中间产物、输出都有清晰的存储位置。

相比旧版 6 张表（其中 3 张每次重启清空），初版 13 张表的每张都有明确的业务含义和长期保留价值。

v6.0 升级后扩展到 21 张表，新增 8 张表覆盖了《产品设计优化思路 v6.0》的全部九项需求：多渠道接入、语音/图像多模态、情绪感知、转人工协同、电话外呼、安全护栏、医师风格画像、HIS 对接。同时 `followup_records` 表完全对齐 Reply Parsing V11.0 的 10 个输出字段，解决了 `medication_adherence TINYINT(1)` 无法表达 `"partial"` 的类型硬伤。
