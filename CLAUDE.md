# CLAUDE.md

用户是 Haoran (Jeffrey) Jiang，SJSU AI 硕士。用中文和我交流；代码、注释、提交信息用英文。

## 项目

CMPE 295A/B Capstone（2026 秋 – 2027 春）：**LLM-Driven Ethical Conflict Resolution for Multi-Robot Coordination**
导师 Dr. Wencen Wu。在实验室已发表的单机器人工作上扩展：Pandit et al., IEEE BigDataService 2026。

核心想法：多个机器人各自用 LLM 独立提出计划，一个**共享检查层**把所有计划放在一起，按 safety / fairness / policy 规则联合检查；有冲突就返回结构化的拒绝理由，让相关机器人重新规划，而不是让某个机器人自己决定。

研究 gap（Workbook 1 第 1 章论证）：现有工作要么是单机器人护栏（RoboGuard），要么是中心化规划后检查一个联合计划（SAFER），要么是不考虑伦理的路径冲突解决（CBS）。还没有工作联合检查多个 LLM 机器人**各自独立生成**的计划。

对照 baseline：per-robot guardrail（RoboGuard 风格）、Pandit et al. 单机器人系统（每台机器人一份）、SAFER 风格中心化安全规划器、经典 CBS。
评估指标：无需重规划的通过率、安全违规次数、冲突解决的公平性。

技术栈：Python、Pydantic、pytest；后续集成 ROS 2 Jazzy、Gazebo Harmonic、Ollama 上的 Phi-3.5-mini、FastAPI、React。

## 团队分工（来自已提交的 Workbook 1）

- Shervan Shahparnia：仿真（Ubuntu/ROS 2/Gazebo、多机器人世界、冲突场景）
- Yumeng Ren（Lorena）：LLM 规划（Ollama 节点，让 LLM 输出短计划，接收拒绝理由重规划）
- **Haoran Jiang（我）：共享检查层**（规则、plan 格式、跨计划检查、fairness 检查）
- Mu Chen：评估与日志（复现前一组结果、日志、指标）。注：telemetry 可能转给了我，若确认需在 Workbook 2 更新

我的排期：
- Sprint 1（295A 第 1–3 周）：列出第一版 safety / fairness / policy 规则
- Sprint 2（第 4–6 周）：定义 plan 格式；实现跨计划的 safety 和 policy 检查
- Sprint 3（第 7–10 周）：fairness 检查（追踪谁总在让行）；配合两机器人重规划 demo
- 295B：根据实验调整规则、复核每条拒绝是否正确、写论文中检查器部分

个人时间线：10 月下旬 checker 核心能跑 → 更新简历开始投递 new grad SDE / 后端 / AI infra；11 月中两机器人 demo + 初步对照数据。

## 工作约束（严格遵守）

1. **只描述真正做过的事。** 不在简历、README、报告里写未实现的功能或未测量的数字。性能数字必须实测并注明条件；没测就留空，不估算。
2. **我需要能讲清机制。** 实现时说明为什么这样设计、有什么权衡、换方案会怎样。面试会深挖。
3. **改代码前先解释。** 测试失败时先贴报错、说明原因，确认后再改。不要静默修改设计决策。
4. **写法：主干用通用工程语言，领域术语放技术栈。** 本质是「中间表示设计 + 跨实体冲突检测 + 策略引擎 + 失败反馈重试」，要让非机器人方向的后端面试官也看得懂。
5. 检查器核心保持纯 Python、不依赖 ROS，用假数据 + pytest 开发；以后再包成 ROS 2 节点。

## 当前代码

- `fleet_checker/checker/plan.py`：plan 数据结构、校验、占用区间展开
- `fleet_checker/tests/test_plan.py`：16 个测试，全部通过
- `fleet_checker/docs/rules_v1.md`：Sprint 1 规则清单（10 条规则 + 冲突解决 R-1）

数据分三块，刻意分开：
- **Plan**：单个机器人的意图，不可信输入。LLM 给出 action 和 region，时间由确定性的"计划编译器"填写，不让 LLM 估时间。
- **WorldMap**：区域（类型、容量、中心坐标、对称的相邻关系）+ 非地点的独占资源（如电梯呼叫）。
- **WorldSnapshot**：当前时间、各机器人当前所在区域、观测到的人。
提出计划的一方只控制 Plan，没法谎报容量、编造区域或给自己加优先级。

关键设计决策：
1. `robot_id` 只放在 Plan 上，不在每个 step 重复。
2. 容量属于地图，不属于计划。
3. 门、充电桩、电梯都是有容量的 region；`holds` 只用于不是地点的资源。所以空间冲突和资源冲突是同一个问题：**带容量的区间重叠**。
4. Plan 里**没有 priority**：LLM 会填最大值，也和 fairness 冲突。优先级由策略层根据 task_type 和让行记录决定。`extra="forbid"` 会拒绝 LLM 多写的字段。
5. `plan_id` + `revision`：重规划时，拒绝理由和日志能指向具体版本的具体步骤。
6. step 必须首尾相接（无空隙、无重叠），区间左闭右开 `[t_start, t_end)`。没有空隙，机器人才始终"有位置"。
7. 一个 step 只占一个 region，穿过多个区域要展开成多个 step。
8. Pydantic `frozen=True`：检查过的计划不能再被修改。
9. 结构错误（`parse_plan`）和语义错误（`validate_plan`）用同一种 `Violation(code, message, refs)` 格式，方便统一反馈给 LLM 和写日志。
10. Edge conflict（对向互换）不单独实现检测器，在容量检测器里识别：`margin > 0` 使交换表现为容量超限，检测器判断涉及相邻 region 的方向互换后标注 `EDGE_SWAP` code。
11. 有人区域的单机器人动作限制（S-3）推迟：前一组的 per-robot gate 已覆盖，共享检查层优先做跨机器人规则。
12. 区域准入策略（P-2）放在**单独的策略配置**，不放 Region 上——规则可能按时段或部署环境变化，地图不应因策略调整而修改。
13. 冲突时谁重规划：累计让行少的让行（fairness-aware），平局按 task_type 排序。让行失衡（max - min > K）是评估指标，不是触发拒绝的 violation。
14. 连续拒绝 M 次触发饥饿保护：该机器人获得优先权，冲突方被要求重规划。
15. 同一组冲突重规划超 N 轮不收敛，切换到确定性兜底（按优先级串行通过），保证活性。

占用视图 `claims(plan, margin, hold_final)`：
- `margin` 把每个区间两端各加宽一点。不加的话，两车跨边界**迎面互换**会被漏检，因为左闭右开让交接恰好在同一时刻发生（即 CBS 的 edge conflict）。margin 同时吸收时间估计误差。取值后面靠实验定，太小漏检、太大误报。
- `hold_final`：计划结束后机器人停在最后一个区域（占用到 +inf），所以停在通道里的机器人会挡住后来的车。

## 前一组代码（github.com/Preet-Pandit2/AI-Ethics-in-Autonomous-Robots）

他们是**逐帧反应式**的单机器人系统：每个 tick，LLM 根据当前距离等信息输出 GO/SLOW/STOP；arbiter 只在输出格式正确、未过期、置信度够时采纳，否则退回默认指令；gate 在距离过近时把速度清零；decision_logger 写 CSV。规则参数在 `implementations/schema/gate_rules.json`。

他们**没有带时间和区域的多步计划**。我的 Plan 层是新加的，位于他们单机器人 arbiter/gate 之上：共享检查层提前发现跨机器人冲突，他们的 gate 是每台机器人的最后一道急停，两者互补。
复用分工：Shervan 用他们的世界和机器人模型，Yumeng 用 `llm_ollama`，Mu 用 logger 和指标脚本；整套原样跑在每台机器人上就是 Baseline 2。
**需要和 Yumeng 对齐**：LLM 输出的短计划格式要符合 Plan schema（action 词表、region 只能从地图选、LLM 不填时间）。

## 开发命令

```bash
source .venv/bin/activate    # .venv 在 295a 根目录
cd fleet_checker && pytest -v # pytest 要在 fleet_checker/ 下运行（pyproject.toml 配了 pythonpath=["."]）
```
需要 Python 3.10+（用到 `itertools.pairwise`，且 Pydantic 运行时要解析 `X | Y` 类型注解）。

## 下一步

1. ~~Sprint 1：写第一版规则清单~~ → 已完成，见 `fleet_checker/docs/rules_v1.md`
2. Sprint 2：容量检测器（S-1，含 EDGE_SWAP 标注）——sweep line 扫描，同一 resource/region 在同一时段被超过容量数的不同机器人占用就产生 `Violation`。
3. Sprint 2：人员聚集检测器（S-2）——用 region 中心坐标和 `WorldSnapshot.humans`，某时刻离同一个人 R 米内的机器人数超过上限即违规。
4. Sprint 2：单计划策略检查（P-1 通道终停、P-3 时长上限）。
5. Sprint 2：`FairnessLedger` 数据结构 + 冲突解决逻辑（R-1）。
6. Sprint 3：饥饿保护（F-2）、重规划不收敛兜底（P-4）、区域准入策略配置（P-2）。
