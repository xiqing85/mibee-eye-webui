# MiBee Camera Web API 统一规范 (SPEC v1)

本规范定义 MiBee 摄像头设备 Web 管理 API 的统一契约，由三个设备实现：

| 实现 | 仓库 | 部署 |
|------|------|------|
| `mibee-eye-rs` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `mibee-eye-go` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `notebook-cam` (mibee-rec) | 本工作区 | 笔记本，HTTPS :8443（TLS 强制），多相机 CRUD |

配套参考前端：本仓库 `static/`（ES Modules，零构建），由三个设备仓库嵌入。

## 0. 总则

- **信封**：所有 JSON 端点（除注明者）使用统一信封。
  - 成功：`{"ok":true,"data":<payload>}`
  - 失败：`{"ok":false,"error":"<机器码>","message":"<人类可读>"}` + 语义化 HTTP 状态码
  - 二进制端点（快照 / MJPEG / MSE / metrics / 静态资源）不套信封。
- **错误码表**（`error` 字段取值，与 HTTP 状态一一对应）：

  | error | HTTP | 含义 |
  |-------|------|------|
  | `bad_request` | 400 | 请求体/参数非法 |
  | `unauthorized` | 401 | 未登录 / 会话过期 / CSRF 校验失败 |
  | `forbidden` | 403 | 已登录但无权执行 |
  | `not_found` | 404 | 资源不存在 |
  | `conflict` | 409 | 状态冲突（流已在运行等） |
  | `setup_required` | 503 | 首次启动，需先完成管理员设置 |
  | `rate_limited` | 429 | 限速 / 登录锁定 |
  | `not_implemented` | 501 | 能力通告存在但后端未实现 |
  | `internal_error` | 500 | 内部错误 |

- **版本化**：`/api/capabilities` 的 `spec_version` 标识本规范版本。同一 `spec_version` 内只允许增量变更；破坏性变更必须升版本。
- **认证**：会话 cookie + CSRF 双提交（见 §2）。除 §1 公开端点外，一切 API 需认证。静态资源（前端本身）公开。
- **扩展机制**：Core 端点三端必须实现；Extension 端点仅实现了的设备存在，且**必须**在 `/api/capabilities` 中如实通告。前端一切功能开关以 capabilities 为准，不做设备探测猜测。

## 1. 公开端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 存活探针。`{"ok":true,"data":{"status":"ok","uptime":<秒>}}` |
| GET | `/metrics` | Prometheus 文本格式（指标名各设备自定义，见附录 A5） |
| GET | `/`、`/style.css`、`/js/*` | 嵌入式前端静态资源 |

## 2. 认证（Core）

模型：单管理员（username + password）→ 服务端会话 → `session` cookie；写操作带 CSRF 头。

| 方法 | 路径 | 请求 | 响应 data |
|------|------|------|-----------|
| GET | `/api/auth/me` | — | 已登录：`{"username":"admin","role":"admin"}`；未登录：401；未初始化：503 `setup_required` |
| POST | `/api/auth/setup` | `{"username","password"}`（密码 ≥ 8 字符） | `{"username"}`；建立会话（下发 cookie）；已初始化时 400 |
| POST | `/api/auth/login` | `{"username","password"}`；`username` 为空/省略时按 `"admin"` 处理（单管理员密码登录表单） | `{"username"}`；建立会话；错误凭证 401；限速/锁定 429 |
| POST | `/api/auth/logout` | — | 204，清除会话 |
| POST | `/api/auth/reset` | `{"old_password","new_password"}` | `{"username"}`；成功后使所有既有会话失效 |

Cookie 契约：
- `session=<token>`；`HttpOnly`；`Path=/`；`SameSite=Strict`；TLS 部署加 `Secure`；有效期 24h。
- `csrf-token=<token>`；**非** HttpOnly（供 JS 读取）；`Path=/`；`SameSite=Strict`；登录/setup 时随会话一起下发。

CSRF 契约：所有 `POST/PUT/DELETE/PATCH` 到 `/api/*`（auth 族除外：login/setup/logout）必须携带 `X-CSRF-Token` 头且与 `csrf-token` cookie 一致，否则 401。

登录失败保护：至少实现 per-IP 限速；推荐 per-username 指数退避锁定。

## 3. 设备信息（Core）

| 方法 | 路径 | 响应 data |
|------|------|-----------|
| GET | `/api/status` | `{"device_name","model","vendor","firmware","uptime":<秒>, ...}` + 设备专属状态字段（如 `recording`、`gb28181`、`cameras_running`） |
| GET | `/api/capabilities` | 见 §3.1 |

### 3.1 capabilities 超集 schema

```json
{
  "spec_version": "1",
  "device": {"name": "...", "model": "...", "vendor": "..."},
  "auth": {"model": "session", "setup": true},
  "multi_camera": false,
  "camera_management": false,
  "camera_control": false,
  "imaging": false,
  "ai": false,
  "ptz": false,
  "hls": false,
  "recording": false,
  "devices": false,
  "mjpeg": true,
  "mse": true,
  "webrtc": false,
  "events": ["param_changed", "ai_detection"],
  "config_apply": {"default": "restart", "sections": {"imaging": "immediate"}},
  "restart": true
}
```

字段语义：
- `multi_camera`：相机数 > 1（前端显示相机列表/网格视图）。
- `camera_management`：支持相机 CRUD（§4.2）。
- `camera_control`：支持 start/stop（§4.3）。
- `imaging` / `ai` / `ptz` / `hls` / `recording` / `devices` / `webrtc`：对应 Extension 端点存在。
- `mjpeg` / `mse`：对应流端点存在（前端回落链 MSE → MJPEG → 快照轮询）。
- `events`：SSE 实际会推送的事件词汇表（§6）。
- `config_apply`：`"restart"`（写后需进程重启生效）/ `"immediate"`（立即生效），按配置节细化；节未列出时用 `default`。前端应在每个配置节标题处标注其生效时机，并在改动了 `restart` 节后向用户提供重启入口（§5.1）。
- `restart`：设备支持 `POST /api/system/restart`（§5.1）。

## 4. 相机资源（Core）

相机是资源，统一挂在 `/api/cameras` 下。单相机设备恒有一个固定 id `"0"` 的相机。

Camera 文档：

```json
{
  "id": "0",
  "name": "Front Door",
  "status": "online",
  "camera_type": "csi",
  "rtsp_url": "rtsp://host:8554/stream",
  "resolution": "1280x720",
  "fps": 25
}
```

`status`：`online`（采集中）/ `offline`（设备拔出）；带 `camera_control` 的设备（notebook）使用 `running` / `stopped` / `idle` / `offline`。前端"运行中"判定 = `online | running`。`camera_type`：`csi` / `usb` / `rtsp`。

### 4.1 读取与媒体（Core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras` | `data` 为 Camera 数组 |
| GET | `/api/cameras/{id}` | 单个 Camera；404 不存在 |
| GET | `/api/cameras/{id}/snapshot` | JPEG 快照（`image/jpeg`），需认证 |
| GET | `/api/cameras/{id}/live` | MJPEG 流（`multipart/x-mixed-replace; boundary=...`），需认证；能力 `mjpeg` |
| GET | `/api/cameras/{id}/stream.mse` | chunked HTTP fMP4（`video/mp4`），服务端复用、首段为 init segment，需认证；能力 `mse`。客户端用 `fetch` + ReadableStream 追加 MediaSource |

MSE 流细则：init segment（`ftyp`+`moov`）只发一次，随后每访问单元一个 `moof`+`mdat`；新订阅者需等待关键帧再开始，init segment 需重发。断连后客户端重连即可（服务端是无状态推流）。

### 4.2 相机 CRUD（Extension：`camera_management`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/cameras` | `{"name","camera_type","config"}` → 201 + Camera |
| PUT | `/api/cameras/{id}` | 部分更新 `{"name"?,"config"?,"status"?}` |
| DELETE | `/api/cameras/{id}` | 删除（须先 stop） |

### 4.3 启停（Extension：`camera_control`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/cameras/{id}/start` | 启动采集；已在运行 409 |
| POST | `/api/cameras/{id}/stop` | 停止采集；幂等 |

### 4.4 录像控制（Extension：`recording`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras/{id}/recording` | `{"active":bool,"storage_path"?,"segment_secs"?,"retention_days"?}` |
| POST | `/api/cameras/{id}/recording` | `{"active":bool}`；生效时机由 `config_apply.sections.recording` 决定 |

### 4.5 成像控制（Extension：`imaging`）

参数名沿用 ONVIF PascalCase（`Brightness`、`AWBMode`…）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras/{id}/imaging/params` | `{"<Param>":<value>...}` |
| GET | `/api/cameras/{id}/imaging/options` | 数值参数 `{"min","max","step","default"}`；枚举参数 `{"enums":[...]}` |
| POST | `/api/cameras/{id}/imaging/param` | `{"name","value"}`；立即生效；广播 `param_changed` 事件 |

### 4.6 AI 检测（Extension：`ai`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/detections` | `{"detections":[{"label","confidence","bbox":[x,y,w,h]}],"model","timestamp"}`；未启用时 `{"enabled":false}` |

**bbox 坐标系**：`[x, y, w, h]` 为整数**视频像素**，原点左上角，坐标系为相机原生流分辨率（即 `/api/cameras` 返回的流分辨率，如 1280×720）。不是模型输入分辨率，也不是 0..1 归一化值——设备必须把模型空间坐标映射回视频像素空间后再返回（模型内部将 16:9 帧拉伸进正方形输入时，x/y 轴缩放比不同，映射不可省略）。

### 4.7 PTZ（Extension：`ptz`，虚拟或实云台）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ptz/status` | `{"pan":0.5,"tilt":0.5,"zoom":1.0}`（归一化） |
| POST | `/api/ptz/move` | `{"pan"?,"tilt"?,"zoom"?}` 绝对位置 |

### 4.8 主机设备枚举（Extension：`devices`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices/video` | `[{"index","name","formats":[string]}]` |
| GET | `/api/devices/video/{index}/formats` | `[{"width","height","format","fps"}]` |
| GET | `/api/devices/audio` | `[{"name","supported_configs":[...]}]` |

## 5. 配置（Core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 完整配置文档（各设备 schema 不同），机密字段（`password` 等）脱敏为 `"****"` |
| PUT | `/api/config` | **部分合并**写：只提交要改的子树，深合并到现配置；`"****"` 值原样写回时服务端还原为存储值。响应 `{"applied":"restart"|"immediate"}` |

前端职责：读取 → 递归渲染编辑器 → 收集变更子树 → 深合并 → PUT。生效语义从 `capabilities.config_apply` 读取并向用户展示：每个配置节标题处标注「需重启生效」/「立即生效」；改动了 `restart` 节并保存后，若设备通告 `restart` 能力，展示"立即重启"入口。机头字段名统一 `web.username` / `web.password` / `rtsp.*` / `onvif.*` / `gb28181.*`（各设备多出的节自由扩展）。

### 5.1 服务重启（Extension：`restart`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/system/restart` | 重启设备服务进程（应用所有 `restart` 语义的已保存配置）。响应 `200 {"status":"restarting"}` 后尽快返回，随后进程退出并拉起（正常数秒内）。幂等：重启中重复调用无副作用 |

前端流程：确认对话框 → POST → 轮询 `GET /api/health`（公开）至恢复 → 刷新页面。

## 6. 事件通道（Core：`GET /api/events`，SSE）

- `Content-Type: text/event-stream`；15s keep-alive 注释行；需认证（cookie 由 EventSource 自动携带）。
- 事件格式：`event: <type>\ndata: <json>\n\n`。
- 客户端不应假设事件集合固定 —— 以 `capabilities.events` 通告为准，未知事件类型忽略。

词汇表：

| type | payload | 产生方 |
|------|---------|--------|
| `camera_added` | `{"camera_id","name","device_index"?}` | notebook 热插拔 |
| `camera_offlined` | `{"camera_id"}` | notebook 热插拔 |
| `param_changed` | `{"camera_id","name","value"}` | imaging 参数被任意客户端修改 |
| `ai_detection` | `{"camera_id","detections":[{"label","confidence","bbox"}],"frame_number"?}` | AI 推理帧；bbox 坐标系同 §4.6（视频像素空间） |
| `recording` | `{"camera_id","active"}` | 录像启停 |
| `status` | `{"uptime",...}` | 周期状态摘要（可选） |

## 7. 附录 A：接受的设备方言（差异显式清单）

1. **传输层**：Pi 走 HTTP :8088（cookie 无 `Secure`）；notebook 默认 TLS :8443（cookie 带 `Secure`），可另配 `web.http_port` 开启一个附加纯 HTTP 监听用于局域网免证书访问——经该端口颁发的会话 cookie 不带 `Secure`（浏览器拒发 `Secure` cookie 到 http://，带上会导致 HTTP 端口无法登录）。前端以 `location.protocol` 自适应。
2. **Go 遗留 `/snapshot`**（:8088，无认证）保留，专供 NVR 拉流，与 `/api/cameras/0/snapshot` 并存。
3. **凭证存储**：Pi 存于配置文件（会话为内存态，进程重启即全员下线）；notebook 存 SQLite（bcrypt 哈希，会话持久）。认证协议层面无差别。
4. **Go 无 MJPEG**（H.264 管线无原始帧），`capabilities.mjpeg=false`，前端回落快照轮询。
5. **metrics 指标名**各设备自定义（`mibee_eye_*` / `mibee_*`），不做统一。
6. **Go HLS**（`/hls/*`）与其 metrics 独立端口 :9100 保留为 Go 专属方言。
7. **notebook 协议热切换**：`GET /api/protocols/runtime-status` 为 notebook 扩展端点（ONVIF/GB28181/RTMP 运行态），配置本体已并入 `/api/config` 的 `protocols` 节。
8. **配置文件格式**：Go YAML、Pi Rust TOML、notebook SQLite —— 对前端不可见，仅是 `PUT /api/config` 的落地方式。
9. **设备级翻转（hflip/vflip）**：翻转烘焙进编码流，对所有观看端（RTSP/ONVIF/GB28181/录像/快照）持久生效，与浏览器端仅显示用的直播翻转按钮（localStorage）相互独立。配置位置方言：rs 为 `/api/config` 的 `camera.hflip`/`camera.vflip`（bool，重启生效）；Go 为同名字段（经 libcamera transform，重启生效），且 Go 的成像端点（§4.5）收到 `VFlip`/`HFlip` 时同样转发落地为 `camera.vflip`/`camera.hflip` 并重启生效（响应附 `applied:"restart"`，为 §4.5「立即生效」的显式例外——rpicam-vid 无运行时翻转通道；值与现值相同的翻转请求为幂等 no-op：不写盘、不重启，响应不带 `applied` 字段），即 Go 端两类翻转是同一持久概念；notebook 为每相机 `PUT /api/cameras/{id}` 的 `config.hflip`/`config.vflip`（相机流 (重)启时生效，前端相机卡片提供翻转按钮并自动 stop→start）。
10. **配置生效路径**：Go 保存即自动重启服务（`applied:"restart"` 落地为 SIGTERM 自重启，内存会话失效）；rs 保存仅落盘，由用户经 `POST /api/system/restart`（§5.1）显式重启应用；notebook 按节热应用，无 `restart` 能力（`capabilities.restart=false`，前端不展示重启入口）。

## 8. 附录 B：本规范取代的旧端点（迁移对照）

| 旧端点（项目） | 新端点 |
|----------------|--------|
| `GET /health`（go/notebook） | `GET /api/health` |
| `GET /api/version`（go） | 并入 `GET /api/status` 的 `firmware` |
| `POST /api/login` + token（go） | `POST /api/auth/login` + cookie |
| `X-Password` 写门（rs） | cookie 会话 + CSRF |
| `GET /api/settings`、`/api/protocols/{x}` GET/PUT（notebook） | `GET/PUT /api/config`（`protocols` 节） |
| `POST /api/config/onvif`、`/api/config/gb28181`（go） | `PUT /api/config` 部分合并 |
| `GET /api/camera/params` 等（go） | `GET/POST /api/cameras/{id}/imaging/*` |
| `GET /api/stream/ws`（go）、`GET /ws/video`（rs） | `GET /api/cameras/{id}/stream.mse` |
| `GET /ws`（go/rs 控制通道） | `GET /api/events`（SSE） |
| `GET /api/stream`（rs MJPEG）、`/api/cameras/{id}/live`（notebook） | `GET /api/cameras/{id}/live` |
| `GET /api/capture`、`/snapshot.jpg`（rs） | `GET /api/cameras/{id}/snapshot` |
| `GET/POST /api/record`（rs） | `GET/POST /api/cameras/{id}/recording` |
