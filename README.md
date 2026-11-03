# mall-performance

基于 JMeter 的 mall-admin 本地性能测试项目，包含登录与品牌分页查询场景、结果有效性校验、重复性分析，以及主机和应用资源监控。

## 已实现

- JMeter 非 GUI 执行，支持线程数、启动时间和持续时间参数化。
- Once Only Controller 登录、JWT 提取、Bearer Token 鉴权、CSV 分页参数化及 HTTP/业务状态断言。
- Python 统一编排 JMeter 与监控，按 JTL 样本检查错误并校验监控时间覆盖范围。
- 使用 psutil 采集主机、mall 和 JMeter 进程指标，通过 Docker 采集 MySQL、Redis 指标。
- 保留 6 次有效正式执行的统计数据，排除登录全部失败的无效执行。

## 项目结构

```text
mall-performance/
├── config/maven-settings.xml       # Maven 公共镜像配置
├── data/brand_queries.csv          # 品牌分页查询参数
├── reports/                        # 仅提交代表性 statistics.json
├── testplans/
│   ├── mall_admin_smoke.jmx        # 单用户功能冒烟
│   └── mall_admin_baseline.jmx     # 持续负载场景
├── run_baseline_with_monitor.py    # 压测与监控编排
└── requirements.txt
```

## 环境要求

本次使用 Java 21、JMeter 5.6.3、Python 和 psutil 7.2.2。执行前需要：

1. mall-admin 已启动并监听 `localhost:8080`，Java 进程命令行包含 `com.macro.mall.MallAdminApplication`。
2. MySQL、Redis 容器名称分别为 `mysql`、`redis`，Docker CLI 可用。
3. Java 在 PATH 中，或通过 `--java` 指定 `java.exe`。
4. 安装 Python 依赖：

```powershell
python -m pip install -r requirements.txt
```

JMX 使用 mall 示例环境的默认本地账号 `admin/macro123`。可通过 JMeter 属性 `admin_username`、`admin_password`、`host` 和 `port` 覆盖；不要提交真实环境凭据。

## 执行

```powershell
python run_baseline_with_monitor.py `
  --run-id local-005 `
  --threads 5 `
  --ramp-time 10 `
  --duration 60 `
  --jmeter-jar "C:\tools\apache-jmeter-5.6.3\bin\ApacheJMeter.jar"
```

也可通过 `JMETER_JAR` 或 `JMETER_HOME` 环境变量指定 JMeter。脚本会自动创建输出目录，并拒绝覆盖相同 `run-id` 的已有结果。

生成内容：

- `results/baseline-<run-id>.jtl`：原始样本。
- `runtime/jmeter-baseline-<run-id>.log`：JMeter 日志。
- `reports/baseline-<run-id>/`：HTML 报告和统计数据。
- `results/monitor-host-<run-id>.csv`：主机及进程指标。
- `results/monitor-docker-<run-id>.csv`：MySQL、Redis 容器指标。

脚本最后输出样本数、错误数、监控覆盖范围和 `valid` 判定。JMeter 正常退出并不代表测试有效，仍需检查 JTL 和业务错误。

## 正式测试结果

以下均为本地 mall-admin 执行结果，统计包含登录和品牌查询样本：

| 场景 | 样本数 | 吞吐量/s | 平均响应/ms | P95/ms | P99/ms | 错误率 |
|---|---:|---:|---:|---:|---:|---:|
| 5 并发 baseline | 21,055 | 351.95 | 12.68 | 14 | 15 | 0% |
| 10 并发 r2 | 54,309 | 907.25 | 9.33 | 10 | 11 | 0% |
| 10 并发 r3 | 55,281 | 923.47 | 9.20 | 10 | 11 | 0% |
| 10 并发 r4 | 49,395 | 825.98 | 10.31 | 12 | 13 | 0% |
| 20 并发 r1 | 63,128 | 1,054.68 | 16.78 | 20 | 23 | 0% |
| 20 并发 r2 | 66,525 | 1,111.84 | 15.91 | 19 | 21 | 0% |

重复执行表明核心指标具有一定重复性，但并发增加并未带来同比例吞吐增长，20 并发下响应时间及尾部分位数上升。完整原始 JTL、监控 CSV 和 HTML 报告未提交到仓库，各场景仅保留 `statistics.json`。

## 结论边界

- 数据仅反映本次本地环境，不代表生产容量。
- JMeter 与被测系统同机运行，主机资源占用不能直接归因于 mall 或 MySQL。
- 部分正式运行虽然监控时间覆盖完整，但测试区间内仅有 3–6 个主机样本，因此不据此作精确瓶颈诊断。
- 当前 psutil 轻量监控版本已通过无负载功能验证，但未宣称已测得采集改造带来的性能提升。
- 尚未进行单变量调优对照，不宣称任何调优收益。
