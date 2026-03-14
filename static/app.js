document.addEventListener("DOMContentLoaded", () => {
    loadAccounts();
    loadSchedules();

    // 送信モード切替
    document.querySelectorAll('input[name="mode"]').forEach(radio => {
        radio.addEventListener("change", () => {
            document.getElementById("userIdsArea").classList.toggle(
                "hidden", radio.value !== "multicast" || !radio.checked
            );
        });
    });

    // 文字数カウント
    const messageInput = document.getElementById("messageInput");
    if (messageInput) {
        messageInput.addEventListener("input", (e) => {
            document.getElementById("charCount").textContent = e.target.value.length;
        });
    }

    // Enterキーでトークン追加
    const tokenInput = document.getElementById("tokenInput");
    if (tokenInput) {
        tokenInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") addAccount();
        });
    }

    // 登録URL表示
    const registerUrlEl = document.getElementById("registerUrl");
    if (registerUrlEl) {
        registerUrlEl.textContent = `${window.location.origin}/register`;
    }

    // 予約チェック切替
    const scheduleCheck = document.getElementById("scheduleCheck");
    if (scheduleCheck) {
        scheduleCheck.addEventListener("change", (e) => {
            document.getElementById("scheduleInput").classList.toggle("hidden", !e.target.checked);
            document.getElementById("sendBtn").textContent = e.target.checked ? "予約" : "送信";
        });
    }
});

// ─── アカウント一覧 ───────────────────────────────────

async function loadAccounts() {
    try {
        const resp = await fetch("/api/accounts");
        const accounts = await resp.json();
        renderAccounts(accounts);
        renderCapacityOverview(accounts);
    } catch (err) {
        console.error("Failed to load accounts:", err);
    }
}

function getProgressColor(pct) {
    if (pct >= 90) return "red";
    if (pct >= 70) return "yellow";
    return "green";
}

// 現在のページがアカウント管理ページかどうか
const isAccountsPage = window.location.pathname === "/accounts";

function renderAccounts(accounts) {
    const list = document.getElementById("accountList");
    if (accounts.length === 0) {
        if (isAccountsPage) {
            list.innerHTML = '<p class="empty-msg">アカウントが登録されていません</p>';
        } else {
            list.innerHTML = '<p class="empty-msg">アカウントが登録されていません。<a href="/accounts">アカウント管理</a>から追加してください。</p>';
        }
        return;
    }

    const baseUrl = window.location.origin;

    if (isAccountsPage) {
        // アカウント管理ページ: フル表示
        list.innerHTML = accounts.map(acc => {
            const pct = acc.maxFriends > 0 ? Math.min(100, Math.round(acc.friendCount / acc.maxFriends * 100)) : 0;
            const color = getProgressColor(pct);
            const webhookUrl = `${baseUrl}/webhook/${acc.id}`;

            const banBadge = acc.apiStatus === 'banned' ? ' <span style="background:#e65100;color:#fff;padding:1px 6px;border-radius:4px;font-size:0.7rem">垢BAN</span>' : '';
            return `
            <div class="account-item" data-id="${acc.id}" ${acc.apiStatus === 'banned' ? 'style="border-left:4px solid #e65100"' : ''}>
                <div class="account-header">
                    <span class="name">${escapeHtml(acc.name)}${banBadge}</span>
                    <button class="action-btn" onclick="refreshFriendCount('${acc.id}')" title="友だち数を更新">&#8635;</button>
                    <button class="action-btn" onclick="openEditModal('${acc.id}', ${acc.maxFriends})" title="設定">&#9881;</button>
                    <button class="delete-btn" onclick="deleteAccount('${acc.id}')" title="削除">&#10005;</button>
                </div>
                <div class="account-detail">
                    <div class="friend-info">
                        友だち: <span class="count">${acc.friendCount} / ${acc.maxFriends}</span>
                        (${pct}%)
                    </div>
                    <div class="progress-bar">
                        <div class="fill ${color}" style="width: ${pct}%"></div>
                    </div>
                    <div class="webhook-info">
                        Webhook:
                        <code>${escapeHtml(webhookUrl)}</code>
                        <button class="copy-btn" onclick="copyText('${webhookUrl}')" title="コピー">&#128203;</button>
                    </div>
                </div>
            </div>`;
        }).join("");
    } else {
        // 配信ページ: チェックボックス + 名前のみ
        list.innerHTML = accounts.map(acc => {
            const pct = acc.maxFriends > 0 ? Math.min(100, Math.round(acc.friendCount / acc.maxFriends * 100)) : 0;
            const banBadge = acc.apiStatus === 'banned' ? ' <span style="background:#e65100;color:#fff;padding:1px 6px;border-radius:4px;font-size:0.7rem">垢BAN</span>' : '';
            return `
            <div class="account-item" data-id="${acc.id}">
                <div class="account-header">
                    <input type="checkbox" ${acc.apiStatus === 'banned' ? '' : 'checked'}>
                    <span class="name">${escapeHtml(acc.name)}${banBadge}</span>
                    <span class="friend-count-badge">${acc.friendCount}人</span>
                </div>
            </div>`;
        }).join("");
    }
}

function renderCapacityOverview(accounts) {
    const el = document.getElementById("capacityOverview");
    if (accounts.length === 0) {
        el.innerHTML = "";
        return;
    }

    el.innerHTML = accounts.map(acc => {
        const pct = acc.maxFriends > 0 ? Math.min(100, Math.round(acc.friendCount / acc.maxFriends * 100)) : 0;
        const color = getProgressColor(pct);
        const status = acc.friendCount < acc.maxFriends ? "空きあり" : "満員";

        return `
        <div class="capacity-row">
            <span class="cap-name">${escapeHtml(acc.name)}</span>
            <div class="cap-bar">
                <div class="cap-fill ${color}" style="width: ${pct}%"></div>
            </div>
            <span class="cap-text">${acc.friendCount}/${acc.maxFriends} ${status}</span>
        </div>`;
    }).join("");
}

// ─── アカウント追加 ───────────────────────────────────

async function addAccount() {
    const tokenInput = document.getElementById("tokenInput");
    const secretInput = document.getElementById("secretInput");
    const maxFriendsInput = document.getElementById("maxFriendsInput");
    const btn = document.getElementById("addBtn");
    const token = tokenInput.value.trim();

    if (!token) return;

    btn.disabled = true;
    btn.textContent = "検証中...";

    try {
        const resp = await fetch("/api/accounts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                token,
                channelSecret: secretInput.value.trim(),
                maxFriends: parseInt(maxFriendsInput.value) || 500,
            }),
        });
        const data = await resp.json();

        if (resp.ok) {
            tokenInput.value = "";
            secretInput.value = "";
            maxFriendsInput.value = "500";
            loadAccounts();
        } else {
            alert(data.error || "追加に失敗しました");
        }
    } catch (err) {
        alert("通信エラーが発生しました");
    } finally {
        btn.disabled = false;
        btn.textContent = "追加";
    }
}

// ─── アカウント削除 ───────────────────────────────────

async function deleteAccount(id) {
    if (!confirm("このアカウントを削除しますか？")) return;

    try {
        await fetch(`/api/accounts/${id}`, { method: "DELETE" });
        loadAccounts();
    } catch (err) {
        alert("削除に失敗しました");
    }
}

// ─── 友だち数リフレッシュ ─────────────────────────────

async function refreshFriendCount(id) {
    try {
        const resp = await fetch(`/api/accounts/${id}/refresh`, { method: "POST" });
        const data = await resp.json();
        if (resp.ok) {
            loadAccounts();
        } else {
            alert(data.error || "更新に失敗しました");
        }
    } catch (err) {
        alert("通信エラーが発生しました");
    }
}

// ─── 設定モーダル ─────────────────────────────────────

function openEditModal(id, currentMax) {
    document.getElementById("editAccountId").value = id;
    document.getElementById("editMaxFriends").value = currentMax;
    document.getElementById("editChannelSecret").value = "";
    document.getElementById("editModal").classList.remove("hidden");
}

function closeEditModal() {
    document.getElementById("editModal").classList.add("hidden");
}

async function saveAccountSettings() {
    const id = document.getElementById("editAccountId").value;
    const maxFriends = parseInt(document.getElementById("editMaxFriends").value);
    const channelSecret = document.getElementById("editChannelSecret").value.trim();

    if (!maxFriends || maxFriends < 1) {
        alert("有効な上限数を入力してください");
        return;
    }

    const body = { maxFriends };
    if (channelSecret) {
        body.channelSecret = channelSecret;
    }

    try {
        const resp = await fetch(`/api/accounts/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (resp.ok) {
            closeEditModal();
            loadAccounts();
        } else {
            alert(data.error || "保存に失敗しました");
        }
    } catch (err) {
        alert("通信エラーが発生しました");
    }
}

// ─── 画像添付 ──────────────────────────────────────────

let broadcastPendingImage = null;

function handleBroadcastImage(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (file.size > 10 * 1024 * 1024) {
        alert("画像は10MB以下にしてください");
        event.target.value = "";
        return;
    }

    broadcastPendingImage = file;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById("broadcastPreviewImg").src = e.target.result;
        document.getElementById("broadcastImagePreview").classList.remove("hidden");
    };
    reader.readAsDataURL(file);
}

function cancelBroadcastImage() {
    broadcastPendingImage = null;
    document.getElementById("broadcastImagePreview").classList.add("hidden");
    document.getElementById("broadcastPreviewImg").src = "";
    document.getElementById("broadcastImageInput").value = "";
}

async function uploadBroadcastImage() {
    if (!broadcastPendingImage) return null;
    const formData = new FormData();
    formData.append("image", broadcastPendingImage);
    const resp = await fetch("/api/upload-image", { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "画像アップロード失敗");
    return data.url;
}

// ─── 送信 ────────────────────────────────────────────

async function sendMessage() {
    const btn = document.getElementById("sendBtn");
    const message = document.getElementById("messageInput").value.trim();
    const mode = document.querySelector('input[name="mode"]:checked').value;

    // 選択されたアカウント取得
    const accountIds = [];
    document.querySelectorAll(".account-item").forEach(item => {
        if (item.querySelector("input[type='checkbox']").checked) {
            accountIds.push(item.dataset.id);
        }
    });

    if (accountIds.length === 0) {
        alert("送信先アカウントを選択してください");
        return;
    }
    if (!message && !broadcastPendingImage) {
        alert("メッセージまたは画像を入力してください");
        return;
    }
    if (message.length > 5000) {
        alert("メッセージは5000文字以内にしてください");
        return;
    }

    // マルチキャスト時のユーザーID
    let userIds = [];
    if (mode === "multicast") {
        const raw = document.getElementById("userIdsInput").value.trim();
        userIds = raw.split("\n").map(s => s.trim()).filter(s => s.length > 0);
        if (userIds.length === 0) {
            alert("ユーザーIDを入力してください");
            return;
        }
    }

    // 確認ダイアログ
    const modeLabel = mode === "broadcast" ? "ブロードキャスト（全友だち）" : `マルチキャスト（${userIds.length}名）`;
    const imgNote = broadcastPendingImage ? "\n画像あり" : "";
    if (!confirm(`${accountIds.length}個のアカウントから${modeLabel}で送信します。${imgNote}\nよろしいですか？`)) {
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span>送信中...';

    try {
        // 画像がある場合は先にアップロード
        let imageUrl = "";
        if (broadcastPendingImage) {
            imageUrl = await uploadBroadcastImage();
        }

        const resp = await fetch("/api/send", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ accountIds, message, mode, userIds, imageUrl }),
        });
        const data = await resp.json();

        if (data.error) {
            alert(data.error);
        } else {
            showResults(data.results);
            cancelBroadcastImage();
        }
    } catch (err) {
        alert(err.message || "通信エラーが発生しました");
    } finally {
        btn.disabled = false;
        btn.textContent = "送信";
    }
}

function showResults(results) {
    const section = document.getElementById("resultSection");
    const list = document.getElementById("resultList");

    section.classList.remove("hidden");

    list.innerHTML = results.map(r => {
        if (r.success) {
            return `
                <div class="result-item success">
                    <span class="icon">&#10004;</span>
                    <div class="detail">
                        <div class="name">${escapeHtml(r.name)}</div>
                        <div class="msg">送信成功</div>
                    </div>
                </div>`;
        } else if (r.banned) {
            return `
                <div class="result-item error" style="background:#fff3e0;color:#e65100;border-left:4px solid #e65100">
                    <span class="icon" style="font-size:1.5rem">&#9888;</span>
                    <div class="detail">
                        <div class="name">${escapeHtml(r.name)} <span style="background:#e65100;color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75rem;margin-left:6px">垢BAN</span></div>
                        <div class="msg">このアカウントはBANされています。メッセージは送信されていません。</div>
                    </div>
                </div>`;
        } else {
            return `
                <div class="result-item error">
                    <span class="icon">&#10008;</span>
                    <div class="detail">
                        <div class="name">${escapeHtml(r.name)}</div>
                        <div class="msg">エラー: ${escapeHtml(r.error || "不明なエラー")}</div>
                    </div>
                </div>`;
        }
    }).join("");

    section.scrollIntoView({ behavior: "smooth" });
}

// ─── 予約配信 ─────────────────────────────────────────

function handleSend() {
    if (document.getElementById("scheduleCheck").checked) {
        scheduleMessage();
    } else {
        sendMessage();
    }
}

async function loadSchedules() {
    try {
        const resp = await fetch("/api/schedules");
        const schedules = await resp.json();
        renderSchedules(schedules);
    } catch (err) {
        console.error("Failed to load schedules:", err);
    }
}

function renderSchedules(schedules) {
    const section = document.getElementById("scheduleSection");
    const list = document.getElementById("scheduleList");
    if (!section || !list) return;

    if (schedules.length === 0) {
        section.classList.add("hidden");
        return;
    }

    section.classList.remove("hidden");
    list.innerHTML = schedules.map(s => {
        const dt = new Date(s.scheduledAt);
        const dateStr = dt.toLocaleString("ja-JP", {
            year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit"
        });
        const msgPreview = s.message.length > 30 ? s.message.substring(0, 30) + "..." : s.message;
        const modeLabel = s.mode === "broadcast" ? "ブロードキャスト" : "マルチキャスト";

        return `
        <div class="schedule-card">
            <div class="schedule-header">
                <span class="schedule-time">${escapeHtml(dateStr)}</span>
                <button class="schedule-cancel-btn" onclick="cancelSchedule('${s.id}')" title="キャンセル">&#10005;</button>
            </div>
            <div class="schedule-body">
                <div class="schedule-msg">${escapeHtml(msgPreview)}</div>
                <div class="schedule-meta">${modeLabel} / ${s.accountCount}アカウント</div>
            </div>
        </div>`;
    }).join("");
}

async function scheduleMessage() {
    const btn = document.getElementById("sendBtn");
    const message = document.getElementById("messageInput").value.trim();
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const scheduledAt = document.getElementById("scheduledAt").value;

    // 選択されたアカウント取得
    const accountIds = [];
    document.querySelectorAll(".account-item").forEach(item => {
        if (item.querySelector("input[type='checkbox']").checked) {
            accountIds.push(item.dataset.id);
        }
    });

    if (accountIds.length === 0) {
        alert("送信先アカウントを選択してください");
        return;
    }
    if (!message && !broadcastPendingImage) {
        alert("メッセージまたは画像を入力してください");
        return;
    }
    if (message.length > 5000) {
        alert("メッセージは5000文字以内にしてください");
        return;
    }
    if (!scheduledAt) {
        alert("予約日時を指定してください");
        return;
    }

    let userIds = [];
    if (mode === "multicast") {
        const raw = document.getElementById("userIdsInput").value.trim();
        userIds = raw.split("\n").map(s => s.trim()).filter(s => s.length > 0);
        if (userIds.length === 0) {
            alert("ユーザーIDを入力してください");
            return;
        }
    }

    const dt = new Date(scheduledAt);
    const dateStr = dt.toLocaleString("ja-JP", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit"
    });
    const imgNote = broadcastPendingImage ? "\n画像あり" : "";
    if (!confirm(`${dateStr} に${accountIds.length}個のアカウントから予約送信します。${imgNote}\nよろしいですか？`)) {
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span>登録中...';

    try {
        // 画像がある場合は先にアップロード
        let imageUrl = "";
        if (broadcastPendingImage) {
            imageUrl = await uploadBroadcastImage();
        }

        const resp = await fetch("/api/schedule", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ accountIds, message, mode, userIds, scheduledAt, imageUrl }),
        });
        const data = await resp.json();

        if (resp.ok) {
            alert("予約を登録しました");
            loadSchedules();
            cancelBroadcastImage();
        } else {
            alert(data.error || "予約に失敗しました");
        }
    } catch (err) {
        alert(err.message || "通信エラーが発生しました");
    } finally {
        btn.disabled = false;
        btn.textContent = "予約";
    }
}

async function cancelSchedule(id) {
    if (!confirm("この予約をキャンセルしますか？")) return;

    try {
        const resp = await fetch(`/api/schedules/${id}`, { method: "DELETE" });
        const data = await resp.json();

        if (resp.ok) {
            loadSchedules();
        } else {
            alert(data.error || "キャンセルに失敗しました");
        }
    } catch (err) {
        alert("通信エラーが発生しました");
    }
}

// ─── Webhook URL一括更新 ────────────────────────────────

async function updateWebhooks() {
    const input = document.getElementById("publicUrlInput");
    const btn = document.getElementById("updateWebhookBtn");
    const resultDiv = document.getElementById("webhookResult");
    const publicUrl = input.value.trim().replace(/\/+$/, "");

    if (!publicUrl || !publicUrl.startsWith("https://")) {
        alert("https:// で始まるURLを入力してください");
        return;
    }

    btn.disabled = true;
    btn.textContent = "更新中...";

    try {
        const resp = await fetch("/api/update-webhooks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ publicUrl }),
        });
        const data = await resp.json();

        if (data.error) {
            alert(data.error);
        } else {
            let html = data.results.map(r =>
                `<div class="result-item ${r.success ? 'success' : 'error'}">
                    <span class="icon">${r.success ? '&#10004;' : '&#10008;'}</span>
                    <div class="detail">
                        <div class="msg">${r.success ? '更新成功' : 'エラー: ' + escapeHtml(r.error || '不明')}</div>
                    </div>
                </div>`
            ).join("");
            resultDiv.innerHTML = html;
        }
    } catch (err) {
        alert("通信エラーが発生しました");
    } finally {
        btn.disabled = false;
        btn.textContent = "一括更新";
    }
}

// ─── ユーティリティ ───────────────────────────────────

function copyText(text) {
    navigator.clipboard.writeText(text).then(() => {
        // 一時的にツールチップ表示（簡易版）
    }).catch(() => {
        // フォールバック
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
    });
}

function copyRegisterUrl() {
    const url = document.getElementById("registerUrl").textContent;
    copyText(url);
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
