document.addEventListener('DOMContentLoaded', () => {
    // ─── Elements ──────────────────────────────────────────
    const uploadView = document.getElementById('upload-view');
    const workspaceView = document.getElementById('workspace-view');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const uploadBtn = document.getElementById('upload-btn');
    const loadingSpinner = document.getElementById('loading-spinner');
    const loadingText = document.getElementById('loading-text');
    const errorMessage = document.getElementById('error-message');

    const docName = document.getElementById('doc-name');
    const docMeta = document.getElementById('doc-meta');
    const markdownPane = document.getElementById('markdown-pane');
    const markdownContent = document.getElementById('markdown-content');
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const newDocBtn = document.getElementById('new-doc-btn');
    const toggleExtractBtn = document.getElementById('toggle-extract-btn');
    const collapseMdBtn = document.getElementById('collapse-md-btn');
    const resizer = document.getElementById('resizer');
    const workspaceBody = document.getElementById('workspace-body');
    const turnCounter = document.getElementById('turn-counter');

    let selectedFile = null;
    let currentSessionId = null;
    let isProcessing = false;
    let mdPaneVisible = true;
    let turnCount = 0;
    let isCached = false;

    // Configure marked
    marked.setOptions({ breaks: true, gfm: true });

    // ─── Drag & Drop ───────────────────────────────────────
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, e => { e.preventDefault(); e.stopPropagation(); }, false);
    });
    ['dragenter', 'dragover'].forEach(evt => {
        dropZone.addEventListener(evt, () => dropZone.classList.add('dragover'), false);
    });
    ['dragleave', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, () => dropZone.classList.remove('dragover'), false);
    });
    dropZone.addEventListener('drop', e => handleFiles(e.dataTransfer.files));
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', function () { handleFiles(this.files); });

    function handleFiles(files) {
        if (files.length === 0) return;
        const file = files[0];
        if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
            selectedFile = file;
            const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
            dropZone.querySelector('p').innerHTML =
                `<strong>${file.name}</strong><span class="file-size">${sizeMB} MB</span>`;
            uploadBtn.disabled = false;
            errorMessage.textContent = '';
        } else {
            errorMessage.textContent = 'Please select a valid PDF file.';
            selectedFile = null;
            uploadBtn.disabled = true;
        }
    }

    // ─── Upload ────────────────────────────────────────────
    uploadBtn.addEventListener('click', async () => {
        if (!selectedFile || isProcessing) return;
        isProcessing = true;

        const formData = new FormData();
        formData.append('pdf', selectedFile);

        uploadBtn.style.display = 'none';
        loadingSpinner.style.display = 'block';
        loadingText.textContent = 'Parsing PDF and extracting structured content...';
        errorMessage.textContent = '';

        try {
            const response = await fetch('/upload', { method: 'POST', body: formData });
            const data = await response.json();

            if (!response.ok) throw new Error(data.error || 'Processing failed.');

            currentSessionId = data.session_id;
            turnCount = 1;

            // Switch views
            uploadView.style.display = 'none';
            workspaceView.style.display = 'flex';

            // Populate
            docName.textContent = selectedFile.name;
            isCached = data.cached || false;
            const cacheTag = isCached ? ' · Cached' : '';
            if (data.truncated) {
                docMeta.textContent = `(Partial: ${(data.loaded_chars / 1000).toFixed(0)}K of ${(data.doc_chars / 1000).toFixed(0)}K chars loaded${cacheTag})`;
            } else {
                docMeta.textContent = `(${(data.doc_chars / 1000).toFixed(0)}K chars${cacheTag})`;
            }

            markdownContent.innerHTML = marked.parse(data.markdown || 'No content extracted.');
            chatMessages.innerHTML = '';
            appendMessage('ai', data.ai_response);
            updateTurnCounter();

            // Enable chat
            chatInput.disabled = false;
            sendBtn.disabled = false;
            chatInput.focus();

            // Show markdown pane
            showMarkdownPane();

        } catch (error) {
            errorMessage.textContent = error.message;
            console.error(error);
        } finally {
            loadingSpinner.style.display = 'none';
            uploadBtn.style.display = 'block';
            isProcessing = false;
        }
    });

    // ─── Chat ──────────────────────────────────────────────
    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + 'px';
        sendBtn.disabled = chatInput.value.trim() === '';
    });

    async function sendMessage() {
        const message = chatInput.value.trim();
        if (!message || !currentSessionId || isProcessing) return;
        isProcessing = true;

        appendMessage('user', message);
        chatInput.value = '';
        chatInput.style.height = 'auto';
        sendBtn.disabled = true;

        const typingEl = appendTypingIndicator();

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: currentSessionId, message })
            });
            const data = await response.json();
            typingEl.remove();

            if (!response.ok) throw new Error(data.error || 'Failed to get a response.');

            appendMessage('ai', data.ai_response);
            turnCount = data.history_turns || turnCount + 1;
            if (data.cached !== undefined) isCached = data.cached;
            updateTurnCounter();

        } catch (error) {
            typingEl.remove();
            appendMessage('error', error.message);
            console.error(error);
        } finally {
            isProcessing = false;
            sendBtn.disabled = chatInput.value.trim() === '';
            chatInput.focus();
        }
    }

    function appendMessage(role, content) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('chat-msg', `chat-msg-${role}`);

        const header = document.createElement('div');
        header.classList.add('msg-header');

        const label = document.createElement('span');
        label.classList.add('msg-label');
        label.textContent = role === 'user' ? 'You' : role === 'ai' ? 'AI' : 'Error';
        header.appendChild(label);

        // Copy button for AI messages
        if (role === 'ai') {
            const copyBtn = document.createElement('button');
            copyBtn.classList.add('btn-copy');
            copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            copyBtn.title = 'Copy to clipboard';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(content).then(() => {
                    copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
                    setTimeout(() => {
                        copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
                    }, 2000);
                });
            });
            header.appendChild(copyBtn);
        }

        const body = document.createElement('div');
        body.classList.add('msg-body');
        if (role === 'error') {
            body.textContent = content;
        } else if (role === 'ai') {
            body.innerHTML = marked.parse(content);
        } else {
            body.textContent = content;
        }

        wrapper.appendChild(header);
        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function appendTypingIndicator() {
        const wrapper = document.createElement('div');
        wrapper.classList.add('chat-msg', 'chat-msg-ai', 'typing-indicator');
        const header = document.createElement('div');
        header.classList.add('msg-header');
        const label = document.createElement('span');
        label.classList.add('msg-label');
        label.textContent = 'AI';
        header.appendChild(label);
        const body = document.createElement('div');
        body.classList.add('msg-body');
        body.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
        wrapper.appendChild(header);
        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return wrapper;
    }

    function updateTurnCounter() {
        turnCounter.textContent = `${turnCount} turn${turnCount !== 1 ? 's' : ''}`;
    }

    // ─── Panel Toggle / Collapse ───────────────────────────
    function hideMarkdownPane() {
        mdPaneVisible = false;
        markdownPane.classList.add('collapsed');
        resizer.classList.add('hidden');
        workspaceBody.classList.add('chat-only');
        toggleExtractBtn.classList.add('active');
    }

    function showMarkdownPane() {
        mdPaneVisible = true;
        markdownPane.classList.remove('collapsed');
        resizer.classList.remove('hidden');
        workspaceBody.classList.remove('chat-only');
        toggleExtractBtn.classList.remove('active');
        // Reset any custom widths
        markdownPane.style.width = '';
    }

    function toggleMarkdownPane() {
        if (mdPaneVisible) hideMarkdownPane();
        else showMarkdownPane();
    }

    toggleExtractBtn.addEventListener('click', toggleMarkdownPane);
    collapseMdBtn.addEventListener('click', hideMarkdownPane);

    // ─── Resizable Splitter ────────────────────────────────
    let isResizing = false;

    resizer.addEventListener('mousedown', (e) => {
        isResizing = true;
        document.body.classList.add('resizing');
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const rect = workspaceBody.getBoundingClientRect();
        const offset = e.clientX - rect.left;
        const totalWidth = rect.width;
        const pct = (offset / totalWidth) * 100;

        // Clamp between 20% and 70%
        const clamped = Math.max(20, Math.min(70, pct));
        markdownPane.style.width = clamped + '%';
        markdownPane.style.flex = 'none';
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.classList.remove('resizing');
        }
    });

    // Touch support for mobile resizing
    resizer.addEventListener('touchstart', (e) => {
        isResizing = true;
        document.body.classList.add('resizing');
        e.preventDefault();
    }, { passive: false });

    document.addEventListener('touchmove', (e) => {
        if (!isResizing) return;
        const touch = e.touches[0];
        const rect = workspaceBody.getBoundingClientRect();
        const offset = touch.clientX - rect.left;
        const pct = (offset / rect.width) * 100;
        const clamped = Math.max(20, Math.min(70, pct));
        markdownPane.style.width = clamped + '%';
        markdownPane.style.flex = 'none';
    }, { passive: false });

    document.addEventListener('touchend', () => {
        if (isResizing) {
            isResizing = false;
            document.body.classList.remove('resizing');
        }
    });

    // ─── Keyboard Shortcuts ────────────────────────────────
    document.addEventListener('keydown', (e) => {
        // Ctrl+B: Toggle extracted content
        if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
            e.preventDefault();
            toggleMarkdownPane();
        }
        // Escape: Focus chat input
        if (e.key === 'Escape' && currentSessionId) {
            chatInput.focus();
        }
    });

    // ─── New Document ──────────────────────────────────────
    newDocBtn.addEventListener('click', async () => {
        if (currentSessionId) {
            try {
                await fetch('/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSessionId })
                });
            } catch (e) { /* ignore */ }
        }

        currentSessionId = null;
        selectedFile = null;
        turnCount = 0;
        isCached = false;

        dropZone.querySelector('p').innerHTML = 'Drag & Drop your PDF here, or <span class="browse-link">browse</span>';
        uploadBtn.disabled = true;
        errorMessage.textContent = '';

        workspaceView.style.display = 'none';
        uploadView.style.display = 'flex';

        markdownContent.innerHTML = '';
        chatMessages.innerHTML = '';
        chatInput.value = '';
        chatInput.disabled = true;
        sendBtn.disabled = true;

        showMarkdownPane();
        markdownPane.style.width = '';
        markdownPane.style.flex = '';
    });
});
