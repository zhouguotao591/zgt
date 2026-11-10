# Windows服务器发布版本

此目录为独立发布副本。唯一预置业务数据为2026年历史开奖记录，具体期数和截止期号见RELEASE-MANIFEST.json。没有从本地复制会员、管理员、策略配置/订单、机器人绑定/密钥、历史回测、AI模型或运行日志。2024/2025开奖数据不在包中。历史回测单次上限100,000期，全年数据超过上限时请分段选择。

## 部署

1. 将整个目录解压到服务器独立目录，例如D:\pc28-online。保留可写的data目录，勿覆盖服务器已运行实例的数据库。
2. 安装Python 3.11+，执行 `powershell -ExecutionPolicy Bypass -File .\scripts\安装依赖.ps1`。这会创建本目录独立.venv，安装时区、计算和两步验证依赖；需要服务器能访问pip包源，也可预先配置pip镜像。发布包不包含Python安装器或本机虚拟环境。
3. 执行 `powershell -ExecutionPolicy Bypass -File .\scripts\配置上线域名.ps1 -PublicOrigin https://你的域名`，Origin必须与用户浏览器访问的HTTPS域名完全一致，不支持通配。
4. 使用IIS URL Rewrite + ARR配置反向代理与HTTPS证书，转发到127.0.0.1:8028。将deployment/iis-web.config.example复制到**单独新建的IIS空站点目录**并命名web.config，启用ARR代理。公网域名须与上一步相同；后端接受本机Host或已配置域名。若修改后端端口，同时修改反代目标。不要把本项目根目录或data目录直接设为IIS静态站点根目录。
5. 按下方「两步验证升级」初始化加密密钥，再运行 `后台启动.ps1`。检查 `data/watchdog-state.json` 的status=running；健康接口为服务器本机 http://127.0.0.1:8028/api/health。只开放IIS的HTTPS入口，Python后端保持127.0.0.1。
6. 服务首次启动会生成新的data/admin-setup-code.txt。在服务器读取初始化码，浏览器访问 `https://你的域名/admin/setup` 自行创建管理员。公网Origin启用后登录Cookie启用Secure，请使用HTTPS域名操作后台，不使用本机HTTP登录。
7. 重新配置线上策略、会员邀请及推送平台。所有模拟与机器人初始关闭；采集默认启用，只访问官方及配置的备用开奖源，不与开发电脑同步数据库或账号。
8. 需要登录自启动时，再运行 `设置登录自启动.ps1` 注册当前Windows用户任务；停止使用 `停止系统.ps1`，正常停止不会被守护重启。

IIS官方配置说明：https://learn.microsoft.com/en-us/iis/extensions/url-rewrite-module/reverse-proxy-with-url-rewrite-v2-and-application-request-routing

## 两步验证升级

1. 升级前使用 SQLite 一致性备份保留现有数据库。重新执行依赖安装脚本，确保运行服务的 Python 使用新依赖。
2. 由运行服务和登录自启动任务的同一 Windows 账户执行 `powershell -ExecutionPolicy Bypass -File .\run.ps1 mfa-key-init`。命令在 `data/private/mfa.key` 创建密钥并限制文件 ACL。该命令只需执行一次；已有密钥必须保留，并与数据库分开备份。若初始化失败后只留下空文件，由运维确认其为空再清理、重试。
3. 在受控维护窗口重启服务，再通过 HTTPS 登录管理后台。管理员首次登录必须绑定 Google Authenticator，旧后台会话需重新登录。扫码确认后，请保存页面上的一次性恢复码。服务器和手机需开启自动时间同步。
4. 会员默认关闭两步验证。管理员在「会员管理」为指定会员启用或关闭验证，也可以填写原因并用自己的新动态码确认解绑。启用且未绑定的会员在下次登录会员中心时扫码；关闭时保留原绑定。公开前台登录流程保持现状。
5. 会员中心使用独立 Cookie 和 `/member/api/*`；旧 `/api/member/*` 已停用。若反向代理按路径放行，需包含 `/member/api/*`，并确认管理后台和会员中心均通过 HTTPS 访问。

丢失管理员验证器时，可凭密码和一次性恢复码进入重绑。无恢复码时，由服务器运维执行 `powershell -ExecutionPolicy Bypass -File .\run.ps1 mfa-admin-recovery 管理员用户名 --reason "已核实身份"`，安全交付输出的十分钟重绑码。此操作撤销该管理员的现有后台会话。若加密主密钥丢失，应从独立备份恢复原密钥。

## 数据与隔离

- 预置库：data/pc28.sqlite3。仅draws表预置2026来源年份的记录，其余表为空或首次启动初始化。
- RELEASE-MANIFEST.json提供源文件校验、2026开奖期数、期号范围和包内文件SHA256。
- 默认管理员、会员、密码、邀请码、信号Token、Windows DPAPI密钥均未打包。首次启动才在目标服务器生成新初始化码和控制密钥。
- 不安装本机定时任务、不启动本发布副本，也不会修改D:\pc28的运行配置。服务器启用后会独立采集新开奖，不会连接开发电脑。
- 源文件时间与验证状态保留，不能核验的历史UTC时间不伪造；未确认时间及冲突的排除数量见清单。
- 日志在data/logs，守护会自动重启异常退出/持续失联的服务。线上必须重新配置自己的机器人地址和密钥；127.0.0.1指线上服务器自身。

## 发布验证

使用 `scripts/校验发布包.ps1` 检查清单与数据库。首次启动前不应出现任何本地业务订单或会员数据。依赖装好后可运行 `python -m unittest discover -s tests` 验证代码；测试只在临时目录运行。构建方的验证摘要见RELEASE-VERIFICATION.json。

本地开发版的历史说明另存于docs/LOCAL-README.md，仅作代码参考，部署步骤以本说明为准。
