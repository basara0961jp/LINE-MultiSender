// ステップ配信 JS

let currentScenarioId = null;
let allScenarios = [];

// 初期化: アカウント一覧取得
(function init() {
    fetch('/api/accounts').then(r => r.json()).then(accounts => {
        const sel = document.getElementById('stepAccountSelect');
        accounts.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a.id;
            opt.textContent = a.name;
            sel.appendChild(opt);
        });
    });
})();

function loadScenarios() {
    const accountId = document.getElementById('stepAccountSelect').value;
    const section = document.getElementById('scenarioSection');
    const editSection = document.getElementById('editSection');
    const subsSection = document.getElementById('subsSection');

    editSection.style.display = 'none';
    currentScenarioId = null;

    if (!accountId) {
        section.style.display = 'none';
        subsSection.style.display = 'none';
        return;
    }

    section.style.display = '';
    subsSection.style.display = '';

    fetch('/api/step/scenarios?account_id=' + accountId)
        .then(r => r.json())
        .then(scenarios => {
            allScenarios = scenarios;
            renderScenarios(scenarios);
            renderSubsScenarioSelect(scenarios);
        });
}

function renderScenarios(scenarios) {
    const list = document.getElementById('scenarioList');
    if (!scenarios.length) {
        list.innerHTML = '<p class="empty-msg">シナリオがありません</p>';
        return;
    }
    list.innerHTML = scenarios.map(s => `
        <div class="step-scenario-item">
            <div class="step-scenario-header">
                <span class="step-scenario-name">${esc(s.name)}</span>
                <div class="step-scenario-controls">
                    <label class="step-toggle-label" title="有効/無効">
                        <input type="checkbox" ${s.isActive ? 'checked' : ''} onchange="toggleScenario('${s.id}','isActive',this.checked)">
                        ON
                    </label>
                    <label class="step-toggle-label" title="友だち追加時に自動開始">
                        <input type="checkbox" ${s.autoStart ? 'checked' : ''} onchange="toggleScenario('${s.id}','autoStart',this.checked)">
                        自動開始
                    </label>
                    <button class="action-btn" onclick="editScenario('${s.id}','${esc(s.name)}')">編集</button>
                    <button class="delete-btn" onclick="deleteScenario('${s.id}')">&times;</button>
                </div>
            </div>
        </div>
    `).join('');
}

function renderSubsScenarioSelect(scenarios) {
    const sel = document.getElementById('subsScenarioSelect');
    sel.innerHTML = '<option value="">-- シナリオを選択 --</option>';
    scenarios.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = s.name;
        sel.appendChild(opt);
    });
}

function createScenario() {
    const accountId = document.getElementById('stepAccountSelect').value;
    const name = document.getElementById('newScenarioName').value.trim();
    if (!accountId || !name) return alert('アカウントとシナリオ名を入力してください');

    fetch('/api/step/scenarios', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accountId, name}),
    }).then(r => r.json()).then(d => {
        if (d.error) return alert(d.error);
        document.getElementById('newScenarioName').value = '';
        loadScenarios();
    });
}

function toggleScenario(id, field, value) {
    fetch('/api/step/scenarios/' + id, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[field]: value}),
    });
}

function deleteScenario(id) {
    if (!confirm('このシナリオを削除しますか？')) return;
    fetch('/api/step/scenarios/' + id, {method: 'DELETE'})
        .then(r => r.json()).then(() => {
            if (currentScenarioId === id) {
                document.getElementById('editSection').style.display = 'none';
                currentScenarioId = null;
            }
            loadScenarios();
        });
}

// ── シナリオ編集（ステップメッセージ） ──

function editScenario(id, name) {
    currentScenarioId = id;
    document.getElementById('editScenarioTitle').textContent = 'シナリオ編集: ' + name;
    document.getElementById('editSection').style.display = '';
    loadStepMessages();
}

function loadStepMessages() {
    if (!currentScenarioId) return;
    fetch('/api/step/scenarios/' + currentScenarioId + '/messages')
        .then(r => r.json())
        .then(renderStepMessages);
}

function renderStepMessages(messages) {
    const list = document.getElementById('stepMessageList');
    if (!messages.length) {
        list.innerHTML = '<p class="empty-msg">ステップがありません</p>';
        return;
    }
    list.innerHTML = messages.map((m, i) => `
        <div class="step-msg-card" data-id="${m.id}">
            <div class="step-msg-header">
                <span class="step-msg-num">ステップ ${m.stepNumber}</span>
                <span class="step-msg-delay">${formatDelay(m.delayMinutes)}</span>
                <button class="delete-btn" onclick="deleteStepMsg(${m.id})">&times;</button>
            </div>
            <div class="step-msg-body">
                <div class="step-delay-input">
                    <label>遅延:</label>
                    <input type="number" min="0" value="${m.delayMinutes}" id="delay_${m.id}" style="width:80px">
                    <span>分</span>
                    <span class="step-delay-hint">(${formatDelay(m.delayMinutes)})</span>
                </div>
                <textarea rows="3" id="text_${m.id}" placeholder="メッセージ本文">${esc(m.messageText)}</textarea>
                <div class="step-img-row">
                    <input type="file" id="imgfile_${m.id}" accept="image/*" style="display:none" onchange="uploadStepImg(${m.id},event)">
                    <button class="image-attach-btn" onclick="document.getElementById('imgfile_${m.id}').click()">画像</button>
                    ${m.imageUrl ? `<img src="${m.imageUrl}" class="step-img-preview"><button class="image-preview-cancel" onclick="clearStepImg(${m.id})">&times;</button>` : ''}
                    <input type="hidden" id="imgurl_${m.id}" value="${esc(m.imageUrl)}">
                </div>
                <button class="action-btn" onclick="saveStepMsg(${m.id}, ${m.stepNumber})" style="margin-top:8px">保存</button>
            </div>
        </div>
    `).join('');
}

function formatDelay(minutes) {
    if (minutes < 60) return minutes + '分後';
    if (minutes < 1440) return Math.floor(minutes / 60) + '時間' + (minutes % 60 ? minutes % 60 + '分' : '') + '後';
    const d = Math.floor(minutes / 1440);
    const h = Math.floor((minutes % 1440) / 60);
    return d + '日' + (h ? h + '時間' : '') + '後';
}

function addStepMessage() {
    if (!currentScenarioId) return;
    // 次のステップ番号を算出
    const cards = document.querySelectorAll('.step-msg-card');
    const nextStep = cards.length + 1;

    fetch('/api/step/scenarios/' + currentScenarioId + '/messages', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stepNumber: nextStep, delayMinutes: 0, messageText: '', imageUrl: ''}),
    }).then(r => r.json()).then(d => {
        if (d.error) return alert(d.error);
        loadStepMessages();
    });
}

function saveStepMsg(msgId, stepNumber) {
    const delay = parseInt(document.getElementById('delay_' + msgId).value) || 0;
    const text = document.getElementById('text_' + msgId).value;
    const imgUrl = document.getElementById('imgurl_' + msgId).value;

    fetch('/api/step/scenarios/' + currentScenarioId + '/messages/' + msgId, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({stepNumber, delayMinutes: delay, messageText: text, imageUrl: imgUrl}),
    }).then(r => r.json()).then(d => {
        if (d.error) return alert(d.error);
        loadStepMessages();
    });
}

function deleteStepMsg(msgId) {
    if (!confirm('このステップを削除しますか？')) return;
    fetch('/api/step/scenarios/' + currentScenarioId + '/messages/' + msgId, {method: 'DELETE'})
        .then(r => r.json()).then(() => loadStepMessages());
}

function uploadStepImg(msgId, event) {
    const file = event.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('image', file);
    fetch('/api/step/upload-image', {method: 'POST', body: fd})
        .then(r => r.json()).then(d => {
            if (d.error) return alert(d.error);
            document.getElementById('imgurl_' + msgId).value = d.imageUrl;
            // 即保存ではなく、プレビュー表示して保存ボタンで確定
            loadStepMessages();
        });
}

function clearStepImg(msgId) {
    document.getElementById('imgurl_' + msgId).value = '';
    loadStepMessages();
}

// ── 配信状況 ──

function loadSubscriptions() {
    const scenarioId = document.getElementById('subsScenarioSelect').value;
    const manualArea = document.getElementById('manualStartArea');
    const subsList = document.getElementById('subsList');

    if (!scenarioId) {
        manualArea.style.display = 'none';
        subsList.innerHTML = '';
        return;
    }

    manualArea.style.display = '';
    loadFriendsForManualStart();

    fetch('/api/step/subscriptions?scenario_id=' + scenarioId)
        .then(r => r.json())
        .then(subs => {
            if (!subs.length) {
                subsList.innerHTML = '<p class="empty-msg">配信中の友だちはいません</p>';
                return;
            }
            subsList.innerHTML = subs.map(s => `
                <div class="step-sub-item">
                    <img src="${s.pictureUrl || 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 40 40%22><circle cx=%2220%22 cy=%2220%22 r=%2220%22 fill=%22%23ccc%22/></svg>'}" class="friend-avatar">
                    <div class="step-sub-info">
                        <span class="step-sub-name">${esc(s.displayName)}</span>
                        <span class="step-sub-meta">ステップ ${s.currentStep} / ${statusLabel(s.status)}</span>
                    </div>
                    <button class="action-btn" onclick="toggleSub(${s.id})">${s.status === 'active' ? '一時停止' : '再開'}</button>
                </div>
            `).join('');
        });
}

function loadFriendsForManualStart() {
    const accountId = document.getElementById('stepAccountSelect').value;
    if (!accountId) return;
    fetch('/api/friends')
        .then(r => r.json())
        .then(friends => {
            const sel = document.getElementById('manualFriendSelect');
            sel.innerHTML = '<option value="">-- 友だちを選択 --</option>';
            friends.filter(f => f.accountId === accountId && f.status === 'active').forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.lineUserId;
                opt.textContent = f.displayName || f.lineUserId;
                sel.appendChild(opt);
            });
        });
}

function manualStart() {
    const scenarioId = document.getElementById('subsScenarioSelect').value;
    const lineUserId = document.getElementById('manualFriendSelect').value;
    if (!scenarioId || !lineUserId) return alert('シナリオと友だちを選択してください');

    fetch('/api/step/subscriptions/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenarioId, lineUserId}),
    }).then(r => r.json()).then(d => {
        if (d.error) return alert(d.error);
        loadSubscriptions();
    });
}

function toggleSub(subId) {
    fetch('/api/step/subscriptions/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: subId}),
    }).then(r => r.json()).then(() => loadSubscriptions());
}

function statusLabel(s) {
    if (s === 'active') return '<span style="color:#06c755;font-weight:bold">配信中</span>';
    if (s === 'paused') return '<span style="color:#ffc107;font-weight:bold">一時停止</span>';
    return '<span style="color:#888;font-weight:bold">完了</span>';
}

function esc(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
