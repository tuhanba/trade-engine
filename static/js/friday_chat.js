/* static/js/friday_chat.js — Friday Live Chat Widget Logic */

document.addEventListener("DOMContentLoaded", () => {
    const chatBtn = document.getElementById("fridayChatBtn");
    const chatContainer = document.getElementById("fridayChatContainer");
    const chatClose = document.getElementById("fridayChatClose");
    const chatMessages = document.getElementById("fridayChatMessages");
    const chatInput = document.getElementById("fridayChatInput");
    const chatSend = document.getElementById("fridayChatSend");
    const autoplayToggle = document.getElementById("fridayAutoplay");

    let isTyping = false;
    let currentAudio = null;

    // Toggle Chat Panel
    chatBtn.addEventListener("click", () => {
        chatContainer.classList.toggle("active");
        if (chatContainer.classList.contains("active")) {
            chatInput.focus();
            // Scroll to bottom
            scrollToBottom();
        }
    });

    chatClose.addEventListener("click", () => {
        chatContainer.classList.remove("active");
    });

    // Send message triggers
    chatSend.addEventListener("click", sendMessage);
    chatInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            sendMessage();
        }
    });

    function sendMessage() {
        const text = chatInput.value.trim();
        if (!text || isTyping) return;

        // Clear input
        chatInput.value = "";

        // Add user bubble
        appendMessage("user", text);

        // Add typing indicator
        showTypingIndicator();

        // Fetch Friday reply
        fetch("/api/friday/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ message: text })
        })
        .then(res => res.json())
        .then(data => {
            hideTypingIndicator();
            if (data.ok) {
                appendMessage("friday", data.reply, data.voice);
                if (data.voice && autoplayToggle.checked) {
                    playVoice(data.voice);
                }
            } else {
                appendMessage("friday", `❌ Hata: ${data.error || "Bilinmeyen bir hata oluştu."}`);
            }
        })
        .catch(err => {
            hideTypingIndicator();
            appendMessage("friday", `❌ Bağlantı hatası: Sunucuya erişilemedi.`);
            console.error("Friday chat error:", err);
        });
    }

    function appendMessage(sender, text, voiceBase64 = null) {
        const msgRow = document.createElement("div");
        msgRow.className = `friday-msg-row ${sender} friday-animate-msg`;

        const bubble = document.createElement("div");
        bubble.className = "friday-msg-bubble";
        
        // Format text with code markup / line breaks
        bubble.innerHTML = formatText(text);

        const meta = document.createElement("div");
        meta.className = "friday-msg-meta";
        const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        meta.textContent = timeStr;

        if (sender === "friday" && voiceBase64) {
            // Add a speaker button next to timestamp to replay audio
            const voiceBtn = document.createElement("button");
            voiceBtn.className = "friday-voice-btn";
            voiceBtn.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
            voiceBtn.title = "Ses kaydını oynat";
            voiceBtn.addEventListener("click", () => {
                playVoice(voiceBase64);
            });
            meta.appendChild(voiceBtn);
        }

        msgRow.appendChild(bubble);
        msgRow.appendChild(meta);
        chatMessages.appendChild(msgRow);
        scrollToBottom();
    }

    function showTypingIndicator() {
        isTyping = true;
        const typingRow = document.createElement("div");
        typingRow.className = "friday-msg-row friday friday-animate-msg";
        typingRow.id = "fridayTypingIndicator";

        const bubble = document.createElement("div");
        bubble.className = "friday-msg-bubble friday-typing";
        bubble.innerHTML = "<span></span><span></span><span></span>";

        typingRow.appendChild(bubble);
        chatMessages.appendChild(typingRow);
        scrollToBottom();
    }

    function hideTypingIndicator() {
        isTyping = false;
        const typingIndicator = document.getElementById("fridayTypingIndicator");
        if (typingIndicator) {
            typingIndicator.remove();
        }
    }

    function playVoice(base64Data) {
        try {
            if (currentAudio) {
                currentAudio.pause();
            }
            currentAudio = new Audio("data:audio/mp3;base64," + base64Data);
            currentAudio.play().catch(e => {
                console.warn("Otomatik oynatma tarayıcı engeline takılmış olabilir:", e);
            });
        } catch (e) {
            console.error("Audio playback failed:", e);
        }
    }

    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function formatText(text) {
        // Safe HTML escape first
        let escaped = text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");

        // Format code blocks ```code```
        escaped = escaped.replace(/```json\s*([\s\S]*?)\s*```/g, '<pre style="background: rgba(0,0,0,0.4); padding: 8px; border-radius: 6px; overflow-x: auto; margin: 6px 0; border: 1px solid rgba(255,255,255,0.05); font-family: monospace; font-size: 11px;">$1</pre>');
        escaped = escaped.replace(/```\s*([\s\S]*?)\s*```/g, '<pre style="background: rgba(0,0,0,0.4); padding: 8px; border-radius: 6px; overflow-x: auto; margin: 6px 0; border: 1px solid rgba(255,255,255,0.05); font-family: monospace; font-size: 11px;">$1</pre>');

        // Format inline code `code`
        escaped = escaped.replace(/`([^`]+)`/g, '<code style="background: rgba(255,255,255,0.08); padding: 2px 4px; border-radius: 4px; font-family: monospace;">$1</code>');

        // Bold tags
        escaped = escaped.replace(/&lt;b&gt;([\s\S]*?)&lt;\/b&gt;/g, "<strong>$1</strong>");
        
        // Convert double linebreaks to spacing
        return escaped.replace(/\n/g, "<br>");
    }
});
