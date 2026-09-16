# MiBee Camera Web API 统一规范 (SPEC v1)

本规范定义 MiBee 摄像头设备 Web 管理 API 的统一契约，由三个设备实现：

| 实现 | 仓库 | 部署 |
|------|------|------|
| `mibee-eye-rs` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `mibee-eye-go` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `mibee-eye-notebook` (mibee-rec) | 本工作区 | 笔记本，HTTPS :8443（TLS 强制），多相机 CRUD |

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
  "ai_models": false,
  "ai_upload": false,
  "ptz": false,
  "hls": false,
  "recording": false,
  "watermark": false,
  "devices": false,
  "mjpeg": true,
  "mse": true,
  "webrtc": false,
  "events": ["param_changed", "ai_detection"],
  "config_apply": {"default": "restart", "sections": {"imaging": "immediate"}},
  "restart": true,
  "observability": {"metrics": true, "logs": true, "requests": true}
}
```

字段语义：
- `multi_camera`：相机数 > 1（前端显示相机列表/网格视图）。
- `camera_management`：支持相机 CRUD（§4.2）。
- `camera_control`：支持 start/stop（§4.3）。
- `imaging` / `ai` / `ptz` / `hls` / `recording` / `devices` / `webrtc`：对应 Extension 端点存在。
- `watermark`：设备支持视频水印（§5.2 配置子树）。烧录发生在编码前，对该设备的全部视频输出（直播 / RTSP / ONVIF / GB28181 / 录制文件 / 快照）生效；生效时机由 `config_apply.sections.watermark` 通告。
- `ai_models`：设备带模型注册表并支持运行时热切换（§4.6 的模型清单 / 激活端点）。仅在 `ai:true` 时有意义；缺省视为 `false`，前端隐藏模型切换 UI。
- `ai_upload`：设备允许运行时上传/删除模型文件（§4.6 的 POST/DELETE）。仅在 `ai_models:true` 时有意义；缺省视为 `false`。设备侧以配置开关（如 `ai.allow_upload`，默认关）控制——模型文件是对推理引擎的不可信输入，生产环境应仅在需要时开启。
- `mjpeg` / `mse`：对应流端点存在（前端回落链 MSE → MJPEG → 快照轮询）。
- `events`：SSE 实际会推送的事件词汇表（§6）。
- `config_apply`：`"restart"`（写后需进程重启生效）/ `"immediate"`（立即生效），按配置节细化；节未列出时用 `default`。前端应在每个配置节标题处标注其生效时机，并在改动了 `restart` 节后向用户提供重启入口（§5.1）。可选布尔 `auto`（缺省 `false`）：为 `true` 时（Go 方言）改动 `restart` 节的**保存会使设备自动立即自重启**（保存响应即带 `applied:"restart"`），前端应进入统一重启等待流程（提示→轮询 `/api/health`→恢复后自动重载），而不是展示手动重启入口。
- `restart`：设备支持 `POST /api/system/restart`（§5.1）。
- `observability`：可观测能力（§3.2）；缺省（字段不存在）视为三项皆 `false`，前端隐藏资源监控图与日志/请求视图。

### 3.2 可观测（Extension：`observability`）

实时监控与可观测性。所有速率值由设备内置 2s 采样器计算（与调用方请求节奏无关，多次轮询语义稳定）；无历史存储——历史由前端滚动窗口自行保留。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/metrics/summary` | 系统+进程资源实时快照（结构见下），会话鉴权 |
| GET | `/metrics` | Prometheus 文本格式（0.0.4），**公开无鉴权**（抓取惯例；go 保留 9100 独立端口为方言） |
| GET | `/api/logs?limit=&level=` | 内存环形缓冲的最近日志（`limit` 缺省 200 上限 1000；`level` 为最低级别过滤 debug/info/warn/error），会话鉴权 |
| GET | `/api/requests?limit=` | 最近的 Web API 请求追踪摘要（`limit` 缺省 100 上限 500），会话鉴权 |

`/api/metrics/summary` 响应 data：

```json
{
  "ts": 1788320000,
  "interval_ms": 2000,
  "system": {
    "cpu_percent": 23.5,
    "load_avg": [0.4, 0.35, 0.3],
    "memory": {"total": 8589934592, "used": 3200000000, "available": 5389934592},
    "disks": [{"path": "/", "total": 61080000000, "used": 12216000000, "free": 48864000000},
               {"path": "/mnt/data", "total": 240000000000, "used": 9600000000, "free": 230400000000}],
    "network": {"rx_bytes": 123456789, "tx_bytes": 9876543,
                 "rx_rate": 12000.0, "tx_rate": 800.0}
  },
  "process": {
    "cpu_percent": 12.0,
    "rss_bytes": 123456789,
    "open_fds": 42,
    "uptime": 3600,
    "io_read_bytes": 1000000, "io_write_bytes": 2000000,
    "storage_bytes": 9600000000,
    "traffic": {"http_rx_bytes": 5000, "http_tx_bytes": 900000,
                 "rtsp_tx_bytes": 100000000, "gb28181_tx_bytes": 40000000}
  }
}
```

字段语义：
- `system.cpu_percent`：自上一采样周期以来的整机 CPU 占用（0–100，含其它进程）。
- `system.disks`：设备相关挂载点（根分区 + 录像数据分区，如已挂载）。
- `system.network`：聚合网卡计数与速率（字节/秒）。
- `process.cpu_percent` / `rss_bytes` / `open_fds`：本服务进程的 CPU、常驻内存、打开的文件描述符数。
- `process.io_read_bytes` / `io_write_bytes`：进程累计 I/O 字节（Linux `/proc/<pid>/io` 的 rchar/wchar，含文件与套接字）。
- `process.storage_bytes`：本服务的磁盘占用 = 录像数据目录实际大小（按录像索引累计；未启用录像时为 0）。
- `process.traffic`：**应用归因**流量计数（非内核精确值）：HTTP 请求收发字节（中间件统计）、RTSP/RTP 出流字节、GB28181 出流字节。Linux 不提供按进程的内核网络计数，此字段为设备自行埋点的累计值，速率由前端按两次轮询差值计算。

`/api/requests` 响应 data：`{"entries":[{"id":"a1b2c3","method":"GET","path":"/api/status","status":200,"duration_ms":3.2,"ts":1788320000}]}`，按时间倒序。中间件为每个 Web API 请求分配 `request_id`（响应头 `X-Request-Id` 回显），记录方法/路径/状态码/耗时；该 `request_id` 同时出现在 `/api/logs` 的相关条目中，用于设备级调用关联。RTSP/ONVIF/GB28181 独立端口面不在追踪范围（以 `/metrics` 计数器覆盖）。

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
| GET | `/api/ai/models` | 模型注册表（能力 `ai_models`）：`{"active":"<id>","models":[{"id","family","input":<int>,"source":"builtin"\|"custom"\|"uploaded","available":bool}]}`。`available:false`（模型文件缺失/不可加载）的条目前端应禁选。能力 `ai_upload` 时另附 `"upload":{"allowed":bool,"max_bytes":<int>}` |
| POST | `/api/ai/models/{id}/activate` | 运行时热切换模型：`{"active":"<id>","applied":"immediate"}`。未知 `id` → 404；`available:false` → 409；加载失败**回滚保持旧模型**并 500。成功后写回 `/api/config` 的 `ai.model` 并广播 `ai_model_changed` |
| POST | `/api/ai/models/{id}` | 上传模型（能力 `ai_upload`）：multipart 表单 `family`（`nanodet`\|`yolox`…，须为该设备已实现的解码族）+ `file`（ONNX 二进制）；`id` 须匹配 `^[a-z0-9][a-z0-9-]{0,63}$`。设备**必须先完整加载验证**（会话构建 + 族形状校验）通过后再落盘入册 → `201 {"id","family","input","source":"uploaded"}`；验证失败 400（文件删除不留痕）。id 已存在 → 409；超 `max_bytes` → 413；`family`/id 非法 → 400；能力关闭 → 501 |
| DELETE | `/api/ai/models/{id}` | 移除上传模型（仅 `source:"uploaded"`）：204。内置/自定义条目 → 409；该模型正在运行 → 409；未知 → 404。删除后条目从清单与磁盘移除 |

**bbox 坐标系**：`[x, y, w, h]` 为整数**视频像素**，原点左上角，坐标系为相机原生流分辨率（即 `/api/cameras` 返回的流分辨率，如 1280×720）。不是模型输入分辨率，也不是 0..1 归一化值——设备必须把模型空间坐标映射回视频像素空间后再返回（模型内部将 16:9 帧拉伸进正方形输入时，x/y 轴缩放比不同，映射不可省略）。

**模型标识**：`/api/detections` 响应与 `ai_model_changed` 事件中的 `"model"` 为**模型 id**（即 `/api/ai/models` 条目的 `id`，如 `nanodet-plus-m-320`），不是文件路径。`/api/config` 的 `ai.model` 字段是启动时加载的模型（`PUT /api/config` 直接改它为 restart 语义，重启后生效）；运行时不重启切换必须走 activate 端点。

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

前端职责：读取 → 递归渲染编辑器 → 收集变更子树 → 深合并 → PUT。生效语义从 `capabilities.config_apply` 读取并向用户展示：每个配置节标题处标注「需重启生效」/「立即生效」；改动了 `restart` 节并保存后，若设备通告 `restart` 能力，展示"立即重启"入口。机头字段名统一 `web.username` / `web.password` / `rtsp.*` / `onvif.*` / `gb28181.*`；`watermark.*` 水印子树字段三端同名（定义见 §5.2）（各设备多出的节自由扩展）。

### 5.2 视频水印（Extension：`watermark`）

在编码前把水印（自定义文案 + 实时时间）烧录进设备的**全部视频输出**（直播 / RTSP / ONVIF / GB28181 / 录制文件 / 快照，安防 OSD 惯例）。配置子树：

| 字段 | 类型 | 缺省 | 说明 |
|------|------|------|------|
| `watermark.enabled` | bool | `false` | 启用水印 |
| `watermark.text` | string ≤128 | `""` | 自定义文案；可含非 ASCII 字符，能否渲染取决于字体（见 `font_path`） |
| `watermark.show_timestamp` | bool | `true` | 烧录实时时间（设备本地时区） |
| `watermark.timestamp_format` | string | `"%Y-%m-%d %H:%M:%S"` | strftime 子集白名单：`%Y` `%m` `%d` `%H` `%M` `%S` `%F` `%T` `%%` + 字面量字符 |
| `watermark.position` | enum | `"top-left"` | `top-left` / `top-right` / `bottom-left` / `bottom-right` |
| `watermark.font_size` | int | `24` | 像素高度，12..96 |
| `watermark.font_path` | string | `""` | 可选 TTF/OTF 路径（如 CJK 字体，启用中文文案）；空 = 设备内嵌 ASCII 字体（非 ASCII 字符渲染为缺字/空白） |

语义：
- 文案与时间戳同行渲染（`text` + 两个空格 + 时间戳；二者可独立关闭）。样式 v1 固定：白字 + 1px 黑描边（OSD 标准），边距为设备常量不可配。
- 校验：`enabled:true` 时须 `text` 非空或 `show_timestamp:true`；`position` / `font_size` / `timestamp_format` 非法 → 400。
- `font_path` 指向的字体加载失败：回落内嵌字体并记录告警，服务不失败（fail-open）。
- 生效时机由 `config_apply.sections.watermark` 通告；设备方言见附录 A14。

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
| `ai_model_changed` | `{"camera_id","model"}` | 模型热切换完成（§4.6 activate 端点）；`model` 为新模型 id |
| `alarm` | `{"camera_id","active","source","targets","timestamp"}` | 告警上升沿（v1 同版本加法）：与 GB28181 告警 NOTIFY 同源同门控（上升沿 + 冷却 + 运行时开关），在边缘被接受时即推送、与平台侧投递成败无关；`active` 恒为 `true`（上升沿事件），`source` 目前恒为 `"ai"`，`targets` 为触发目标数，`timestamp` epoch-ms |
| `recording` | `{"camera_id","active"}` | 录像启停 |
| `status` | `{"uptime",...}` | 周期状态摘要（可选） |

## 7. 附录 A：接受的设备方言（差异显式清单）

1. **传输层**：Pi 走 HTTP :8088（cookie 无 `Secure`）；notebook 默认 TLS :8443（cookie 带 `Secure`），可另配 `web.http_port` 开启一个附加纯 HTTP 监听用于局域网免证书访问——经该端口颁发的会话 cookie 不带 `Secure`（浏览器拒发 `Secure` cookie 到 http://，带上会导致 HTTP 端口无法登录）。前端以 `location.protocol` 自适应。
2. **Go 遗留 `/snapshot`**（:8088，无认证）保留，专供 NVR 拉流，与 `/api/cameras/0/snapshot` 并存。rs 设备自 2026-09-08 起提供同一方言端点（即 ONVIF GetSnapshotUri 通告的 URI）。
3. **凭证存储**：Pi 存于配置文件（会话为内存态，进程重启即全员下线）；notebook 存 SQLite（bcrypt 哈希，会话持久）。认证协议层面无差别。
4. **Go 无 MJPEG**（H.264 管线无原始帧），`capabilities.mjpeg=false`，前端回落快照轮询。
5. **metrics 指标名**各设备自定义（`mibee_eye_*` / `mibee_*`），不做统一。
6. **Go HLS**（`/hls/*`）与其 metrics 独立端口 :9100 保留为 Go 专属方言。
7. **notebook 协议热切换**：`GET /api/protocols/runtime-status` 为 notebook 扩展端点（ONVIF/GB28181/RTMP 运行态），配置本体已并入 `/api/config` 的 `protocols` 节。
8. **配置文件格式**：Go YAML、Pi Rust TOML、notebook SQLite —— 对前端不可见，仅是 `PUT /api/config` 的落地方式。
9. **设备级翻转（hflip/vflip）**：翻转烘焙进编码流，对所有观看端（RTSP/ONVIF/GB28181/录像/快照）持久生效，与浏览器端仅显示用的直播翻转按钮（localStorage）相互独立。配置位置方言：rs 为 `/api/config` 的 `camera.hflip`/`camera.vflip`（bool，重启生效）；Go 为同名字段（经 libcamera transform，重启生效），且 Go 的成像端点（§4.5）收到 `VFlip`/`HFlip` 时同样转发落地为 `camera.vflip`/`camera.hflip` 并重启生效（响应附 `applied:"restart"`，为 §4.5「立即生效」的显式例外——rpicam-vid 无运行时翻转通道；值与现值相同的翻转请求为幂等 no-op：不写盘、不重启，响应不带 `applied` 字段），即 Go 端两类翻转是同一持久概念；notebook 为每相机 `PUT /api/cameras/{id}` 的 `config.hflip`/`config.vflip`（相机流 (重)启时生效，前端相机卡片提供翻转按钮并自动 stop→start）。
10. **配置生效路径**：Go 保存即自动重启服务（`applied:"restart"` 落地为 SIGTERM 自重启；会话持久化在配置同目录的 `web-sessions.json`，自重启（保存/翻转/§5.1 显式重启）后浏览器免重登无感恢复，显式登出或密码重置仍清空全部会话）；rs 保存仅落盘，由用户经 `POST /api/system/restart`（§5.1）显式重启应用（会话同样持久化到配置同目录的 `web-sessions.json`，重启后保持登录）；notebook 按节热应用，无 `restart` 能力（`capabilities.restart=false`，前端不展示重启入口）。
11. **可观测（§3.2）实现方言**：Go 保留 9100 独立 Prometheus 端口（历史抓取配置），同时 `/metrics` 挂在 Web 端口；rs 仅 Web 端口 `/metrics`。日志环形缓冲覆盖 log 门面（Go 为 slog 全量、rs 为协议库 log 门面）；rs 产品代码的历史 `println!` 输出仅进 journald 不进 `/api/logs`。请求追踪覆盖 Web API 面；RTSP/ONVIF/GB28181 独立端口面以 `/metrics` 计数器覆盖。notebook 后端尚未实现 §3.2（`capabilities.observability` 缺省，前端自动隐藏资源监控区）。
12. **AI 检测（§4.6）方言**：notebook 为多相机设备，除规范端点 `GET /api/detections`（返回最近一次推理的相机结果）外，另提供逐相机扩展端点 `GET /api/cameras/{id}/detections`（响应结构同 §4.6：`{"detections","model","timestamp"}`，bbox 同为该相机原生流分辨率的视频像素坐标）。`ai_detection` SSE 事件（§6）的 `camera_id` 在 notebook 上为真实相机 UUID；Pi 设备恒为 `"0"`。**模型注册表（§4.6）**：notebook 的内置注册表只含 NanoDet 族条目（其解码器未实现 YOLOX，上传 `family` 仅接受 `nanodet`）；`ai_model_changed` 的 `camera_id` 为 `"all"`（设备级切换）；激活选择持久化在设备数据库 `ai.model` 设置（TOML `[ai]` 仅引导默认），重启后自动覆盖。
13. **notebook GB35114 A 级子树**：`protocols.gb28181.gb35114`（`enabled`、`device_cert_file`、`device_key_file`、`platform_cert_file`、`server_id`），随 `PUT /api/config` 深合并热应用（嵌套节同样拒绝未知字段、兼容字符串布尔）。证书缺失/无效时 GB28181 拒绝启动（fail-closed），Web 与其他协议不受影响。
14. **视频水印（§5.2）方言**：rs 为顶层 `watermark` TOML 节（`PUT /api/config` 落盘、`POST /api/system/restart` 生效，`config_apply.sections.watermark = "restart"`）；notebook 为 `protocols.watermark`（SQLite 持久化，设备级全局、作用于全部相机；相机流 (重)启时读取生效——与 `protocols.recording` 同为 read-at-use。水印启用时 MJPEG 相机的快照直通关闭，改为从带水印的 YUV 重编码）；Go 未实现（`watermark` 能力缺省 `false`，前端不渲染水印设置）——libcamera rpicam-apps 已移除 annotate 通道，实时水印需改造采集管线，待单独立项。
15. **notebook 语音对讲接收（GB/T 28181-2022 §9.2）**：`protocols.gb28181.talkback_playback`（bool，缺省 `true`，随 `PUT /api/config` 深合并、协议重启时生效）。开启且本机存在可用音频输出时，audio-only INVITE 应答 200 OK 并在本地扬声器播放（G.711 A/μ 律解码）；关闭或无输出设备时 fail-open 回落 488（与协议库对未注册 sink 的设计一致），绝不应答 200 后静默丢音。
16. **notebook 告警与位置方言（GB/T 28181-2022 §9.5 / §9.7）**：`protocols.gb28181` 新增四键——`alarm_notify_enabled`（bool，缺省 `true`；平台 DeviceConfig AlarmReport 双开关可运行时覆盖）、`alarm_cooldown_secs`（u64，缺省 `30`；AI 检测上升沿告警的冷却）、`position_longitude` / `position_latitude`（string，缺省空 = 不上报 MobilePosition；非空时随订阅周期以静态位置上报）。AI 检测上升沿触发 §6 `alarm` SSE 事件并在启用时发 Alarm NOTIFY（AlarmPriority 4 / AlarmMethod 5 / AlarmType 2，2022 标准表）。均随 `PUT /api/config` 深合并，协议重启时生效。

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
