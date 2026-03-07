// ─── チャット機能 JS ─────────────────────────────────

let currentConv = null;   // { accountId, lineUserId, displayName, pictureUrl, accountName }
let lastMessageId = 0;
let pollTimer = null;
let conversations = [];
let pendingImage = null;  // File object for image to send

// ─── 初期化 ────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    loadConversations();
    // 3秒ごとに会話一覧とメッセージを更新
    pollTimer = setInterval(() => {
        loadConversations();
        if (currentConv) {
            pollMessages();
        }
    }, 3000);
});

// ─── 会話一覧 ──────────────────────────────────────

async function loadConversations() {
    try {
        const resp = await fetch('/api/chat/conversations');
        if (!resp.ok) return;
        conversations = await resp.json();
        renderConversations();
    } catch (e) {
        // ignore
    }
}

function renderConversations() {
    const list = document.getElementById('convList');
    if (conversations.length === 0) {
        list.innerHTML = '<p class="chat-empty">メッセージはまだありません</p>';
        updateTitleBadge(0);
        return;
    }

    // 未読総数 → タブタイトル更新
    const totalUnread = conversations.reduce((sum, c) => sum + (c.unreadCount || 0), 0);
    updateTitleBadge(totalUnread);

    let html = '';
    for (const c of conversations) {
        const isActive = currentConv &&
            currentConv.accountId === c.accountId &&
            currentConv.lineUserId === c.lineUserId;
        const activeClass = isActive ? ' conv-active' : '';
        const unread = c.unreadCount > 0
            ? `<span class="conv-unread">${c.unreadCount > 99 ? '99+' : c.unreadCount}</span>`
            : '';
        const time = c.lastAt ? formatTime(c.lastAt) : '';
        const avatarSrc = c.pictureUrl || defaultAvatar();
        const lastMsg = escapeHtml(truncate(c.lastMessage, 30));

        html += `
        <div class="conv-item${activeClass}" onclick="selectConversation('${c.accountId}', '${c.lineUserId}')">
            <img class="conv-avatar" src="${avatarSrc}" alt="" onerror="this.src='${defaultAvatar()}'">
            <div class="conv-info">
                <div class="conv-top">
                    <span class="conv-name">${escapeHtml(c.displayName)}</span>
                    <span class="conv-time">${time}</span>
                </div>
                <div class="conv-bottom">
                    <span class="conv-last">${lastMsg}</span>
                    ${unread}
                </div>
                <span class="conv-bot">${escapeHtml(c.accountName)}</span>
            </div>
        </div>`;
    }
    list.innerHTML = html;
}

// ─── 会話選択 ──────────────────────────────────────

async function selectConversation(accountId, lineUserId) {
    const conv = conversations.find(c => c.accountId === accountId && c.lineUserId === lineUserId);
    if (!conv) return;

    currentConv = {
        accountId: conv.accountId,
        lineUserId: conv.lineUserId,
        displayName: conv.displayName,
        pictureUrl: conv.pictureUrl,
        accountName: conv.accountName,
    };
    lastMessageId = 0;

    // ヘッダー更新
    document.getElementById('chatHeaderName').textContent = conv.displayName;
    document.getElementById('chatHeaderBot').textContent = conv.accountName;
    const avatar = document.getElementById('chatHeaderAvatar');
    avatar.src = conv.pictureUrl || defaultAvatar();
    avatar.onerror = function() { this.src = defaultAvatar(); };

    // 表示切替
    document.getElementById('chatPlaceholder').classList.add('hidden');
    document.getElementById('chatActive').classList.remove('hidden');
    document.getElementById('chatMessages').innerHTML = '';

    // モバイル: サイドバーを隠してメインを表示
    document.getElementById('chatSidebar').classList.add('chat-sidebar-hidden');
    document.getElementById('chatMain').classList.add('chat-main-visible');

    // メッセージ読み込み
    await loadMessages();
    renderConversations(); // active状態更新

    // 入力欄にフォーカス
    document.getElementById('chatInput').focus();
}

async function loadMessages() {
    if (!currentConv) return;
    try {
        const resp = await fetch(`/api/chat/messages/${currentConv.accountId}/${currentConv.lineUserId}?since_id=${lastMessageId}`);
        if (!resp.ok) return;
        const msgs = await resp.json();
        if (msgs.length === 0) return;

        appendMessages(msgs);
        lastMessageId = msgs[msgs.length - 1].id;

        // 既読更新
        markRead(currentConv.accountId, currentConv.lineUserId, lastMessageId);
    } catch (e) {
        // ignore
    }
}

function pollMessages() {
    loadMessages();
}

function appendMessages(msgs) {
    const container = document.getElementById('chatMessages');
    for (const m of msgs) {
        const div = document.createElement('div');
        div.className = `chat-bubble chat-bubble-${m.direction === 'incoming' ? 'in' : 'out'}`;

        let content;
        if (m.messageType === 'image' && m.mediaUrl) {
            content = `<img class="bubble-image" src="${escapeHtml(m.mediaUrl)}" alt="画像" onclick="openLightbox('${escapeHtml(m.mediaUrl)}')" onerror="this.outerHTML='<div class=\\'bubble-text\\'>[画像の読み込み失敗]</div>'">`;
        } else {
            content = `<div class="bubble-text">${escapeHtml(m.text)}</div>`;
        }

        div.innerHTML = `
            ${content}
            <div class="bubble-time">${formatTime(m.createdAt)}</div>
        `;
        container.appendChild(div);
    }
    container.scrollTop = container.scrollHeight;
}

// ─── 入力 ──────────────────────────────────────────

function handleChatKey(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChat();
    }
    // テキストエリア自動リサイズ
    setTimeout(() => autoResizeInput(event.target), 0);
}

function autoResizeInput(el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// ─── 既読 ──────────────────────────────────────────

async function markRead(accountId, lineUserId, lastReadId) {
    try {
        await fetch('/api/chat/mark-read', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ accountId, lineUserId, lastReadId }),
        });
    } catch (e) {
        // ignore
    }
}

// ─── モバイル: サイドバー表示 ───────────────────────

function showSidebar() {
    document.getElementById('chatSidebar').classList.remove('chat-sidebar-hidden');
    document.getElementById('chatMain').classList.remove('chat-main-visible');
}

// ─── ユーティリティ ────────────────────────────────

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function truncate(str, len) {
    if (!str) return '';
    return str.length > len ? str.slice(0, len) + '...' : str;
}

function formatTime(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr.replace(' ', 'T'));
        const now = new Date();
        const isToday = d.toDateString() === now.toDateString();
        if (isToday) {
            return d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
        }
        return d.toLocaleDateString('ja-JP', { month: 'short', day: 'numeric' });
    } catch {
        return '';
    }
}

function defaultAvatar() {
    return 'data:image/svg+xml,' + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><circle cx="20" cy="20" r="20" fill="#ccc"/><circle cx="20" cy="16" r="7" fill="#fff"/><ellipse cx="20" cy="32" rx="12" ry="9" fill="#fff"/></svg>'
    );
}

// ─── タブタイトル未読バッジ ────────────────────────────

const _origTitle = document.title;
function updateTitleBadge(count) {
    document.title = count > 0 ? `(${count}) ${_origTitle}` : _origTitle;
}

// ─── 画像送信 ────────────────────────────────────────

function handleImageSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    // サイズチェック (10MB)
    if (file.size > 10 * 1024 * 1024) {
        alert('画像は10MB以下にしてください');
        event.target.value = '';
        return;
    }

    pendingImage = file;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('imagePreviewImg').src = e.target.result;
        document.getElementById('imagePreview').classList.remove('hidden');
    };
    reader.readAsDataURL(file);
}

function cancelImagePreview() {
    pendingImage = null;
    document.getElementById('imagePreview').classList.add('hidden');
    document.getElementById('imagePreviewImg').src = '';
    document.getElementById('chatImageInput').value = '';
}

async function sendChat() {
    if (!currentConv) return;

    // 画像送信モード
    if (pendingImage) {
        await sendImage();
        return;
    }

    const input = document.getElementById('chatInput');
    const text = input.value.trim();
    if (!text) return;

    const btn = document.getElementById('chatSendBtn');
    btn.disabled = true;
    input.value = '';
    autoResizeInput(input);

    try {
        const resp = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                accountId: currentConv.accountId,
                lineUserId: currentConv.lineUserId,
                text: text,
            }),
        });
        const data = await resp.json();
        if (resp.ok) {
            appendMessages([{
                id: data.messageId,
                direction: 'outgoing',
                text: text,
                messageType: 'text',
                mediaUrl: '',
                createdAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
            }]);
            lastMessageId = Math.max(lastMessageId, data.messageId);
        } else {
            alert(data.error || '送信に失敗しました');
        }
    } catch (e) {
        alert('送信に失敗しました');
    } finally {
        btn.disabled = false;
        input.focus();
    }
}

async function sendImage() {
    if (!currentConv || !pendingImage) return;

    const btn = document.getElementById('chatSendBtn');
    btn.disabled = true;

    const formData = new FormData();
    formData.append('accountId', currentConv.accountId);
    formData.append('lineUserId', currentConv.lineUserId);
    formData.append('image', pendingImage);

    try {
        const resp = await fetch('/api/chat/send-image', {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json();
        if (resp.ok) {
            appendMessages([{
                id: data.messageId,
                direction: 'outgoing',
                text: '[画像]',
                messageType: 'image',
                mediaUrl: data.mediaUrl,
                createdAt: new Date().toISOString().replace('T', ' ').slice(0, 19),
            }]);
            lastMessageId = Math.max(lastMessageId, data.messageId);
        } else {
            alert(data.error || '画像送信に失敗しました');
        }
    } catch (e) {
        alert('画像送信に失敗しました');
    } finally {
        cancelImagePreview();
        btn.disabled = false;
        document.getElementById('chatInput').focus();
    }
}

// ─── ライトボックス ──────────────────────────────────

function openLightbox(url) {
    document.getElementById('lightboxImg').src = url;
    document.getElementById('lightbox').classList.remove('hidden');
}

function closeLightbox() {
    document.getElementById('lightbox').classList.add('hidden');
    document.getElementById('lightboxImg').src = '';
}
