# 车型配置管理

持久目录统一为同级工作区的 `工作台数据/database/vehicle-config/`；独立运行与门户运行读取同一位置。可用 `PLANHUB_DATA_ROOT` 指定数据根（相对路径按应用根解析），应用原有显式数据目录参数继续优先。程序发布不携带业务数据库；发布版使用 `planhub-runtime/database/vehicle-config/`。共享 Excel 仍在原共享目录，数据库集中不改变业务口径。首次升级必须停服迁移，旧目录改名为 `.pre-central-*` 恢复备份，不再参与运行。

首页直接读取小订与首销草稿中的车型资料。车型配置维护《小订及首销数据整理》车型汇总 A–N 列及订单脚本的五类规则，日常填数在同一网页的「小订首销填报」办理。前端无外网依赖。

## 启动与停止

开发环境双击 `启动车型配置.cmd`，或在项目根运行 `./start.ps1`。启动器向上查找 Project/.venv，不回退到系统 Python。依赖见 requirements.txt（openpyxl）。前台 Ctrl+C 正常停止；门户接入后由工作台管理进程。独立入口默认 http://127.0.0.1:8790/vehicle-config/，端口可覆盖。

```powershell
./start.ps1 --port 8790 --public-prefix /vehicle-config/
../.venv/Scripts/python.exe -B -m unittest discover -s tests -v
./start.ps1 --show-paths
$releaseVersion = Read-Host '输入新发布版本'
../.venv/Scripts/python.exe -B scripts/build_release.py --version $releaseVersion --destination "artifacts/releases/vehicle-config-$releaseVersion"
```

## 运行诊断与验收

在任意模块打开顶部「数据工具 → 运行路径」，可查当前服务使用的目录、规则脚本和路径来源。离线运行 `./start.ps1 --show-paths` 可查看按本次启动参数计算的路径，无须启动服务。使用发布包运行时，页面还会展示包版本和构建时的 Git 提交（旧包可能缺失）。

| 资产 | 开发默认位置 | 工作台部署默认位置 |
| --- | --- | --- |
| 车型库、规则库与备份 | `Project/工作台数据/database/vehicle-config/` | 运行目录下的 `database/vehicle-config/` |
| 订单规则脚本 | `Project/订单分析/scripts/config/shared_config.py` | `sopSharedConfig` 配置的相对路径，模板值为 `config/shared_config.py` |
| 网页导入、导出 Excel | 从浏览器选取文件，导出到浏览器下载目录 | 同开发版 |

快速验收使用共享 Python 运行 `-B scripts/verify.py`，覆盖单测和已导入数据的车型、日程、填报及导出流程。每次 push 和 PR 也在 Windows CI 上运行快速验收。完整本地验收加 `--full`，需要 Edge、Playwright、Node 和同级计划工作台。首次先核对本机界面并运行 `-B tests/browser_header_check.py --approve` 建立两种宽度的标题栏截图基线；截图放在忽略的 artifacts 目录，后续运行只比较。完整验收还会在填报和筛选过程中测量帧间隔，结果在 artifacts/motion/timings.json。更新系统字体或 Edge 后应人工复核差异。验收发布包加 `--release <发布目录>`，测试使用临时数据库。发布清单中 sourceRevision 是载荷指纹，从 Git 工作区构建时 gitCommit 和 sourceDirty 标记提交及源码是否含本地修改。

## 前台维护

### 车型与日程、小订首销填报

| 入口 | 维护内容 |
| --- | --- |
| 车型与日程 → 车型资料 | A–D 的代际名、二级代际名、产品档位和能源类型 |
| 车型与日程 → 发布日程 | E–N 的发布类型、发布时间或时段、留资日期、小订与首销起止日期及天数 |
| 代际规则 | 代际显示名称、代际合并 |
| 电池与选配 | 电池包规则（含版本和 SKU 匹配条件）、选配名称合并、选配金 |
| 小订首销填报 | 每日小订、六类阶段进度、车型汇总 P–AD 的经营指标 |
| 变更记录 | 车型日程与填报的发布记录、脚本规则的变更与恢复 |

车型与日程和小订首销填报共享草稿及发布批次。现有导入草稿打开即可维护。服务可从空库开始。使用任意模块的「数据工具 → 导入小订首销旧表」选择《小订及首销数据整理.xlsx》，预览八张表的变化后确认替换当前小订与首销草稿。可以撤回最近一次导入，报表在重新发布后读取新数据。网页录入、保存、发布直接使用数据库。每日录入支持批量粘贴，空值与实际零值分别保存。

整表迁移需要能识别「车型汇总」「小订by天」和六张进度表。车型汇总按表头匹配，支持列顺序变化及常见旧名称。含义不明的列、缺少的工作表、没有缓存结果的公式会在正式导入前提示核对。

「车型与日程」导出车型汇总的 A–N 列。需要完整旧表格式时，在「小订首销填报」导出，可选草稿或发布版本、工作表、车型及逐日小订的日期范围。生成的 Excel 通过浏览器下载。服务不在共享目录生成或维护工作簿。

业务数据保存在数据目录的 `campaigns/campaigns.sqlite3`。发布产生带修订号的 `campaigns/published.json`，下游随报表批次读取此快照。经工作台身份验证也可读取 `GET api/campaign/published`，查询历史版本时附上 `?revision=版本号`。Excel 迁入后先核对历史阶段、实际零值、每日数量以及各进度区段，再切换下游数据源。

### 订单配置规则

「代际规则」管理 `GEN_DISPLAY_NAME_MAP` 和 `MERGE_GROUPS`。「电池与选配」管理 `BATTERY_RULES`、`MERGE_OPTIONS` 和 `OPTION_CONFIG`。桌面在标题行显示当前视图适用的筛选。手机用模块下拉框切换栏目，点「筛选」展开条件。

切换栏目保留未保存的车型配置和脚本规则。保存草稿、导入导出、撤销和发布作用于当前视图所属的数据。电池规则保留优先级，使用代际、动力及版本、SKU 关键词匹配电池容量。选配金保留金额、默认选配和版本包含、版本精确、座位限制。

在任意模块打开顶部「数据工具 → 从脚本重新载入」，可核对服务当前使用的 `shared_config.py`。确认后，五类规则的草稿与已发布版本都更新为脚本内容，当前车型配置编辑和筛选会保留。数据一致时直接提示结果。底部「重新载入草稿」用于读取当前视图已保存在数据库里的草稿。

开发环境默认目标为 `Project/订单分析/scripts/config/shared_config.py`。可通过服务参数 `--shared-config` 或环境变量 `VEHICLE_CONFIG_SHARED_CONFIG` 指定；正式部署须配置目标脚本的实际路径，并授予服务账号该文件与父目录的写入权限。浏览器不能自选服务器路径。规则入口沿用工作台代理、身份、来源、CSRF 和只读限制。

规则草稿保存在当前数据目录下的 `rules/rules.sqlite3`。点击「发布并更新脚本」会显示目标文件、规则差异和 Python 差异，确认后备份至 `rules/backups/`，再原子替换目标文件。脚本之外的源码不修改；不执行或导入源脚本。重复定义、非字面量配置、未知选配限制会阻止写回。编辑修订号和源文件 SHA-256 均参与冲突校验，外部修改后须显式「从脚本重新载入」。发布日志支持意外中断后的恢复；有未决冲突时拒绝继续发布。预览和发布都会按 SOP 的归并口径检查 `MERGE_GROUPS`：全角字符、独立的 BETA 标记与空白归一后，组名须唯一、每个成员只能出现一次，组不能再作为成员。旧规则可先载入，在草稿中修正后再发布。源文件原有的编码声明和成员拼写会保留。备份、数据库和真实规则不进入 Git。

五类规则各有 Excel 工作表；支持模板下载、多文件预览、批量粘贴名称映射、复制、删除确认及撤销。列表条件使用单元格内换行；`[]` 可显式清空列表，空白单元格和缺行保留原值。导入电池优先级时须为受影响的规则填写不同序号。导入预览不保存原文件，应用到编辑区后仍需保存或发布。规则历史可恢复到编辑区，再确认发布。

选配金支持固定金额和按条件定价。网页可逐条维护匹配条件及金额，也可单独添加默认价。Excel 的「金额」列接受数字或 JSON 对象，例如 `{"条件甲":3000,"default":1000}`。缺少默认价的规则会保留原有条件，写回脚本时也保留条件顺序。

SOP 从发布的共享脚本读取 `MERGE_GROUPS`。订单报表展示的数据来自已生成的 Excel；发布脚本规则后，已有 Excel 和历史报表仍是原结果，需要通过对应的生成流程更新。

## 持久数据与发布口径

数据路径优先使用 `--data-dir`；否则使用 `PLANHUB_DATA_ROOT/database/vehicle-config`，未设置根目录时为同级 `工作台数据/database/vehicle-config`。发布包统一传入 `planhub-runtime/database/vehicle-config`；旧版共享目录中的数据须先停服迁移。相对 `--data-dir` 仍按进程目录解析；相对 `PLANHUB_DATA_ROOT` 按项目根解析。

当前网页使用 `campaigns/campaigns.sqlite3` 和 `rules/rules.sqlite3`。旧版独立车型档案的 `catalog.sqlite3`、JSON 快照及原有 API 继续供已有调用方使用。这些旧档案与小订首销导入记录分别存储。本次界面调整无需迁库或重新导入。备份时停服并备份整个数据目录。

## 工作台接入与部署

启用代理密钥后，每个业务请求都需要可信代理身份。`--require-proxy-identity` 还会在启动阶段检查密钥，缺失时终止启动。健康检查可使用回环地址或 `--public-origin` 中配置的主机。

车型配置与脚本规则的变更记录每页 40 条，搜索覆盖全部历史。若脚本发布中断且源文件已被外部修改，重新载入前须确认。该次待发布内容会保存在「变更记录 → 脚本变更记录」中，可恢复到编辑区。

应用 ID `vehicle-config`，公共前缀 `/vehicle-config/`。工作台剥离前缀代理至独立回环服务；所有浏览器 URL 使用相对路径。健康接口 `/healthz` 轻量只读，返回 ok、app、public_prefix。前后端同版封装为工作台通用 app-release.json + payload 发布包，构建拒绝覆盖已存在目录，文件带 SHA-256；正式运行使用独立 Python 运行时。门户部署模板提供端口、公共来源、共享根和独立服务参数，发布工具只更新本应用。

独立运行默认记录 `本机:系统用户名`。工作台 Docker 版通过公司认证网关校验访问资格，门户将可信账号和密钥写入 `X-Config-User`、`X-Config-Proxy-Key` 转发到车型配置。启动器从包外 `planhub-runtime/config.json` 的 `vehicleSecret` 注入 `VEHICLE_CONFIG_PROXY_SECRET`，并传入 `--require-proxy-identity`。密钥错误或账号缺失的业务请求返回 403。`--read-only` 控制整个服务的写入。

部署验收应覆盖未获部门访问资格的请求、伪造身份、操作人记录、保存后重启和 Excel 导入导出。公共地址须与 `--public-origin` 一致，健康接口 `/healthz` 单独用于检查服务可用性。

车型资料的已发布快照与订单规则脚本分别发布。接入车型快照的报表应记录 `revision` 和汇总批次，以便复核历史结果。
