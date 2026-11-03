#!/usr/bin/env python3
"""
qoder_device_login.py — Qoder 设备码登录（Device Flow + PKCE/S256）

实测过的服务端行为（2026-11，qoder.com 全球版）
  GET  /device/selectAccounts?challenge=..&challenge_method=S256&nonce=..&machine_id=..&client_id=..
       未登录 -> 302 /users/sign-in?oauth_callback=<授权页 URL>&directLogin=true
       登录后 -> 回到授权页，选择账号即完成授权
  GET  /api/v1/deviceToken/poll?nonce=..&verifier=..&challenge_method=S256
       未授权 -> 401 {"errorCode":"Unauthorized","errorMessage":"User not authenticated"}
       已授权 -> 200 {"token":..,"refresh_token":..,"user_id":..,"user_name":..,"expires_in":..}
  POST /api/v1/deviceToken/refresh  {"refresh_token":..}
       缺 CSRF -> 400 CSRFInvalid（浏览器会话路径才需要 cookie+csrf；CLI 用 poll/refresh 裸接口）

子命令
  python qoder_device_login.py --print-only --state-out .qoder-device.json
      生成并打印授权 URL，把 verifier/nonce/machine_id 存进 state 文件
  python qoder_device_login.py --resume .qoder-device.json --out qoder-token.json --timeout 600
      轮询直到授权成功，落 token
  python qoder_device_login.py            # 一条命令走完（本机交互式）
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DASHBOARD = os.environ.get("QODER_DASHBOARD_ORIGIN", "https://qoder.com").rstrip("/")
CLIENT_ID_DEFAULT = "e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb"   # qodercli 非 CI
CLIENT_ID_CI = "e93fe488-5778-4c35-a6fc-0f54ed7b3139"        # qodercli CI
POLL_INTERVAL = 2.0


def make_verifier(length: int = 64) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def stable_machine_id() -> str:
    seed = f"{socket.gethostname()}|{platform.node()}|{os.environ.get('USER', 'runner')}"
    return hashlib.sha256(seed.encode()).hexdigest()[:48]


def build_auth_url(verifier: str, nonce: str, machine_id: str, client_id: str) -> str:
    qs = urllib.parse.urlencode({
        "challenge": s256(verifier),
        "challenge_method": "S256",
        "nonce": nonce,
        "machine_id": machine_id,
        "client_id": client_id,
    })
    return f"{DASHBOARD}/device/selectAccounts?{qs}"


def poll_once(verifier: str, nonce: str) -> tuple[int, dict]:
    qs = urllib.parse.urlencode({"nonce": nonce, "verifier": verifier, "challenge_method": "S256"})
    req = urllib.request.Request(f"{DASHBOARD}/api/v1/deviceToken/poll?{qs}",
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def emit_github_output(**kv) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


def _read_csrf(opener: urllib.request.OpenerDirector) -> str:
    """取 qoder.com 页面里的 csrfToken + 会话 cookie（PAT 写接口要求 X-CSRF-TOKEN）。"""
    req = urllib.request.Request(
        f"{DASHBOARD}/users/sign-in",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
    )
    with opener.open(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r"csrfToken\s*=\s*'([^']+)'", html)
    return m.group(1) if m else ""


def mint_pat(token: str, name: str, expires_at=None) -> tuple[int, dict]:
    """用已登录会话的 Bearer token 调 /api/v1/me/personal-access-tokens 造 PAT。"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = _read_csrf(opener)

    body = json.dumps({"name": name, "expires_at": expires_at}).encode()
    req = urllib.request.Request(
        f"{DASHBOARD}/api/v1/me/personal-access-tokens",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-CSRF-TOKEN": csrf,
            "Origin": DASHBOARD,
            "Referer": f"{DASHBOARD}/account/integrations",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def extract_pat(payload: dict) -> str:
    """兼容不同版本的返回字段。"""
    keys = ("token", "pat", "personal_access_token", "personalAccessToken", "key", "secret")
    for src in (payload, payload.get("data") if isinstance(payload.get("data"), dict) else {}):
        if not isinstance(src, dict):
            continue
        for k in keys:
            v = src.get(k)
            if isinstance(v, str) and v:
                return v
    return ""


def new_state(args) -> dict:
    is_ci = any(os.environ.get(k) for k in ("CI", "GITHUB_ACTIONS"))
    client_id = args.client_id or (CLIENT_ID_CI if is_ci else CLIENT_ID_DEFAULT)
    verifier = make_verifier()
    nonce = str(uuid.uuid4())
    machine_id = args.machine_id or stable_machine_id()
    return {
        "dashboard": DASHBOARD,
        "client_id": client_id,
        "verifier": verifier,
        "nonce": nonce,
        "machine_id": machine_id,
        "auth_url": build_auth_url(verifier, nonce, machine_id, client_id),
        "created_at": int(time.time()),
    }


def wait_for_token(state: dict, out: str, timeout: int) -> int:
    verifier, nonce = state["verifier"], state["nonce"]
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, data = poll_once(verifier, nonce)
        if code == 200 and data.get("token"):
            record = {
                "dashboard": state["dashboard"],
                "token": data["token"],
                "refresh_token": data.get("refresh_token", ""),
                "expires_in": data.get("expires_in", 0),
                "expires_at": data.get("expires_at", ""),
                "refresh_token_expires_at": data.get("refresh_token_expires_at", ""),
                "user_id": data.get("user_id", ""),
                "user_name": data.get("user_name", ""),
                "machine_id": state["machine_id"],
                "obtained_at": int(time.time()),
            }
            with open(out, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            try:
                os.chmod(out, 0o600)
            except OSError:
                pass
            who = record["user_name"] or record["user_id"]
            print(f"[ok] login ok: user={who} expires_in={record['expires_in']}s -> {out}")
            emit_github_output(qoder_user=who, qoder_token_file=out)
            return 0
        note = data.get("errorCode") or data.get("error") or code
        if note != last:
            print(f"[wait] not authorized yet ({note}) ...", flush=True)
            last = note
        time.sleep(POLL_INTERVAL)
    print("[timeout] authorization not completed; rerun to get a fresh URL")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="qoder-token.json")
    ap.add_argument("--state-out", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--machine-id", default=None)
    ap.add_argument("--client-id", default=None)
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args()

    if args.resume:
        state = json.load(open(args.resume, encoding="utf-8"))
    else:
        state = new_state(args)
        if args.state_out:
            with open(args.state_out, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        print("=" * 78)
        print("Qoder device login — open this URL in a browser:")
        print("  existing account: sign in")
        print("  no account: click Sign up; oauth_callback returns you to the device page")
        print("=" * 78)
        print(state["auth_url"])
        print("=" * 78, flush=True)
        emit_github_output(auth_url=state["auth_url"])
        if args.print_only:
            return 0

    return wait_for_token(state, args.out, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
