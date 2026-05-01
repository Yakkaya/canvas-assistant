// ==UserScript==
// @name         Canvas Assistant
// @namespace    http://tampermonkey.net/
// @version      1.1
// @description  AI-powered Canvas study assistant — floating chatbot widget
// @author       you
// @match        *://*.instructure.com/*
// @match        *://canvas.calpoly.edu/*
// @grant        GM_xmlhttpRequest
// @connect      localhost
// ==/UserScript==

(function () {
    'use strict';

    const API_URL = 'http://localhost:8000/chat';
    const MANUAL_UPDATE_URL = 'http://localhost:8000/manual-update';
    const FORM_DATA_URL = 'http://localhost:8000/form-data';
    const SESSION_KEY = 'canvas_assistant_session_id';
    const HISTORY_KEY = 'canvas_assistant_history';

    let sessionId = localStorage.getItem(SESSION_KEY) || null;
    let chatHistory = [];
    try {
        chatHistory = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
    } catch (e) {
        chatHistory = [];
    }

    // -------------------------------------------------------------------------
    // Styles
    // -------------------------------------------------------------------------
    const style = document.createElement('style');
    style.textContent = `
        #ca-btn {
            position: fixed;
            bottom: 24px;
            right: 24px;
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #0770A3;
            border: none;
            cursor: pointer;
            box-shadow: 0 4px 14px rgba(0,0,0,0.3);
            z-index: 99999;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.15s, box-shadow 0.15s;
        }
        #ca-btn:hover {
            transform: scale(1.08);
            box-shadow: 0 6px 18px rgba(0,0,0,0.35);
        }
        #ca-btn svg { fill: white; width: 26px; height: 26px; }

        #ca-panel {
            position: fixed;
            bottom: 92px;
            right: 24px;
            width: 370px;
            height: 510px;
            background: #fff;
            border-radius: 14px;
            box-shadow: 0 8px 36px rgba(0,0,0,0.18);
            z-index: 99998;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, sans-serif;
            font-size: 14px;
            transition: opacity 0.2s, transform 0.2s;
        }
        #ca-panel.ca-hidden {
            opacity: 0;
            pointer-events: none;
            transform: translateY(12px);
        }

        #ca-header {
            background: #0770A3;
            color: white;
            padding: 13px 16px;
            font-weight: 600;
            font-size: 15px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-shrink: 0;
        }
        #ca-header-title {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #ca-header-title svg { fill: white; width: 18px; height: 18px; }
        #ca-clear-btn {
            background: none;
            border: none;
            color: rgba(255,255,255,0.65);
            cursor: pointer;
            font-size: 12px;
            padding: 3px 7px;
            border-radius: 4px;
            font-family: inherit;
        }
        #ca-clear-btn:hover { color: white; background: rgba(255,255,255,0.15); }

        #ca-messages {
            flex: 1;
            overflow-y: auto;
            padding: 14px 12px;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        #ca-messages::-webkit-scrollbar { width: 4px; }
        #ca-messages::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 4px; }

        .ca-msg {
            max-width: 86%;
            padding: 9px 13px;
            border-radius: 16px;
            line-height: 1.5;
            white-space: pre-wrap;
            word-wrap: break-word;
            font-size: 13.5px;
        }
        .ca-msg.ca-user {
            align-self: flex-end;
            background: #0770A3;
            color: white;
            border-bottom-right-radius: 4px;
        }
        .ca-msg.ca-assistant {
            align-self: flex-start;
            background: #f0f2f5;
            color: #1a1a1a;
            border-bottom-left-radius: 4px;
        }
        .ca-msg.ca-thinking {
            align-self: flex-start;
            background: #f0f2f5;
            color: #888;
            font-style: italic;
            font-size: 13px;
        }
        .ca-empty {
            color: #9ca3af;
            text-align: center;
            margin: auto;
            padding: 24px 20px;
            line-height: 1.7;
        }
        .ca-empty strong {
            color: #0770A3;
            display: block;
            margin-bottom: 8px;
            font-size: 15px;
        }

        #ca-input-area {
            border-top: 1px solid #e5e7eb;
            padding: 10px;
            display: flex;
            gap: 8px;
            align-items: flex-end;
            flex-shrink: 0;
        }
        #ca-input {
            flex: 1;
            border: 1.5px solid #d1d5db;
            border-radius: 9px;
            padding: 8px 12px;
            font-family: inherit;
            font-size: 13.5px;
            resize: none;
            outline: none;
            min-height: 38px;
            max-height: 100px;
            line-height: 1.45;
            color: #1a1a1a;
        }
        #ca-input:focus { border-color: #0770A3; }
        #ca-send {
            background: #0770A3;
            border: none;
            border-radius: 9px;
            width: 36px;
            height: 36px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            transition: background 0.15s;
        }
        #ca-send:hover { background: #055a80; }
        #ca-send:disabled { background: #9ca3af; cursor: not-allowed; }
        #ca-send svg { fill: white; width: 16px; height: 16px; }

        #ca-edit-btn {
            background: none;
            border: none;
            color: rgba(255,255,255,0.65);
            cursor: pointer;
            font-size: 18px;
            line-height: 1;
            padding: 3px 7px;
            border-radius: 4px;
            font-family: inherit;
        }
        #ca-edit-btn:hover { color: white; background: rgba(255,255,255,0.15); }

        #ca-form-panel {
            display: none;
            flex-direction: column;
            flex: 1;
            overflow-y: auto;
            padding: 14px 16px;
            gap: 10px;
        }
        #ca-form-panel.ca-form-visible { display: flex; }
        .ca-form-group {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        .ca-form-group > span {
            font-size: 11px;
            font-weight: 700;
            color: #555;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .ca-fi {
            border: 1.5px solid #d1d5db;
            border-radius: 7px;
            padding: 7px 10px;
            font-family: inherit;
            font-size: 13.5px;
            outline: none;
            color: #1a1a1a;
            width: 100%;
            box-sizing: border-box;
            background: white;
        }
        .ca-fi:focus { border-color: #0770A3; }
        .ca-fi-check {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13.5px;
            color: #1a1a1a;
            cursor: pointer;
            padding: 4px 0;
        }
        #ca-form-submit {
            background: #0770A3;
            color: white;
            border: none;
            border-radius: 9px;
            padding: 10px;
            font-size: 14px;
            font-family: inherit;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.15s;
            margin-top: 2px;
        }
        #ca-form-submit:hover { background: #055a80; }
        #ca-form-submit:disabled { background: #9ca3af; cursor: not-allowed; }
        #ca-form-status {
            font-size: 13px;
            text-align: center;
            min-height: 18px;
        }
        .ca-status-success { color: #16a34a; }
        .ca-status-error { color: #dc2626; }
        .ca-status-info { color: #888; }
    `;
    document.head.appendChild(style);

    // -------------------------------------------------------------------------
    // DOM
    // -------------------------------------------------------------------------
    const btn = document.createElement('button');
    btn.id = 'ca-btn';
    btn.title = 'Canvas Assistant';
    btn.innerHTML = `<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/></svg>`;
    document.body.appendChild(btn);

    const panel = document.createElement('div');
    panel.id = 'ca-panel';
    panel.className = 'ca-hidden';
    panel.innerHTML = `
        <div id="ca-header">
            <div id="ca-header-title">
                <svg viewBox="0 0 24 24"><path d="M12 3L1 9l11 6 9-4.91V17h2V9L12 3zm0 12.08L4.35 11 12 7.5 19.65 11 12 15.08zM1 17l11 6 11-6-2-1.09-9 4.9-9-4.9L1 17z"/></svg>
                Canvas Assistant
            </div>
            <div style="display:flex;gap:4px;align-items:center">
                <button id="ca-edit-btn" title="Update course data">✏</button>
                <button id="ca-clear-btn" title="Clear conversation">Clear</button>
            </div>
        </div>
        <div id="ca-messages"></div>
        <div id="ca-form-panel">
            <div class="ca-form-group">
                <span>Update type</span>
                <select class="ca-fi" id="ca-form-type">
                    <option value="">Select…</option>
                    <option value="grading_weight">Grading Weight</option>
                    <option value="late_policy">Late Policy</option>
                    <option value="assignment_category">Assignment Category</option>
                </select>
            </div>
            <div id="ca-form-fields"></div>
            <button id="ca-form-submit" style="display:none">Save</button>
            <div id="ca-form-status"></div>
        </div>
        <div id="ca-input-area">
            <textarea id="ca-input" placeholder="Ask about assignments, deadlines, grades…" rows="1"></textarea>
            <button id="ca-send">
                <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
            </button>
        </div>
    `;
    document.body.appendChild(panel);

    const messagesEl = panel.querySelector('#ca-messages');
    const inputEl = panel.querySelector('#ca-input');
    const sendBtn = panel.querySelector('#ca-send');
    const clearBtn = panel.querySelector('#ca-clear-btn');

    // -------------------------------------------------------------------------
    // Render
    // -------------------------------------------------------------------------
    function renderHistory() {
        messagesEl.innerHTML = '';
        if (chatHistory.length === 0) {
            messagesEl.innerHTML = `
                <div class="ca-empty">
                    <strong>Canvas Assistant</strong>
                    Ask me about upcoming assignments, deadlines, grading weights, and what to prioritize.
                </div>`;
            return;
        }
        for (const msg of chatHistory) {
            appendBubble(msg.role, msg.content, false);
        }
        scrollToBottom();
    }

    function linkify(text) {
        // Escape HTML entities first
        const escaped = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');

        // Markdown links: [label](url) → <a href="url">label</a>
        const mdLinked = escaped.replace(
            /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener" style="color:#0770A3;text-decoration:underline;">$1</a>'
        );

        // Bare URLs (not already inside an href="...") — stop before trailing punctuation
        const bareLinked = mdLinked.replace(
            /(?<!href=")(https?:\/\/[^\s<>"']+?)([.,;:!?)]*(?:\s|$))/g,
            '<a href="$1" target="_blank" rel="noopener" style="color:#0770A3;text-decoration:underline;word-break:break-all;">$1</a>$2'
        );

        // Preserve newlines as <br>
        return bareLinked.replace(/\n/g, '<br>');
    }

    function appendBubble(role, text, scroll = true) {
        const emptyEl = messagesEl.querySelector('.ca-empty');
        if (emptyEl) emptyEl.remove();

        const el = document.createElement('div');
        el.className = `ca-msg ca-${role}`;
        el.innerHTML = linkify(text);
        messagesEl.appendChild(el);
        if (scroll) scrollToBottom();
        return el;
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function saveHistory() {
        try {
            // Keep last 60 messages to avoid localStorage overflow
            localStorage.setItem(HISTORY_KEY, JSON.stringify(chatHistory.slice(-60)));
        } catch (e) { /* storage full — ignore */ }
    }

    // -------------------------------------------------------------------------
    // Send message
    // -------------------------------------------------------------------------
    async function sendMessage() {
        const text = inputEl.value.trim();
        if (!text || sendBtn.disabled) return;

        inputEl.value = '';
        inputEl.style.height = 'auto';
        sendBtn.disabled = true;

        chatHistory.push({ role: 'user', content: text });
        appendBubble('user', text);
        saveHistory();

        const thinkingEl = document.createElement('div');
        thinkingEl.className = 'ca-msg ca-thinking';
        thinkingEl.textContent = 'Thinking…';
        messagesEl.appendChild(thinkingEl);
        scrollToBottom();

        try {
            const data = await new Promise((resolve, reject) => {
                GM_xmlhttpRequest({
                    method: 'POST',
                    url: API_URL,
                    headers: { 'Content-Type': 'application/json' },
                    data: JSON.stringify({ message: text, session_id: sessionId }),
                    onload: (res) => {
                        if (res.status >= 400) {
                            reject(new Error(`Server returned ${res.status}`));
                        } else {
                            try { resolve(JSON.parse(res.responseText)); }
                            catch (e) { reject(new Error('Invalid JSON response')); }
                        }
                    },
                    onerror: () => reject(new Error('Network error — is the server running?')),
                });
            });

            sessionId = data.session_id;
            localStorage.setItem(SESSION_KEY, sessionId);

            thinkingEl.remove();
            chatHistory.push({ role: 'assistant', content: data.reply });
            appendBubble('assistant', data.reply);
            saveHistory();
        } catch (e) {
            thinkingEl.remove();
            appendBubble('assistant', `⚠️ Could not reach the server.\n\nMake sure it's running:\n  uvicorn web_server:app --port 8000\n\n(${e.message})`);
        }

        sendBtn.disabled = false;
        inputEl.focus();
    }

    // -------------------------------------------------------------------------
    // Manual data entry form
    // -------------------------------------------------------------------------
    const formPanel = panel.querySelector('#ca-form-panel');
    const formType = panel.querySelector('#ca-form-type');
    const formFields = panel.querySelector('#ca-form-fields');
    const formSubmit = panel.querySelector('#ca-form-submit');
    const formStatus = panel.querySelector('#ca-form-status');
    const editBtn = panel.querySelector('#ca-edit-btn');
    const inputArea = panel.querySelector('#ca-input-area');

    let formVisible = false;
    let cachedFormData = null;  // { courses: [{id, label}], assignments: [{id, label, course_id}] }

    function showFormStatus(msg, type) {
        formStatus.textContent = msg;
        formStatus.className = 'ca-status-' + type;
    }

    const CATEGORY_OPTIONS = `
        <option value="homework">Homework</option>
        <option value="project">Project</option>
        <option value="exam">Exam</option>
        <option value="quiz">Quiz</option>
        <option value="lab">Lab</option>
        <option value="participation">Participation</option>
        <option value="reading">Reading</option>
        <option value="discussion">Discussion</option>
        <option value="final">Final</option>
        <option value="midterm">Midterm</option>
        <option value="other">Other</option>`;

    function courseOptions() {
        if (!cachedFormData) return '<option value="">Loading…</option>';
        return cachedFormData.courses.map(c =>
            `<option value="${c.id}">${c.label}</option>`
        ).join('');
    }

    function assignmentOptions() {
        if (!cachedFormData) return '<option value="">Loading…</option>';
        return cachedFormData.assignments.map(a =>
            `<option value="${a.id}">${a.label}</option>`
        ).join('');
    }

    function renderFormFields(type) {
        formStatus.textContent = '';
        if (type === 'grading_weight') {
            formFields.innerHTML = `
                <div class="ca-form-group">
                    <span>Course</span>
                    <select class="ca-fi" id="ca-f-course-id">${courseOptions()}</select>
                </div>
                <div class="ca-form-group">
                    <span>Category</span>
                    <select class="ca-fi" id="ca-f-category">${CATEGORY_OPTIONS}</select>
                </div>
                <div class="ca-form-group">
                    <span>Weight %</span>
                    <input class="ca-fi" type="number" id="ca-f-weight" min="0" max="100" placeholder="e.g. 30">
                </div>`;
        } else if (type === 'late_policy') {
            formFields.innerHTML = `
                <div class="ca-form-group">
                    <span>Course</span>
                    <select class="ca-fi" id="ca-f-course-id">${courseOptions()}</select>
                </div>
                <div class="ca-form-group">
                    <label class="ca-fi-check">
                        <input type="checkbox" id="ca-f-allows-late" checked> Allows late submissions
                    </label>
                </div>
                <div class="ca-form-group">
                    <span>Penalty per day % (optional)</span>
                    <input class="ca-fi" type="number" id="ca-f-penalty" min="0" max="100" placeholder="e.g. 10">
                </div>
                <div class="ca-form-group">
                    <span>Max days late (optional)</span>
                    <input class="ca-fi" type="number" id="ca-f-max-days" min="0" placeholder="e.g. 5">
                </div>`;
        } else if (type === 'assignment_category') {
            formFields.innerHTML = `
                <div class="ca-form-group">
                    <span>Assignment</span>
                    <select class="ca-fi" id="ca-f-assignment-id">${assignmentOptions()}</select>
                </div>
                <div class="ca-form-group">
                    <span>Correct category</span>
                    <select class="ca-fi" id="ca-f-category">${CATEGORY_OPTIONS}</select>
                </div>`;
        } else {
            formFields.innerHTML = '';
        }
        formSubmit.style.display = type ? 'block' : 'none';
    }

    function submitForm() {
        const type = formType.value;
        if (!type) { showFormStatus('Select an update type.', 'error'); return; }

        let course_id = null;
        const data = {};

        if (type === 'grading_weight') {
            course_id = parseInt(panel.querySelector('#ca-f-course-id').value);
            data.category = panel.querySelector('#ca-f-category').value;
            data.weight_pct = parseFloat(panel.querySelector('#ca-f-weight').value);
            if (!course_id || !data.category || isNaN(data.weight_pct)) {
                showFormStatus('All fields are required.', 'error'); return;
            }
        } else if (type === 'late_policy') {
            course_id = parseInt(panel.querySelector('#ca-f-course-id').value);
            data.allows_late = panel.querySelector('#ca-f-allows-late').checked;
            const p = panel.querySelector('#ca-f-penalty').value;
            const m = panel.querySelector('#ca-f-max-days').value;
            if (p) data.penalty_per_day = parseFloat(p);
            if (m) data.max_days_late = parseInt(m);
            if (!course_id) { showFormStatus('Course ID is required.', 'error'); return; }
        } else if (type === 'assignment_category') {
            data.assignment_id = parseInt(panel.querySelector('#ca-f-assignment-id').value);
            data.category = panel.querySelector('#ca-f-category').value;
            if (!data.assignment_id || !data.category) {
                showFormStatus('All fields are required.', 'error'); return;
            }
        }

        formSubmit.disabled = true;
        showFormStatus('Saving…', 'info');

        GM_xmlhttpRequest({
            method: 'POST',
            url: MANUAL_UPDATE_URL,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({ update_type: type, course_id, data }),
            onload: (res) => {
                formSubmit.disabled = false;
                if (res.status < 400) {
                    showFormStatus('✓ Saved!', 'success');
                    setTimeout(() => {
                        formType.value = '';
                        formFields.innerHTML = '';
                        formSubmit.style.display = 'none';
                        formStatus.textContent = '';
                    }, 2000);
                } else {
                    try {
                        const body = JSON.parse(res.responseText);
                        showFormStatus('Error: ' + (body.error || res.status), 'error');
                    } catch (e) {
                        showFormStatus('Error: ' + res.status, 'error');
                    }
                }
            },
            onerror: () => {
                formSubmit.disabled = false;
                showFormStatus('Network error — is the server running?', 'error');
            },
        });
    }

    function toggleForm() {
        formVisible = !formVisible;
        if (formVisible) {
            messagesEl.style.display = 'none';
            inputArea.style.display = 'none';
            formPanel.classList.add('ca-form-visible');
            editBtn.style.color = 'white';
            // Fetch course/assignment lists once, then re-render the current type
            if (!cachedFormData) {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: FORM_DATA_URL,
                    onload: (res) => {
                        try {
                            cachedFormData = JSON.parse(res.responseText);
                        } catch (e) {
                            cachedFormData = { courses: [], assignments: [] };
                        }
                        if (formType.value) renderFormFields(formType.value);
                    },
                    onerror: () => {
                        cachedFormData = { courses: [], assignments: [] };
                    },
                });
            }
        } else {
            messagesEl.style.display = '';
            inputArea.style.display = '';
            formPanel.classList.remove('ca-form-visible');
            editBtn.style.color = '';
        }
    }

    formType.addEventListener('change', () => renderFormFields(formType.value));
    formSubmit.addEventListener('click', submitForm);
    editBtn.addEventListener('click', toggleForm);

    // -------------------------------------------------------------------------
    // Event listeners
    // -------------------------------------------------------------------------
    btn.addEventListener('click', () => {
        const isHidden = panel.classList.toggle('ca-hidden');
        if (!isHidden) {
            renderHistory();
            inputEl.focus();
        }
    });

    sendBtn.addEventListener('click', sendMessage);

    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    inputEl.addEventListener('input', () => {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + 'px';
    });

    clearBtn.addEventListener('click', () => {
        chatHistory = [];
        sessionId = null;
        localStorage.removeItem(SESSION_KEY);
        localStorage.removeItem(HISTORY_KEY);
        renderHistory();
    });

})();
