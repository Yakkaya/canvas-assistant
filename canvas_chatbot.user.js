// ==UserScript==
// @name         Canvas Assistant
// @namespace    http://tampermonkey.net/
// @version      1.0
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
            <button id="ca-clear-btn" title="Clear conversation">Clear</button>
        </div>
        <div id="ca-messages"></div>
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

    function appendBubble(role, text, scroll = true) {
        const emptyEl = messagesEl.querySelector('.ca-empty');
        if (emptyEl) emptyEl.remove();

        const el = document.createElement('div');
        el.className = `ca-msg ca-${role}`;
        el.textContent = text;
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
