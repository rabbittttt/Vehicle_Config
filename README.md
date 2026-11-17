# 车型配置管理

第一版独立维护车型、发布阶段、版本与配置适用关系、名称映射、汇总规则及变更历史。前端无外网依赖。不会自动生成 SKU，也不会更改订单、预测或 SOP 的历史资料。

## 启动与停止

开发环境双击 `启动车型配置.cmd`，或在项目根运行 `./start.ps1`。启动器向上查找 Project/.venv，不回退到系统 Python。依赖见 requirements.txt（openpyxl）。前台 Ctrl+C 正常停止；门户接入后由工作台管理进程。独立入口默认 http://127.0.0.1:8790/vehicle-config/，端口可覆盖。

```powershell
./start.ps1 --port 8790 --public-prefix /vehicle-config/
../.venv/Scripts/python.exe -B -m unittest discover -s tests -v
../.venv/Scripts/python.exe -B scripts/build_release.py --version 0.1.0 --destination artifacts/releases/vehicle-config-0.1.0
```

## 持久数据与发布口径

数据路径优先使用 `--data-dir`；其次 `SHARED_DATA_ROOT/车型配置`；开发默认 `Project/共享数据/车型配置`。工作台发布包的 Windows 试运行和 Docker 均应把 `--data-dir` 指向配置的共享根下的 `车型配置/`，默认 `planhub-runtime/shared/车型配置/`；不要另存到 `data/vehicle-config/`。相对参数按进程工作目录解析。正式包不依赖开发工作区，必须显式指定外部数据路径或共享根。

`catalog.sqlite3` 为事务主存储，包含草稿、已发布配置和操作记录；`current.json` 是已发布快照（revision 与 data），`drafts/current.json` 是草稿快照，`history/*.json` 是历史记录。快照以原子替换方式导出；若文件写入失败，接口报错，数据库已提交状态在重启时重新导出。消费者仅读取 current.json，不扫描草稿。备份应在服务停止后备份整个目录；不能只备份 current.json。持久目录不可放在发布包内。OS 生命周期锁阻止新旧进程同时打开同一目录，服务停止后操作系统自动释放锁。

保存使用全目录草稿修订号进行乐观并发检测；冲突返回 409，编辑内容保留在浏览器，可导出后重新载入。发布校验必填、重复名称、关联、日期及汇总成员重复归属；空矩阵为未配置。汇总组仅引用车型，不允许组嵌套，从结构上排除循环。恢复旧记录只进入草稿，新记录仍保留，车型/版本/配置项标为停用，再预览并发布。记录不可删除，改名保留编号。

导出 XLSX 包含 models、phases、versions、options、matrix、aliases、groups；列名是稳定字段键。先导出模板再导入。导入按 id 合并，缺少 id 的新行生成 UUID；空白单元格和缺行不删除或清空已有字段。公式拒绝导入。历史编号保留原值；旧系统字段需先人工对照模板，不猜测映射。当前共享车型信息目录没有可导入文件，默认不写入样例。

## 工作台接入与部署

应用 ID `vehicle-config`，公共前缀 `/vehicle-config/`。工作台剥离前缀代理至独立回环服务；所有浏览器 URL 使用相对路径。健康接口 `/healthz` 轻量只读，返回 ok、app、public_prefix。前后端同版封装为工作台通用 app-release.json + payload 发布包，构建拒绝覆盖已存在目录，文件带 SHA-256；正式运行使用独立 Python 运行时。门户部署模板提供端口、公共来源、共享根和独立服务参数，发布工具只更新本应用。

本机运行记录 `本机:系统用户名`，不假装是浏览器登录者。正式多人部署必须启用 `--require-proxy-identity` 和 `VEHICLE_CONFIG_PROXY_SECRET`（从服务器外置 secret 文件注入），并通过现有 Windows 身份边界配置 IIS 代理：清除客户端传入的 X-Config-User、X-Config-Proxy-Key，再以已验证的 AUTH_USER 和服务端密钥覆盖。缺少密钥或用户的业务请求返回 403；不设业务角色，所有已获部门访问权成员能力相同。`--read-only` 为全服务维护开关。需由部署人员在目标机配置此可信身份桥，不能仅开启普通反向代理后宣布上线；模板使用 proxyMode=existing 防止普通代理覆盖身份规则。健康接口无需身份，不能代替身份验收。

发布前在目标机验证部门外访问拒绝、代理伪造身份拒绝、真实用户记录、保存后重启、导入导出及前缀资源。不得自动应用 IIS 安全配置。工作台开发代理保留 Host 和 Origin，服务校验二者一致；正式公共来源通过 --public-origin 显式配置。

业务报表当前尚未适配此目录，发布配置不等于业务报表更新。未来消费方须记录 revision，将车型资料和汇总批次一起固化。
