import os
import json
import uuid
import hmac
import hashlib
import base64
import sqlite3
import asyncio
from datetime import datetime
from pathlib import Path
from functools import wraps

import aiohttp
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

DB_PATH = Path(__file__).parent / "users.db"
SECRET_KEY_PATH = Path(__file__).parent / ".secret_key"
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
PUBLIC_URL_PATH = Path(__file__).parent / ".public_url"
LINE_CONTENT_API = "https://api-data.line.me/v2/bot/message"


def get_or_create_secret_key():
    """シークレットキーをファイルから読み込み、なければランダム生成して保存"""
    if SECRET_KEY_PATH.exists():
        return SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
    key = os.urandom(32).hex()
    SECRET_KEY_PATH.write_text(key, encoding="utf-8")
    print("[INFO] シークレットキーを生成しました (.secret_key)")
    return key


app.secret_key = get_or_create_secret_key()

LINE_API_BASE = "https://api.line.me/v2/bot"

# ─── DB初期化 ──────────────────────────────────────────

def init_db():
    """テーブルを作成し、初期管理者アカウントを登録"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            token TEXT NOT NULL,
            basic_id TEXT DEFAULT '',
            max_friends INTEGER DEFAULT 500,
            friend_count INTEGER DEFAULT 0,
            channel_secret TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            account_ids TEXT NOT NULL,
            message TEXT NOT NULL,
            mode TEXT DEFAULT 'broadcast',
            user_ids TEXT DEFAULT '[]',
            scheduled_at TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS line_friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            picture_url TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(account_id, line_user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            message_text TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts(id)
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_messages_lookup
            ON chat_messages(account_id, line_user_id, created_at)
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_read_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            line_user_id TEXT NOT NULL,
            last_read_id INTEGER DEFAULT 0,
            FOREIGN KEY (account_id) REFERENCES accounts(id),
            UNIQUE(account_id, line_user_id)
        )
    """)

    # must_change_password カラム追加（既存DBの移行対応）
    try:
        c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # chat_messages: message_type カラム追加
    try:
        c.execute("ALTER TABLE chat_messages ADD COLUMN message_type TEXT DEFAULT 'text'")
    except sqlite3.OperationalError:
        pass

    # chat_messages: media_url カラム追加
    try:
        c.execute("ALTER TABLE chat_messages ADD COLUMN media_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # schedules: image_url カラム追加
    try:
        c.execute("ALTER TABLE schedules ADD COLUMN image_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    # 初期管理者アカウント
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO users (email, password_hash, is_admin, must_change_password) VALUES (?, ?, 1, 1)",
            ("admin@admin", generate_password_hash("admin")),
        )
        print("[INFO] 初期管理者アカウントを作成しました (admin@admin / admin)")
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ─── 既存データの自動移行 ─────────────────────────────────

def migrate_json_to_db():
    """config.json / schedule.json が存在する場合、DBへ移行して .bak にリネーム"""
    config_path = Path(__file__).parent / "config.json"
    schedule_path = Path(__file__).parent / "schedule.json"

    conn = get_db()

    # config.json → accounts テーブル
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            for acc in config.get("accounts", []):
                # 既にDBにあればスキップ
                existing = conn.execute("SELECT id FROM accounts WHERE id = ?", (acc["id"],)).fetchone()
                if existing:
                    continue
                conn.execute(
                    "INSERT INTO accounts (id, user_id, name, token, basic_id, max_friends, friend_count, channel_secret) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        acc["id"],
                        1,  # 管理者に紐付け
                        acc["name"],
                        acc["token"],
                        acc.get("basicId", ""),
                        acc.get("maxFriends", 500),
                        acc.get("friendCount", 0),
                        acc.get("channelSecret", ""),
                    ),
                )
            conn.commit()
            config_path.rename(config_path.with_suffix(".json.bak"))
            print("[INFO] config.json → DB移行完了 (config.json.bak に退避)")
        except Exception as e:
            print(f"[WARN] config.json 移行失敗: {e}")

    # schedule.json → schedules テーブル
    if schedule_path.exists():
        try:
            with open(schedule_path, "r", encoding="utf-8") as f:
                sched_data = json.load(f)
            for s in sched_data.get("schedules", []):
                existing = conn.execute("SELECT id FROM schedules WHERE id = ?", (s["id"],)).fetchone()
                if existing:
                    continue
                conn.execute(
                    "INSERT INTO schedules (id, user_id, account_ids, message, mode, user_ids, scheduled_at, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        s["id"],
                        1,  # 管理者に紐付け
                        json.dumps(s.get("accountIds", [])),
                        s["message"],
                        s.get("mode", "broadcast"),
                        json.dumps(s.get("userIds", [])),
                        s["scheduledAt"],
                        s.get("status", "pending"),
                        s.get("createdAt", datetime.now().isoformat(timespec="seconds")),
                    ),
                )
            conn.commit()
            schedule_path.rename(schedule_path.with_suffix(".json.bak"))
            print("[INFO] schedule.json → DB移行完了 (schedule.json.bak に退避)")
        except Exception as e:
            print(f"[WARN] schedule.json 移行失敗: {e}")

    conn.close()


init_db()
migrate_json_to_db()

# ─── Flask-Login 初期化 ────────────────────────────────

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "ログインが必要です。"


class User(UserMixin):
    def __init__(self, id, email, password_hash, is_admin, created_at, must_change_password=False):
        self.id = id
        self.email = email
        self.password_hash = password_hash
        self.is_admin = bool(is_admin)
        self.created_at = created_at
        self.must_change_password = bool(must_change_password)


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return User(row["id"], row["email"], row["password_hash"], row["is_admin"], row["created_at"], row["must_change_password"])
    return None


def admin_required(f):
    """管理者のみアクセス可能なデコレータ"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({"error": "管理者権限が必要です"}), 403
        return f(*args, **kwargs)
    return decorated


# ─── スケジューラ初期化 ──────────────────────────────────
# debug=Trueのreloaderで二重起動を防止
scheduler = BackgroundScheduler()
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    scheduler.start()


def execute_scheduled_send(schedule_id):
    """予約時刻に実行される送信処理"""
    conn = get_db()
    row = conn.execute("SELECT * FROM schedules WHERE id = ? AND status = 'pending'", (schedule_id,)).fetchone()
    if row is None:
        conn.close()
        return

    account_ids = json.loads(row["account_ids"])
    accounts = conn.execute(
        "SELECT * FROM accounts WHERE id IN ({})".format(",".join("?" * len(account_ids))),
        account_ids,
    ).fetchall() if account_ids else []

    if not accounts:
        conn.execute("UPDATE schedules SET status = 'done' WHERE id = ?", (schedule_id,))
        conn.commit()
        conn.close()
        return

    selected = [{"id": a["id"], "name": a["name"], "token": a["token"]} for a in accounts]
    messages = []
    sched_image_url = row["image_url"] if "image_url" in row.keys() else ""
    if sched_image_url:
        public_url = get_public_url() or ""
        full_url = f"{public_url}{sched_image_url}" if public_url else sched_image_url
        messages.append(build_flex_image_message(full_url, row["message"] or ""))
    elif row["message"]:
        messages.append({"type": "text", "text": row["message"]})
    mode = row["mode"]
    user_ids = json.loads(row["user_ids"]) if mode == "multicast" else None

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(send_all(selected, messages, mode, user_ids))
    finally:
        loop.close()

    conn.execute("UPDATE schedules SET status = 'done' WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()


def restore_schedules():
    """サーバー起動時に未実行の予約ジョブを復元"""
    conn = get_db()
    rows = conn.execute("SELECT * FROM schedules WHERE status = 'pending'").fetchall()
    conn.close()

    now = datetime.now()
    for row in rows:
        run_time = datetime.fromisoformat(row["scheduled_at"])
        if run_time <= now:
            execute_scheduled_send(row["id"])
        else:
            scheduler.add_job(
                execute_scheduled_send,
                "date",
                run_date=run_time,
                args=[row["id"]],
                id=row["id"],
            )


def verify_token(token):
    """LINE Bot Info APIでトークンを検証し、Bot名とbasicIdを取得"""
    resp = requests.get(
        f"{LINE_API_BASE}/info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        return True, data.get("displayName", "Unknown"), data.get("basicId", "")
    return False, None, None


def get_follower_count(token):
    """フォロワーIDを全ページ巡回してカウント"""
    count = 0
    url = f"{LINE_API_BASE}/followers/ids"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 1000}

    while True:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            user_ids = data.get("userIds", [])
            count += len(user_ids)
            next_token = data.get("next")
            if not next_token:
                break
            params["start"] = next_token
        except Exception:
            break

    return count


def save_public_url(url):
    """Webhook経由で検出したパブリックURLを保存"""
    try:
        PUBLIC_URL_PATH.write_text(url.rstrip("/"), encoding="utf-8")
    except Exception:
        pass


def get_public_url():
    """保存されたパブリックURLを取得"""
    try:
        if PUBLIC_URL_PATH.exists():
            return PUBLIC_URL_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None


def update_all_webhook_urls(new_base_url):
    """全アカウントのLINE Webhook URLを自動更新"""
    conn = get_db()
    accounts = conn.execute("SELECT id, token FROM accounts").fetchall()
    conn.close()

    for acc in accounts:
        webhook_url = f"{new_base_url}/webhook/{acc['id']}"
        try:
            resp = requests.put(
                "https://api.line.me/v2/bot/channel/webhook/endpoint",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {acc['token']}",
                },
                json={"endpoint": webhook_url},
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"[INFO] Webhook URL更新成功: {acc['id'][:8]}... → {webhook_url}")
            else:
                print(f"[WARN] Webhook URL更新失敗: {acc['id'][:8]}... ({resp.status_code})")
        except Exception as e:
            print(f"[WARN] Webhook URL更新エラー: {acc['id'][:8]}... ({e})")


def build_flex_image_message(image_url, text=""):
    """画像+テキストをFlex Messageのリッチカードとして構築"""
    body_contents = []
    if text:
        body_contents.append({
            "type": "text",
            "text": text,
            "wrap": True,
            "size": "md",
            "color": "#333333",
        })

    bubble = {
        "type": "bubble",
        "size": "mega",
    }

    if body_contents:
        bubble["body"] = {
            "type": "box",
            "layout": "vertical",
            "contents": body_contents,
        }

    bubble["footer"] = {
        "type": "box",
        "layout": "vertical",
        "contents": [{
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
        }],
        "paddingAll": "0px",
    }

    alt_text = text[:400] if text else "画像メッセージ"

    return {
        "type": "flex",
        "altText": alt_text,
        "contents": bubble,
    }


def check_and_update_public_url(new_url):
    """パブリックURLが変わった場合、保存してWebhook URLを全更新"""
    new_url = new_url.rstrip("/")
    old_url = get_public_url()
    if old_url == new_url:
        return  # 変更なし
    print(f"[INFO] パブリックURL変更検出: {old_url} → {new_url}")
    save_public_url(new_url)
    update_all_webhook_urls(new_url)


def download_line_content(token, message_id):
    """LINE Content APIから画像をダウンロードしてローカルに保存"""
    try:
        resp = requests.get(
            f"{LINE_CONTENT_API}/{message_id}/content",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            stream=True,
        )
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("Content-Type", "")
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "webp" in content_type:
            ext = ".webp"

        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return filename
    except Exception:
        return None


def verify_signature(channel_secret, body, signature):
    """LINE Webhook署名検証"""
    hash_val = hmac.new(
        channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def send_to_account(session, token, messages, mode, user_ids=None):
    """1アカウントへメッセージ送信"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    if mode == "broadcast":
        url = f"{LINE_API_BASE}/message/broadcast"
        payload = {"messages": messages}
    else:
        url = f"{LINE_API_BASE}/message/multicast"
        payload = {"messages": messages, "to": user_ids}

    async with session.post(url, json=payload, headers=headers) as resp:
        status = resp.status
        body = await resp.text()
        return status, body


async def send_all(accounts, messages, mode, user_ids=None):
    """全アカウントに並列送信"""
    results = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for acc in accounts:
            tasks.append(send_to_account(session, acc["token"], messages, mode, user_ids))

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for acc, resp in zip(accounts, responses):
            if isinstance(resp, Exception):
                results.append({
                    "id": acc["id"],
                    "name": acc["name"],
                    "success": False,
                    "error": str(resp),
                })
            else:
                status, body = resp
                success = status == 200
                result = {
                    "id": acc["id"],
                    "name": acc["name"],
                    "success": success,
                    "status": status,
                }
                if not success:
                    try:
                        result["error"] = json.loads(body).get("message", body)
                    except json.JSONDecodeError:
                        result["error"] = body
                results.append(result)

    return results


# ─── 認証ページ ────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        conn = get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if row and check_password_hash(row["password_hash"], password):
            user = User(row["id"], row["email"], row["password_hash"], row["is_admin"], row["created_at"])
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))

        flash("メールアドレスまたはパスワードが正しくありません。", "error")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ─── セキュリティヘッダー ─────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ─── パスワード強制変更 ──────────────────────────────────

@app.before_request
def check_password_change():
    """初回ログイン時にパスワード変更を強制"""
    if not current_user.is_authenticated:
        return
    if not current_user.must_change_password:
        return
    # パスワード変更ページ・ログアウト・静的ファイルは許可
    allowed = ("change_password", "logout", "static")
    if request.endpoint in allowed:
        return
    return redirect(url_for("change_password"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """パスワード変更ページ"""
    if request.method == "POST":
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not new_password:
            flash("新しいパスワードを入力してください。", "error")
        elif len(new_password) < 6:
            flash("パスワードは6文字以上で設定してください。", "error")
        elif new_password != confirm_password:
            flash("パスワードが一致しません。", "error")
        else:
            conn = get_db()
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                (generate_password_hash(new_password), current_user.id),
            )
            conn.commit()
            conn.close()
            current_user.must_change_password = False
            flash("パスワードを変更しました。", "success")
            return redirect(url_for("index"))

    return render_template("change_password.html")


# ─── 管理画面 ──────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_page():
    conn = get_db()
    users = conn.execute("SELECT id, email, is_admin, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return render_template("admin.html", users=users)


@app.route("/admin/users", methods=["POST"])
@admin_required
def add_user():
    data = request.get_json()
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    is_admin = 1 if data.get("is_admin") else 0

    if not email or not password:
        return jsonify({"error": "メールアドレスとパスワードを入力してください"}), 400

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (email, password_hash, is_admin, must_change_password) VALUES (?, ?, ?, 1)",
            (email, generate_password_hash(password), is_admin),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "このメールアドレスは既に登録されています"}), 400

    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({"success": True, "id": user_id}), 201


@app.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "自分自身は削除できません"}), 400

    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id):
    data = request.get_json()
    new_password = data.get("password", "").strip()

    if not new_password:
        return jsonify({"error": "新しいパスワードを入力してください"}), 400

    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(new_password), user_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─── ページ表示 ─────────────────────────────────────────

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/register")
def register_redirect():
    """空きアカウントへ自動リダイレクト"""
    conn = get_db()
    accounts = conn.execute(
        "SELECT basic_id, friend_count, max_friends FROM accounts WHERE basic_id != '' AND friend_count < max_friends ORDER BY friend_count ASC"
    ).fetchall()
    conn.close()

    for acc in accounts:
        return redirect(f"https://line.me/R/ti/p/{acc['basic_id']}", code=302)

    return render_template("full.html"), 200


# ─── アカウント管理 API ────────────────────────────────────

@app.route("/api/accounts", methods=["GET"])
@login_required
def get_accounts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM accounts WHERE user_id = ?", (current_user.id,)).fetchall()
    conn.close()

    safe = []
    for a in rows:
        safe.append({
            "id": a["id"],
            "name": a["name"],
            "basicId": a["basic_id"],
            "maxFriends": a["max_friends"],
            "friendCount": a["friend_count"],
            "hasSecret": bool(a["channel_secret"]),
        })
    return jsonify(safe)


@app.route("/api/accounts", methods=["POST"])
@login_required
def add_account():
    data = request.get_json()
    token = data.get("token", "").strip()
    channel_secret = data.get("channelSecret", "").strip()
    max_friends = data.get("maxFriends", 500)

    if not token:
        return jsonify({"error": "トークンを入力してください"}), 400

    try:
        max_friends = int(max_friends)
    except (TypeError, ValueError):
        max_friends = 500

    valid, name, basic_id = verify_token(token)
    if not valid:
        return jsonify({"error": "無効なトークンです。LINE Developersで確認してください。"}), 400

    conn = get_db()

    # 重複チェック（同一ユーザー内）
    existing = conn.execute(
        "SELECT name FROM accounts WHERE token = ? AND user_id = ?",
        (token, current_user.id),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": f"このトークンは既に登録されています（{existing['name']}）"}), 400

    # フォロワー数取得
    friend_count = get_follower_count(token)

    account_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO accounts (id, user_id, name, token, basic_id, max_friends, friend_count, channel_secret) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, current_user.id, name, token, basic_id or "", max_friends, friend_count, channel_secret),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "id": account_id,
        "name": name,
        "basicId": basic_id or "",
        "maxFriends": max_friends,
        "friendCount": friend_count,
    }), 201


@app.route("/api/accounts/<account_id>", methods=["PUT"])
@login_required
def update_account(account_id):
    """maxFriends / channelSecret 更新"""
    data = request.get_json()
    conn = get_db()

    row = conn.execute("SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "アカウントが見つかりません"}), 404

    if "maxFriends" in data:
        try:
            conn.execute("UPDATE accounts SET max_friends = ? WHERE id = ? AND user_id = ?",
                         (int(data["maxFriends"]), account_id, current_user.id))
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "無効な値です"}), 400
    if "channelSecret" in data:
        conn.execute("UPDATE accounts SET channel_secret = ? WHERE id = ? AND user_id = ?",
                     (data["channelSecret"].strip(), account_id, current_user.id))

    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
@login_required
def delete_account(account_id):
    conn = get_db()
    conn.execute("DELETE FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/accounts/<account_id>/refresh", methods=["POST"])
@login_required
def refresh_friend_count(account_id):
    """LINE APIでフォロワー数を再取得"""
    conn = get_db()
    row = conn.execute("SELECT token FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)).fetchone()

    if row is None:
        conn.close()
        return jsonify({"error": "アカウントが見つかりません"}), 404

    count = get_follower_count(row["token"])
    conn.execute("UPDATE accounts SET friend_count = ? WHERE id = ? AND user_id = ?",
                 (count, account_id, current_user.id))
    conn.commit()
    conn.close()
    return jsonify({"friendCount": count})


# ─── Webhook ───────────────────────────────────────────

def _upsert_friend(conn, account_id, token, line_user_id):
    """LINE友だちをDBに登録/更新（プロフィール取得は初回のみ）"""
    existing = conn.execute(
        "SELECT id FROM line_friends WHERE account_id = ? AND line_user_id = ?",
        (account_id, line_user_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE line_friends SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (existing["id"],),
        )
        return

    # 初回: LINE Profile API でプロフィール取得
    display_name = ""
    picture_url = ""
    try:
        resp = requests.get(
            f"{LINE_API_BASE}/profile/{line_user_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            profile = resp.json()
            display_name = profile.get("displayName", "")
            picture_url = profile.get("pictureUrl", "")
    except Exception:
        pass

    conn.execute(
        "INSERT OR IGNORE INTO line_friends (account_id, line_user_id, display_name, picture_url) VALUES (?, ?, ?, ?)",
        (account_id, line_user_id, display_name, picture_url),
    )


@app.route("/webhook/<account_id>", methods=["POST"])
def webhook(account_id):
    """LINE Webhookイベント受信（follow/unfollow/message）"""
    conn = get_db()
    target = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    if target is None:
        conn.close()
        return jsonify({"error": "Account not found"}), 404

    # パブリックURL自動検出 → 変更時にWebhook URL全更新
    check_and_update_public_url(request.url_root)

    # 署名検証（channelSecretが設定されている場合のみ）
    channel_secret = target["channel_secret"] or ""
    if channel_secret:
        signature = request.headers.get("X-Line-Signature", "")
        body = request.get_data()
        if not verify_signature(channel_secret, body, signature):
            conn.close()
            return jsonify({"error": "Invalid signature"}), 403

    try:
        data = request.get_json()
    except Exception:
        conn.close()
        return jsonify({"error": "Invalid JSON"}), 400

    events = data.get("events", [])
    friend_count = target["friend_count"]
    token = target["token"]

    for event in events:
        event_type = event.get("type")
        user_id = event.get("source", {}).get("userId", "")

        if event_type == "follow":
            friend_count += 1
            if user_id:
                _upsert_friend(conn, account_id, token, user_id)
        elif event_type == "unfollow":
            friend_count = max(0, friend_count - 1)
            if user_id:
                conn.execute(
                    "UPDATE line_friends SET status = 'blocked', updated_at = CURRENT_TIMESTAMP WHERE account_id = ? AND line_user_id = ?",
                    (account_id, user_id),
                )
        elif event_type == "message" and user_id:
            # 友だち登録（未登録なら）
            _upsert_friend(conn, account_id, token, user_id)

            # メッセージ保存
            msg = event.get("message", {})
            msg_type = msg.get("type", "")
            msg_id = msg.get("id", "")
            media_url = ""
            db_msg_type = "text"

            if msg_type == "text":
                text = msg.get("text", "")
            elif msg_type == "image":
                text = "[画像]"
                db_msg_type = "image"
                # LINE Content APIから画像ダウンロード
                if msg_id:
                    filename = download_line_content(token, msg_id)
                    if filename:
                        media_url = f"/uploads/{filename}"
            elif msg_type == "video":
                text = "[動画]"
            elif msg_type == "audio":
                text = "[音声]"
            elif msg_type == "sticker":
                text = "[スタンプ]"
            elif msg_type == "location":
                text = "[位置情報]"
            elif msg_type == "file":
                text = "[ファイル]"
            else:
                text = f"[{msg_type}]"

            conn.execute(
                "INSERT INTO chat_messages (account_id, line_user_id, direction, message_text, message_type, media_url) VALUES (?, ?, 'incoming', ?, ?, ?)",
                (account_id, user_id, text, db_msg_type, media_url),
            )

    conn.execute("UPDATE accounts SET friend_count = ? WHERE id = ?", (friend_count, account_id))
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"}), 200


# ─── 送信 API ──────────────────────────────────────────

@app.route("/api/upload-image", methods=["POST"])
@login_required
def upload_image():
    """画像をアップロードしてURLを返す"""
    file = request.files.get("image")
    if not file:
        return jsonify({"error": "画像ファイルがありません"}), 400

    allowed_ext = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if ext not in allowed_ext:
        return jsonify({"error": "対応形式: JPG, PNG, GIF, WebP"}), 400

    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = UPLOAD_DIR / filename
    file.save(str(filepath))

    return jsonify({"url": f"/uploads/{filename}"})


@app.route("/api/send", methods=["POST"])
@login_required
def send_message():
    data = request.get_json()

    account_ids = data.get("accountIds", [])
    message_text = data.get("message", "").strip()
    mode = data.get("mode", "broadcast")
    user_ids = data.get("userIds", [])
    image_url = data.get("imageUrl", "").strip()

    if not account_ids:
        return jsonify({"error": "送信先アカウントを選択してください"}), 400
    if not message_text and not image_url:
        return jsonify({"error": "メッセージまたは画像を入力してください"}), 400
    if mode == "multicast" and not user_ids:
        return jsonify({"error": "マルチキャスト時はユーザーIDを入力してください"}), 400

    messages = []
    if image_url:
        public_url = get_public_url() or request.url_root.rstrip("/")
        full_image_url = f"{public_url}{image_url}"
        # Flex Messageでリッチカード送信（通知にテキスト表示）
        messages.append(build_flex_image_message(full_image_url, message_text))
    elif message_text:
        messages.append({"type": "text", "text": message_text})

    conn = get_db()
    placeholders = ",".join("?" * len(account_ids))
    rows = conn.execute(
        f"SELECT * FROM accounts WHERE id IN ({placeholders}) AND user_id = ?",
        account_ids + [current_user.id],
    ).fetchall()
    conn.close()

    selected = [{"id": a["id"], "name": a["name"], "token": a["token"]} for a in rows]

    if not selected:
        return jsonify({"error": "選択されたアカウントが見つかりません"}), 400

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(
            send_all(selected, messages, mode, user_ids if mode == "multicast" else None)
        )
    finally:
        loop.close()

    return jsonify({"results": results})


# ─── 予約配信 API ─────────────────────────────────────

@app.route("/api/schedule", methods=["POST"])
@login_required
def create_schedule():
    """予約配信を登録"""
    data = request.get_json()

    account_ids = data.get("accountIds", [])
    message_text = data.get("message", "").strip()
    mode = data.get("mode", "broadcast")
    user_ids = data.get("userIds", [])
    scheduled_at = data.get("scheduledAt", "")
    image_url = data.get("imageUrl", "").strip()

    if not account_ids:
        return jsonify({"error": "送信先アカウントを選択してください"}), 400
    if not message_text and not image_url:
        return jsonify({"error": "メッセージまたは画像を入力してください"}), 400
    if not scheduled_at:
        return jsonify({"error": "予約日時を指定してください"}), 400
    if mode == "multicast" and not user_ids:
        return jsonify({"error": "マルチキャスト時はユーザーIDを入力してください"}), 400

    try:
        run_time = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return jsonify({"error": "無効な日時形式です"}), 400

    if run_time <= datetime.now():
        return jsonify({"error": "予約日時は現在より後の日時を指定してください"}), 400

    schedule_id = str(uuid.uuid4())

    conn = get_db()
    conn.execute(
        "INSERT INTO schedules (id, user_id, account_ids, message, mode, user_ids, scheduled_at, created_at, image_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            schedule_id,
            current_user.id,
            json.dumps(account_ids),
            message_text,
            mode,
            json.dumps(user_ids),
            scheduled_at,
            datetime.now().isoformat(timespec="seconds"),
            image_url,
        ),
    )
    conn.commit()
    conn.close()

    scheduler.add_job(
        execute_scheduled_send,
        "date",
        run_date=run_time,
        args=[schedule_id],
        id=schedule_id,
    )

    return jsonify({"id": schedule_id, "scheduledAt": scheduled_at}), 201


@app.route("/api/schedules", methods=["GET"])
@login_required
def get_schedules():
    """予約一覧を返す"""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM schedules WHERE user_id = ? AND status = 'pending'",
        (current_user.id,),
    ).fetchall()

    result = []
    for s in rows:
        account_ids = json.loads(s["account_ids"])
        # 対象アカウント名を取得
        account_names = []
        if account_ids:
            placeholders = ",".join("?" * len(account_ids))
            acc_rows = conn.execute(
                f"SELECT name FROM accounts WHERE id IN ({placeholders})",
                account_ids,
            ).fetchall()
            account_names = [a["name"] for a in acc_rows]

        result.append({
            "id": s["id"],
            "message": s["message"],
            "mode": s["mode"],
            "scheduledAt": s["scheduled_at"],
            "accountCount": len(account_ids),
            "accountNames": account_names,
            "status": s["status"],
        })

    conn.close()
    return jsonify(result)


@app.route("/api/schedules/<schedule_id>", methods=["DELETE"])
@login_required
def cancel_schedule(schedule_id):
    """予約をキャンセル"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM schedules WHERE id = ? AND user_id = ? AND status = 'pending'",
        (schedule_id, current_user.id),
    ).fetchone()

    if row is None:
        conn.close()
        return jsonify({"error": "予約が見つかりません"}), 404

    conn.execute("UPDATE schedules SET status = 'cancelled' WHERE id = ?", (schedule_id,))
    conn.commit()
    conn.close()

    try:
        scheduler.remove_job(schedule_id)
    except Exception:
        pass

    return jsonify({"success": True})


# ─── チャット機能 ─────────────────────────────────────

@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html")


@app.route("/api/chat/conversations")
@login_required
def chat_conversations():
    """会話一覧（最新メッセージ・未読数付き）"""
    conn = get_db()
    # ユーザーが所有するアカウントIDを取得
    account_rows = conn.execute(
        "SELECT id, name FROM accounts WHERE user_id = ?", (current_user.id,)
    ).fetchall()
    account_map = {r["id"]: r["name"] for r in account_rows}

    if not account_map:
        conn.close()
        return jsonify([])

    placeholders = ",".join("?" * len(account_map))
    account_ids = list(account_map.keys())

    # 各友だちの最新メッセージを取得
    rows = conn.execute(f"""
        SELECT f.account_id, f.line_user_id, f.display_name, f.picture_url,
               m.message_text AS last_message, m.created_at AS last_at, m.id AS last_msg_id,
               COALESCE(
                   (SELECT COUNT(*) FROM chat_messages cm
                    WHERE cm.account_id = f.account_id AND cm.line_user_id = f.line_user_id
                      AND cm.direction = 'incoming'
                      AND cm.id > COALESCE(
                          (SELECT last_read_id FROM chat_read_status rs
                           WHERE rs.account_id = f.account_id AND rs.line_user_id = f.line_user_id), 0)
                   ), 0
               ) AS unread_count
        FROM line_friends f
        LEFT JOIN chat_messages m ON m.id = (
            SELECT MAX(id) FROM chat_messages
            WHERE account_id = f.account_id AND line_user_id = f.line_user_id
        )
        WHERE f.account_id IN ({placeholders})
        ORDER BY m.created_at DESC
    """, account_ids).fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "accountId": r["account_id"],
            "accountName": account_map.get(r["account_id"], ""),
            "lineUserId": r["line_user_id"],
            "displayName": r["display_name"] or r["line_user_id"][:8],
            "pictureUrl": r["picture_url"] or "",
            "lastMessage": r["last_message"] or "",
            "lastAt": r["last_at"] or "",
            "unreadCount": r["unread_count"],
        })
    return jsonify(result)


@app.route("/api/chat/messages/<account_id>/<line_user_id>")
@login_required
def chat_messages(account_id, line_user_id):
    """メッセージ履歴（?since_id=N で差分取得）"""
    conn = get_db()
    # 所有権チェック
    owner = conn.execute(
        "SELECT id FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)
    ).fetchone()
    if not owner:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    since_id = request.args.get("since_id", 0, type=int)
    rows = conn.execute(
        "SELECT id, direction, message_text, message_type, media_url, created_at FROM chat_messages WHERE account_id = ? AND line_user_id = ? AND id > ? ORDER BY id ASC",
        (account_id, line_user_id, since_id),
    ).fetchall()
    conn.close()

    return jsonify([{
        "id": r["id"],
        "direction": r["direction"],
        "text": r["message_text"],
        "messageType": r["message_type"] or "text",
        "mediaUrl": r["media_url"] or "",
        "createdAt": r["created_at"],
    } for r in rows])


@app.route("/api/chat/send", methods=["POST"])
@login_required
def chat_send():
    """個別返信送信（LINE Push Message API）"""
    data = request.get_json()
    account_id = data.get("accountId", "")
    line_user_id = data.get("lineUserId", "")
    text = data.get("text", "").strip()

    if not account_id or not line_user_id or not text:
        return jsonify({"error": "必須項目が不足しています"}), 400

    conn = get_db()
    acc = conn.execute(
        "SELECT token FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)
    ).fetchone()
    if not acc:
        conn.close()
        return jsonify({"error": "アカウントが見つかりません"}), 404

    # LINE Push Message API
    resp = requests.post(
        f"{LINE_API_BASE}/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {acc['token']}",
        },
        json={
            "to": line_user_id,
            "messages": [{"type": "text", "text": text}],
        },
        timeout=10,
    )

    if resp.status_code != 200:
        conn.close()
        try:
            err = resp.json().get("message", resp.text)
        except Exception:
            err = resp.text
        return jsonify({"error": f"送信失敗: {err}"}), 500

    # DB保存
    conn.execute(
        "INSERT INTO chat_messages (account_id, line_user_id, direction, message_text) VALUES (?, ?, 'outgoing', ?)",
        (account_id, line_user_id, text),
    )
    conn.commit()
    msg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return jsonify({"success": True, "messageId": msg_id})


@app.route("/api/chat/mark-read", methods=["POST"])
@login_required
def chat_mark_read():
    """既読更新"""
    data = request.get_json()
    account_id = data.get("accountId", "")
    line_user_id = data.get("lineUserId", "")
    last_read_id = data.get("lastReadId", 0)

    if not account_id or not line_user_id:
        return jsonify({"error": "必須項目が不足しています"}), 400

    conn = get_db()
    # 所有権チェック
    owner = conn.execute(
        "SELECT id FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)
    ).fetchone()
    if not owner:
        conn.close()
        return jsonify({"error": "Not found"}), 404

    conn.execute("""
        INSERT INTO chat_read_status (account_id, line_user_id, last_read_id)
        VALUES (?, ?, ?)
        ON CONFLICT(account_id, line_user_id)
        DO UPDATE SET last_read_id = MAX(last_read_id, excluded.last_read_id)
    """, (account_id, line_user_id, last_read_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route("/api/update-webhooks", methods=["POST"])
@login_required
def api_update_webhooks():
    """手動でWebhook URLを一括更新"""
    public_url = request.get_json().get("publicUrl", "").strip().rstrip("/")
    if not public_url:
        return jsonify({"error": "パブリックURLを指定してください"}), 400

    save_public_url(public_url)

    conn = get_db()
    accounts = conn.execute(
        "SELECT id, token FROM accounts WHERE user_id = ?", (current_user.id,)
    ).fetchall()
    conn.close()

    results = []
    for acc in accounts:
        webhook_url = f"{public_url}/webhook/{acc['id']}"
        try:
            resp = requests.put(
                "https://api.line.me/v2/bot/channel/webhook/endpoint",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {acc['token']}",
                },
                json={"endpoint": webhook_url},
                timeout=10,
            )
            results.append({
                "id": acc["id"],
                "success": resp.status_code == 200,
                "webhookUrl": webhook_url,
            })
        except Exception as e:
            results.append({"id": acc["id"], "success": False, "error": str(e)})

    return jsonify({"results": results})


@app.route("/uploads/<filename>")
def serve_upload(filename):
    """アップロード画像を配信"""
    safe_name = secure_filename(filename)
    if not safe_name or safe_name != filename:
        return jsonify({"error": "Invalid filename"}), 400
    return send_from_directory(str(UPLOAD_DIR), safe_name)


@app.route("/api/chat/send-image", methods=["POST"])
@login_required
def chat_send_image():
    """画像を送信（LINE Image Message + DB保存）"""
    account_id = request.form.get("accountId", "")
    line_user_id = request.form.get("lineUserId", "")
    file = request.files.get("image")

    if not account_id or not line_user_id or not file:
        return jsonify({"error": "必須項目が不足しています"}), 400

    # ファイル種別チェック
    allowed_ext = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ""
    if ext not in allowed_ext:
        return jsonify({"error": "対応形式: JPG, PNG, GIF, WebP"}), 400

    conn = get_db()
    acc = conn.execute(
        "SELECT token FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user.id)
    ).fetchone()
    if not acc:
        conn.close()
        return jsonify({"error": "アカウントが見つかりません"}), 404

    # ファイル保存
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = UPLOAD_DIR / filename
    file.save(str(filepath))

    # パブリックURL構築
    public_url = get_public_url() or request.url_root.rstrip("/")
    image_url = f"{public_url}/uploads/{filename}"

    # LINE Push Message API（画像）
    resp = requests.post(
        f"{LINE_API_BASE}/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {acc['token']}",
        },
        json={
            "to": line_user_id,
            "messages": [{
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url,
            }],
        },
        timeout=10,
    )

    if resp.status_code != 200:
        conn.close()
        try:
            err = resp.json().get("message", resp.text)
        except Exception:
            err = resp.text
        return jsonify({"error": f"送信失敗: {err}"}), 500

    # DB保存
    conn.execute(
        "INSERT INTO chat_messages (account_id, line_user_id, direction, message_text, message_type, media_url) VALUES (?, ?, 'outgoing', '[画像]', 'image', ?)",
        (account_id, line_user_id, f"/uploads/{filename}"),
    )
    conn.commit()
    msg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    return jsonify({"success": True, "messageId": msg_id, "mediaUrl": f"/uploads/{filename}"})


@app.route("/api/chat/unread-count")
@login_required
def chat_unread_count():
    """全アカウントの未読メッセージ総数"""
    conn = get_db()
    account_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM accounts WHERE user_id = ?", (current_user.id,)
    ).fetchall()]

    if not account_ids:
        conn.close()
        return jsonify({"count": 0})

    placeholders = ",".join("?" * len(account_ids))
    row = conn.execute(f"""
        SELECT COUNT(*) AS cnt FROM chat_messages cm
        WHERE cm.account_id IN ({placeholders})
          AND cm.direction = 'incoming'
          AND cm.id > COALESCE(
              (SELECT last_read_id FROM chat_read_status rs
               WHERE rs.account_id = cm.account_id AND rs.line_user_id = cm.line_user_id), 0)
    """, account_ids).fetchone()
    conn.close()

    return jsonify({"count": row["cnt"] if row else 0})


if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    restore_schedules()

if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", 5000))
    if "--dev" in sys.argv:
        app.run(debug=True, port=port)
    else:
        from waitress import serve
        print(f"Starting production server on http://0.0.0.0:{port}")
        serve(app, host="0.0.0.0", port=port, threads=32)
