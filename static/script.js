document.addEventListener('DOMContentLoaded', () => {
    // Elements - Upload view
    const uploadView = document.getElementById('upload-view');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const uploadBtn = document.getElementById('upload-btn');
    const loadingSpinner = document.getElementById('loading-spinner');
    const errorMessage = document.getElementById('error-message');

    // Elements - Workspace view
    const workspaceView = document.getElementById('workspace-view');
    const docName = document.getElementById('doc-name');
    const markdownContent = document.getElementById('markdown-content');
    const chatMessages = document.getElementById('chat-messages');
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const newDocBtn = document.getElementById('new-doc-btn');

    let selectedFile = null;
    let currentSessionId = null;
    let isProcessing = false;

    // Configure marked
    marked.setOptions({ breaks: true, gfm: true });

    // ─── Drag & Drop ───────────────────────────────────────────
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
            dropZone.querySelector('p').innerHTML =
                `Selected: <strong>${file.name}</strong> (${(file.size / 1024).toFixed(0)} KB)`;
            uploadBtn.disabled = false;
            errorMessage.textContent = '';
        } else {
            errorMessage.textContent = 'Please select a valid PDF file.';
            selectedFile = null;
            uploadBtn.disabled = true;
        }
    }

    // ─── Upload ────────────────────────────────────────────────
    uploadBtn.addEventListener('click', async () => {
        if (!selectedFile || isProcessing) return;
        isProcessing = true;

        const formData = new FormData();
        formData.append('pdf', selectedFile);

        uploadBtn.style.display = 'none';
        loadingSpinner.style.display = 'block';
        errorMessage.textContent = '';

        try {
            const response = await fetch('/upload', { method: 'POST', body: formData });
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || 'Something went wrong processing the file.');
            }

            currentSessionId = data.session_id;

            // Switch to workspace view
            uploadView.style.display = 'none';
            workspaceView.style.display = 'flex';

            // Populate
            docName.textContent = selectedFile.name;
            markdownContent.innerHTML = marked.parse(data.markdown || 'No content extracted.');

            // Clear chat and show initial AI response
            chatMessages.innerHTML = '';
            appendMessage('ai', data.ai_response);

            // Enable chat
            chatInput.disabled = false;
            sendBtn.disabled = false;

        } catch (error) {
            errorMessage.textContent = error.message;
            console.error(error);
        } finally {
            loadingSpinner.style.display = 'none';
            uploadBtn.style.display = 'block';
            isProcessing = false;
        }
    });

    // ─── Chat ──────────────────────────────────────────────────
    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + 'px';
        sendBtn.disabled = chatInput.value.trim() === '';
    });

    async function sendMessage() {
        const message = chatInput.value.trim();
        if (!message || !currentSessionId || isProcessing) return;
        isProcessing = true;

        // Show user message
        appendMessage('user', message);
        chatInput.value = '';
        chatInput.style.height = 'auto';
        sendBtn.disabled = true;

        // Show typing indicator
        const typingEl = appendTypingIndicator();

        try {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: currentSessionId, message })
            });
            const data = await response.json();

            // Remove typing indicator
            typingEl.remove();

            if (!response.ok) {
                throw new Error(data.error || 'Failed to get a response.');
            }

            appendMessage('ai', data.ai_response);

        } catch (error) {
            typingEl.remove();
            appendMessage('error', error.message);
            console.error(error);
        } finally {
            isProcessing = false;
            sendBtn.disabled = chatInput.value.trim() === '';
        }
    }

    function appendMessage(role, content) {
        const wrapper = document.createElement('div');
        wrapper.classList.add('chat-msg', `chat-msg-${role}`);

        const label = document.createElement('div');
        label.classList.add('msg-label');
        label.textContent = role === 'user' ? 'You' : role === 'ai' ? 'AI' : 'Error';

        const body = document.createElement('div');
        body.classList.add('msg-body');
        if (role === 'error') {
            body.textContent = content;
        } else if (role === 'ai') {
            body.innerHTML = marked.parse(content);
        } else {
            body.textContent = content;
        }

        wrapper.appendChild(label);
        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);

        // Scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function appendTypingIndicator() {
        const wrapper = document.createElement('div');
        wrapper.classList.add('chat-msg', 'chat-msg-ai', 'typing-indicator');

        const label = document.createElement('div');
        label.classList.add('msg-label');
        label.textContent = 'AI';

        const body = document.createElement('div');
        body.classList.add('msg-body');
        body.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';

        wrapper.appendChild(label);
        wrapper.appendChild(body);
        chatMessages.appendChild(wrapper);
        chatMessages.scrollTop = chatMessages.scrollHeight;
        return wrapper;
    }

    // ─── New Document ──────────────────────────────────────────
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

        // Reset upload view
        dropZone.querySelector('p').innerHTML = 'Drag & Drop your PDF here, or <span class="browse-link">browse</span>';
        uploadBtn.disabled = true;
        errorMessage.textContent = '';

        // Switch views
        workspaceView.style.display = 'none';
        uploadView.style.display = 'block';

        // Clear workspace
        markdownContent.innerHTML = '';
        chatMessages.innerHTML = '';
        chatInput.value = '';
        chatInput.disabled = true;
        sendBtn.disabled = true;
    });
});
