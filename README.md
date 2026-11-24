# 家校双邀请试验与下一轮入组

数据来自独立设计的合成业务记录，不含真实人员资料。文件为 UTF-8 JSON。

## 资料字典

`study` 中的时间都是从项目第 0 日算起；跟踪窗口从对应的一轮邀请起算，**约定窗口固定为 12 个项目日**
（首批第 3 日派发、第 15 日窗口关闭，第 16 日查看时首批成熟；补发批次第 12 日派发、第 24 日窗口关闭，
恰在最终查看日成熟）。`students` 是学生、家庭和班级的归属；`classes` 是校区、时段、名额和历史原分配。
`original_draw` 冻结历史入组可选的跨班分配、实际抽中项和对应概率。`dispatches` 保留本来分给学生的
两种邀请及是否发出，不能用实际到访反推入组。家长邀请以家庭为单位，故同户的两份记录可能是同一邀请或
冲突证据；家长结果不要按学生人数重复计算。`followup` 是查证当日看到的回执，没有回执的一行不能证明
已经完成观察。`amendments` 从登记日开始可见，校正家庭事实不改变已发送凭证。`next_round` 提供下一轮
上限和已承诺名额，已发送旧凭证仍可用于跟踪。任一阶段的多重比较总错误限额以 `look_alpha` 为准，
比较数量在首次查看前登记并锁定。

## 工具结构（`trial_tools/`）

| 模块 | 职责 |
| --- | --- |
| `records.py` | 资料载入与契约核对；派发必须与冻结抽中批次一致，同班一轮邀请必须一致 |
| `households.py` | 应用补录解析家庭/手足/跨班连接；登记家长邀请冲突但不"修正"成整齐臂别 |
| `design.py` | 设计审计、受限随机化求解、可发出安排、分配机会、不可比较范围 |
| `outcomes.py` | 跟踪窗口与成熟度；拒绝是履行结果，无来源/无机会/窗口未结束/窗口外日志一律删失 |
| `analysis.py` | 按原随机安排的 ITT 两因素估计、班级整群 CR1 与多向稳健方差、随机化核对 |
| `monitor.py` | 三次查看的误差限额与阶段判断；阶段查看只登记信号，不得提前宣布有效 |
| `ledger.py` | 只追加的 JSON Lines 决策台账，逐次入组决定可追溯 |
| `cli.py` | 命令行入口，配合根目录的 `run_trial.py` 在隔离模式下运行 |

## 读取与核对

使用 Python 3.11，本地虚拟环境可按 `python3 -m pip install -r requirements.txt` 安装锁定的数值工具。
隔离运行环境（`python3 -I`）已自带 numpy/scipy 时可直接使用下面的启动器，它会把仓库根目录登记到模块路径，
避免在数据目录内执行解释器。

```bash
python3 -m json.tool data/records.json           # 查看原始记录
python3 -I run_trial.py audit data/records.json  # 冻结设计、共同支持、污染范围核对
python3 -I run_trial.py households data/records.json
```

## 可反复办理的流程

```bash
# 1) 下一轮入组：受限随机化给出 4 个等概率可行批次、抽中安排、每班边际机会、service_only 清单
python3 -I run_trial.py plan data/records.json --seed 20261124 --ledger run/ledger.jsonl

# 动态场景：新增班级（无历史凭证，参与本轮随机化）
python3 -I run_trial.py plan data/records.json --new-class C24:S2:T0:8

# 动态场景：新发现一对手足（连接到已污染班级会整体转服务保留；干净连接强制家长臂别一致）
python3 -I run_trial.py plan data/records.json --sibling-link F201:C08,C16

# 2) 各查看日的成熟度与删失分布（缺失绝不记为零事件）
python3 -I run_trial.py status data/records.json --look 9
python3 -I run_trial.py status data/records.json --look 16
python3 -I run_trial.py status data/records.json --look 24

# 3) ITT 两因素分析与门槛判断（比较数量 4 在首次查看前锁定）
python3 -I run_trial.py analyze data/records.json --look 16 --comparisons 4 --ledger run/ledger.jsonl
python3 -I run_trial.py analyze data/records.json --look 24 --comparisons 4 --ledger run/ledger.jsonl

# 4) 冻结备选批次上的随机化推断核对
python3 -I run_trial.py randomization-check data/records.json --look 24

# 5) 追溯一次具体入组决定：用了哪批分配、哪些成熟结果、哪些冲突未解决
python3 -I run_trial.py trace run/ledger.jsonl --entry E0001
```

## 关键业务规则的落地位置

- **共同支持与不重合**：四个组合在每个学校、每个时段都必须出现；`design.py` 的回溯求解器带
  MRV、回填检查与节点预算，前置检查即可识别"某校干净班级不足 4 个"的不可行情形。
- **家庭不矛盾、同班一致**：无冲突跨班手足的已发家长臂别转成班级锁定；新手足连接在无已发凭证时
  作为臂别相等约束交给求解器；冲突家庭波及班级整体 `service_only`，服务保留但退出完整两因素比较。
- **冻结不可改**：已发邀请不重新归组（`records.py` 校验与冻结批次一致），已承诺名额受保护，
  只有 `sent=false` 的未通知安排允许调整，清单由 `audit`/`plan` 单独列出。
- **结果编码**：`拒绝` 按已观察的零事件处理；`未取得来源/来源缺失`、无跟踪行、窗口未结束、
  日志早于邀请或晚于窗口关闭，分别记为 `no_source`、`no_opportunity`、`window_open`、
  `log_outside_window`，均为右删失。
- **相关性**：学生端以随机化单位（班级）做 CR1 整群稳健方差，并附班级×家庭多向稳健核对；
  家庭端以家庭为单位做 HC1，并附家庭×班级多向核对。
- **多次查看**：`look_alpha = [0.008, 0.012, 0.03]`，比较数量锁定为 4 时，单次比较门槛依次为
  0.002、0.003、0.0075；阶段查看即使某对比越过门槛也只登记信号，只有最终查看可宣布有效。

## 测试

```bash
python3 -I run_tests.py        # 58 个单元/端到端测试，覆盖上述全部规则与边界
```
