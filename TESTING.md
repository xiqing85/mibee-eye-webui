# 三个设备环境测试指南

统一 Web 层（SPEC v1 + 共享前端）的分层测试方法。从快到慢四层，越往下越接近真机。

## 环境一览

| 环境 | 地址 | 凭证 | 备注 |
|------|------|------|------|
| raspi-rs（`<rs-device-host>`） | `http://<rs-device-ip>:8088` | 配置文件 `web.username`/`web.password` | 部署主机 `ssh your-user@<rs-device-ip>`，服务 `mibee-eye-rs.service` |
| raspi-go（`<go-device-host>`） | `http://<go-device-ip>:8088` | 回落 ONVIF 凭证 | `ssh your-user@<go-device-ip>`，服务 `mibee-eye` |
| notebook（本地） | `https://127.0.0.1:8443` | 首启 setup 自建 | 自签 TLS，需接受证书；SQLite `mibee_rec.db` 在工作目录 |
| mock（无设备） | `http://127.0.0.1:8090` | 任意 | `make mock`，SPEC 一致的假后端 |

两台 Pi 的凭证在各自设备配置里（rs: `/etc/mibee-eye/config.toml` 的 `[web]` 节；go: `~/<go-device-host>/configs/config.yaml` 的 `web:` 节）。

## 第 0 层：纯前端（不碰设备）

```bash
cd mibee-webui && make mock   # :8090
# 浏览器打开 http://127.0.0.1:8090 —— mock 支持 MOCK_PREAUTH=1 跳过登录
```

改前端时的日常回路：改 `static/` → mock 验证 → `make sync-*` 同步。

## 第 1 层：API 冒烟（一条命令，零依赖）

```bash
tools/smoke.sh http://<rs-device-ip>:8088 <密码>          # rs
tools/smoke.sh http://<go-device-ip>:8088 <密码>          # go
tools/smoke.sh https://127.0.0.1:8443 <密码> admin        # notebook
```

覆盖：health 信封 → 认证态（401 / 首启 503→setup）→ 登录 → me → capabilities（`spec_version`）→ cameras → config → SSE 探测 → 登出后会话失效。全过退出码 0。

## 第 2 层：浏览器自动化走查（Playwright）

一次性安装（无 node 依赖；仓库根 `.venv/` 已 gitignore）：

```bash
python3 -m venv .venv && .venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

跑（自动处理首启 setup / 错密码 / 登录 / 相机启动 / 直播播放头推进 / 各视图 / 语言主题 / 登出；截图落 `tmp/walkthrough-<tag>/`；退出码非 0 = 有问题）：

```bash
.venv/bin/python tools/ux_visual_check.py (mock, 47 项交互/视觉断言 + 截图) 与 tools/browser_walkthrough.py http://<rs-device-ip>:8088 <密码> admin rs
.venv/bin/python tools/browser_walkthrough.py http://<go-device-ip>:8088 <密码> admin go
.venv/bin/python tools/browser_walkthrough.py https://127.0.0.1:8443 <密码> admin nb
```

注意：脚本会先点"相机"页启动停止中的相机再验证直播，因此 notebook 全新首启（相机未启动）也能一遍通过。

退出码非 0 时看 `ISSUES:` 列表和截图。人工抽查建议看三张图：`04-live.png`（画面在动）、`06-settings.png`（配置编辑器完整）、`07-status.png`。

## 第 3 层：notebook 本地起服务

```bash
cd ../notebook-cam
cargo build --release          # 已有 target/release/mibee-rec 可跳过
rm -f mibee_rec.db*            # 想走首启 setup 流就删库；不删则用已有账号
(setsid ./target/release/mibee-rec --config config.toml > tmp/run.log 2>&1 < /dev/null &)
curl -sk https://127.0.0.1:8443/api/health
```

首次访问浏览器会进 setup 表单（用户名 + 密码 ≥8 位 + 确认），建号即登录。

## 第 4 层：真机互联回归（部署后必做）

改动的代码若触碰协议层（见工作区 AGENTS.md 分层判定表），部署到 Pi 后必须过一遍 NVR 互联面：

```bash
# go 端（.118，注册生产 NVR .30）
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://<go-device-ip>:8088/snapshot   # 200 video/H264
printf 'DESCRIBE rtsp://<go-device-ip>:8554/stream RTSP/1.0\r\nCSeq: 1\r\n\r\n' | timeout 4 nc <go-device-ip> 8554 | head -1   # 200 OK
ssh your-user@<go-device-ip> 'journalctl -u mibee-eye --since "-3 min" | grep -iE "REGISTER|SUBSCRIBE"'
curl -s -X POST http://<go-device-ip>:8080/onvif/device_service -H 'Content-Type: application/soap+xml' \
  -d '<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>' | head -c 200

# rs 端（.104）同理，服务名 mibee-eye-rs，地址换 <rs-device-ip>
```

判定：`/snapshot` 无鉴权 200、RTSP DESCRIBE 应答元素名不变（NVR 做本地名匹配）、GB28181 REGISTER successful + SUBSCRIBE 200 OK、ONVIF 响应字节稳定。

## 部署回路（改完代码后）

```bash
# rs：workstation 交叉编译 → 推 → 重启
cd ../mibee-eye-rs && cargo zigbuild --release --target aarch64-unknown-linux-gnu
scp target/aarch64-unknown-linux-gnu/release/mibee-eye-rs your-user@<rs-device-ip>:/tmp/mibee-new
ssh your-user@<rs-device-ip> 'sudo systemctl stop mibee-eye-rs && sudo install -m 755 /tmp/mibee-new /usr/local/bin/mibee-eye-rs && sudo systemctl start mibee-eye-rs'

# go：
cd ../mibee-eye-go && GOOS=linux GOARCH=arm64 go build -o /tmp/<go-device-bin> ./cmd/server
scp /tmp/<go-device-bin> your-user@<go-device-ip>:/tmp/<go-device-host>-new
ssh your-user@<go-device-ip> 'sudo systemctl stop mibee-eye && install -m 755 /tmp/<go-device-host>-new ~/<go-device-host>/<go-device-bin> && sudo systemctl start mibee-eye'

# notebook：本地 cargo build --release 直接跑
```

> 不要在 Pi 上编译。前端改动先在 mibee-webui `make sync-*` 再编译设备仓库。
