# Qoder：GitHub Actions 里「注册 + 登录」

结论先说：**Qoder 的注册 / 密码登录接口带阿里云滑块验证码，纯 HTTP 走不通；Actions 里唯一
稳的路子是设备码（device flow + PKCE/S256）——链接丢进浏览器，人在浏览器里注册或登录，
Job 轮询拿到 token 后继续跑。** 本目录就是这条链路的可运行实现。

```
qoder-gate/
├─ qoder_device_login.py             # 设备码登录器（打印 URL → 轮询 → 落 token）
├─ .github/workflows/qoder-login.yml # Actions 工作流（device / pat 两条路径）
├─ run_local.sh                      # 本机 / 容器里跑同一条链路
└─ README.md
```

---

## 1. 协议（从 qodercli 1.1.81 Linux 二进制 + qoder.com 前端逆出来的）

| 步骤 | 请求 | 说明 |
|---|---|---|
| 授权页 | `GET https://qoder.com/device/selectAccounts?challenge=<S256(verifier)>&challenge_method=S256&nonce=<uuid4>&machine_id=<稳定ID>&client_id=<ID>` | 未登录 302 → `/users/sign-in?oauth_callback=<授权页URL>&directLogin=true`；注册页同样透传 `oauth_callback`，所以**同一条链接既能登录也能注册** |
| 轮询 | `GET https://qoder.com/api/v1/deviceToken/poll?nonce=..&verifier=..&challenge_method=S256` | 未授权：`401 {"errorCode":"Unauthorized"}`；授权后：`{"token","refresh_token","user_id","user_name","expires_in"}`。**不需要 cookie / CSRF** |
| 刷新 | `POST https://qoder.com/api/v1/deviceToken/refresh {"refresh_token":..}` | 同上，裸接口 |
| 身份确认 | `GET https://qoder.com/api/v1/userinfo` + `Authorization: Bearer <token>` | token 与 PAT 同属 Bearer 凭证 |
| CLI 侧 | 非 CI `client_id=e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb`；CI `e93fe488-5778-4c35-a6fc-0f54ed7b3139` | 取哪个都能渲染授权页，建议按环境选 |

实测边界（省得你再撞一遍）：

- `POST /api/v1/users`、`POST /api/v1/sessions`、`POST /api/v1/verificationCodes`
  一律先被 `x-captcha-verify-param` 拦成 `403 Forbidden`；缺 CSRF 是 `400 CSRFInvalid`。
  → **没有浏览器过验证码，注册和密码登录都做不成**，这是硬约束不是配置问题。
- `deviceToken/poll` 授权前对任何 nonce 都回同一个 401（不泄露 nonce 是否存在），
  轮询可以盲跑；`nonce` 一次性，链接过期重跑。
- `qodercli` 在无图形环境（`CI` / `BROWSER=www-browser` / `DEBIAN_FRONTEND=noninteractive` /
  `SSH_CONNECTION` / Linux 无 `DISPLAY|WAYLAND_DISPLAY|MIR_SOCKET`）会自动跳过开浏览器，
  直接把 `Please open the following URL in your browser to sign in:` 打印出来。

---

## 2. GitHub Actions 用法

把 `.github/workflows/qoder-login.yml` 拷进仓库。

### A. 首次 / 换号 / 顺手注册 —— device 路径

```
Actions → qoder-login → Run workflow → mode = device
```

1. `Start device login and publish the URL` 里用 `qoder login` 起设备码流程，把 CLI 打印的
   登录链接抓出来写进 **Job Summary**（抓不到就回退到 `qoder_device_login.py` 自己生成）；
2. 手机或电脑打开链接：
   - 已有账号 → 登录 → 选账号 → 授权；
   - 没有账号 → 点 **Sign up**，邮箱验证码注册，完成后 `oauth_callback` 把你带回
     `/device/selectAccounts?...`，确认授权；
3. Job 等 CLI 退出（授权成功会打 `Login successful! Welcome, <name>`），凭据只写进
   本次运行的 `QODER_CONFIG_DIR`（`.qoder-home`），**不缓存、不上传，运行结束即丢弃**；
   每次都是全新环境，需要重新授权（这是刻意设计，方便验证"从零登录"链路）；
4. `Verify the account works` 用 `qoder -p` 或 `/api/v1/userinfo` 验一遍。

超时（默认 900s）没点授权就重跑，会生成新链接。

### B. 常态 —— pat 路径

1. 浏览器登录后去 <https://qoder.com/account/integrations> 生成 Personal Access Token；
2. 仓库 `Settings → Secrets and variables → Actions` 加 `QODER_PERSONAL_ACCESS_TOKEN`；
3. `mode = pat`（`push` / `pull_request` 触发的默认路径）。

PAT 走环境变量，优先级高于落盘凭据；`qoder -p -o json` 直接可用。

---

## 3. 本机 / 容器 / WSL

```bash
bash run_local.sh                # 打印链接 → 浏览器授权 → token 落到 qoder-token.json
bash run_local.sh --print-only   # 只打印链接
```

不用脚本，直接让 CLI 自己走同一条设备码：

```bash
curl -fsSL https://qoder.com/install | bash
export CI=1        # 强制无图形模式：不开浏览器，只打印 URL
qoder              # 首次运行引导登录；把 URL 复制到浏览器（没有账号就注册）
qoder -p "say ok"  # 验证
```

只要 URL：

```bash
python3 qoder_device_login.py --print-only --state-out .qoder-device.json
python3 qoder_device_login.py --resume .qoder-device.json --timeout 600
```

---

## 4. token 怎么喂给后续步骤

```bash
export QODER_PERSONAL_ACCESS_TOKEN="$(python3 -c 'import json;print(json.load(open("qoder-token.json"))["token"])')"
qoder -p "run tests and fix failures"
```

`expires_in` 到了就用 `refresh_token` 打 `POST /api/v1/deviceToken/refresh` 续期，
不必再走浏览器。

---

## 5. 安全

- `qoder-token.json`、`.qoder-device.json`、`.qoder-home/` 都是明文凭证，`.gitignore` 已排除；
  workflow 每次运行开头 `Reset Qoder state` 主动清空，且只上传日志、不传凭证文件。
- 授权链接 5 分钟内有效（CLI 侧超时 `300000ms`），过期重跑。
- 同账号多机各自持一份 device token，互不影响。
