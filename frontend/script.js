const BASE_URL = 'http://127.0.0.1:8000/api/v1';
let sessionId = null;
let isWaitingForResponse = false;

// DOM Elements
const chatBox = document.getElementById('chat-box');
const userInput = document.getElementById('user-input');
const sendBtn = document.getElementById('send-btn');
const startBtn = document.getElementById('start-btn');
const statusIndicator = document.getElementById('status-indicator');

// Event Listeners
if (startBtn) startBtn.addEventListener('click', startInterview);
if (sendBtn) sendBtn.addEventListener('click', sendMessage);
if (userInput) {
    userInput.addEventListener('keypress', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    userInput.addEventListener('input', function() {
        this.style.height = '52px';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
}

// Main Functions
async function startInterview() {
    if (startBtn) {
        startBtn.disabled = true;
        startBtn.textContent = 'Starting...';
    }
    clearChat();
    
    try {
        const response = await fetch(`${BASE_URL}/sessions`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                candidate_id: "test_user",
                job_id: "backend_role"
            })
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => null);
            console.error("Backend Error:", errData);
            throw new Error((errData && errData.detail) || 'Failed to start session');
        }
        
        const data = await response.json();
        
        if (!data || !data.session_id) {
            throw new Error('Invalid response from server: Missing session_id');
        }
        
        sessionId = data.session_id;
        
        if (startBtn) startBtn.textContent = 'Interview Active';
        if (statusIndicator) {
            statusIndicator.textContent = 'Online';
            statusIndicator.classList.add('online');
        }
        
        const questionText = data.question || data.next_question || data.message;
        if (questionText) {
            appendMessage(questionText, 'ai');
        } else {
            appendMessage("Interview started. Ready for your response.", 'system');
        }
        
        enableInput();

    } catch (error) {
        console.error('Error starting interview:', error);
        appendMessage(`Error: ${error.message || 'Could not connect to the backend API.'}`, 'error');
        if (startBtn) {
            startBtn.disabled = false;
            startBtn.textContent = 'Start Interview';
        }
        if (statusIndicator) {
            statusIndicator.textContent = 'Offline';
            statusIndicator.classList.remove('online');
        }
    }
}

async function sendMessage() {
    if (!userInput || !sessionId || isWaitingForResponse) return;
    
    const text = userInput.value.trim();
    if (!text) return;

    appendMessage(text, 'user');
    
    userInput.value = '';
    userInput.style.height = '52px';
    disableInput();

    const loadingId = showLoading();

    try {
        const response = await fetch(`${BASE_URL}/sessions/${sessionId}/answer`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ answer: text })
        });

        removeMessage(loadingId);

        if (!response.ok) {
             const errData = await response.json().catch(() => null);
             console.error("Backend Error:", errData);
             throw new Error((errData && errData.detail) || 'Failed to send answer');
        }

        const data = await response.json();
        
        if (!data) throw new Error("Empty response from server");

        if (data.feedback || data.score !== undefined) {
            let feedbackText = '';
            if (data.score !== undefined) feedbackText += `Score: ${data.score} | `;
            if (data.feedback) feedbackText += data.feedback;
            if (feedbackText) appendMessage(feedbackText.trim(), 'system', 'feedback-msg');
        }

        const nextQuestion = data.question || data.next_question || data.message;
        if (nextQuestion) {
             appendMessage(nextQuestion, 'ai');
        }

        enableInput();

    } catch (error) {
        console.error('Error sending message:', error);
        removeMessage(loadingId);
        appendMessage(`Error: ${error.message || 'Failed to communicate with the server.'}`, 'error');
        enableInput();
    }
}

// UI Helper Functions
function appendMessage(text, sender, extraClass = '') {
    if (!chatBox) return null;
    
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message');
    
    switch (sender) {
        case 'ai':
            msgDiv.classList.add('ai-msg');
            break;
        case 'user':
            msgDiv.classList.add('user-msg');
            break;
        case 'system':
            msgDiv.classList.add('system-msg');
            break;
        case 'error':
            msgDiv.classList.add('error-msg');
            break;
    }
    
    if (extraClass) {
        msgDiv.classList.add(extraClass);
    }
    
    // Safely stringify objects to avoid rendering [object Object]
    if (typeof text === 'object') {
        text = JSON.stringify(text, null, 2);
    }
    
    msgDiv.textContent = text;
    chatBox.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
}

function showLoading() {
    if (!chatBox) return '';
    
    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message', 'ai-msg');
    msgDiv.id = 'loading-' + Date.now();
    
    msgDiv.innerHTML = `
        <div class="typing-indicator">
            <div class="dot"></div>
            <div class="dot"></div>
            <div class="dot"></div>
        </div>
    `;
    
    chatBox.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv.id;
}

function removeMessage(id) {
    if (!id) return;
    const msgDiv = document.getElementById(id);
    if (msgDiv) {
        msgDiv.remove();
    }
}

function clearChat() {
    if (chatBox) chatBox.innerHTML = '';
}

function scrollToBottom() {
    if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;
}

function disableInput() {
    isWaitingForResponse = true;
    if (userInput) userInput.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
}

function enableInput() {
    isWaitingForResponse = false;
    if (userInput) {
        userInput.disabled = false;
        userInput.focus();
    }
    if (sendBtn) sendBtn.disabled = false;
}
