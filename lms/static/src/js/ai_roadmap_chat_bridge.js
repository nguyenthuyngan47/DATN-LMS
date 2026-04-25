/** @odoo-module **/

// AI roadmap chat bridge:
// - Enter-to-send in the chat input.
// - Optional F12 console debug (toggle by localStorage flag).
(() => {
    if (window.__lmsAiRoadmapChatBridgePatched) {
        return;
    }
    window.__lmsAiRoadmapChatBridgePatched = true;

    const DEBUG_FLAG_KEY = "lms_ai_roadmap_console_debug";
    const isDebugEnabled = () => window.localStorage.getItem(DEBUG_FLAG_KEY) === "1";
    const log = (...args) => {
        if (isDebugEnabled()) {
            console.log(...args);
        }
    };

    if (isDebugEnabled()) {
        console.info(
            `[LMS AI ROADMAP] Console debug is ON (${DEBUG_FLAG_KEY}=1). Set to 0 to disable.`
        );
    }

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
        const [input, init] = args;
        const url = (typeof input === "string" ? input : input?.url) || "";
        const isTarget = url.includes("/web/dataset/call_kw/lms.student.ai.chat/");

        if (isTarget) {
            try {
                const body = init?.body ? JSON.parse(init.body) : null;
                log("[LMS AI ROADMAP] RPC Request:", body);
            } catch (_e) {
                log("[LMS AI ROADMAP] RPC Request: (unparsed)");
            }
        }

        const response = await originalFetch(...args);
        if (!isTarget) {
            return response;
        }

        try {
            const cloned = response.clone();
            const data = await cloned.json();
            log("[LMS AI ROADMAP] RPC Response:", data);

            const result = data?.result;
            const rec = Array.isArray(result) ? result[0] : null;
            if (rec?.debug_last_ai_request || rec?.debug_last_ai_response) {
                log("[LMS AI ROADMAP] AI Request (server):", rec.debug_last_ai_request || "");
                log("[LMS AI ROADMAP] AI Response (server):", rec.debug_last_ai_response || "");
            }
        } catch (_e) {
            log("[LMS AI ROADMAP] RPC Response: (non-json)");
        }

        return response;
    };

    document.addEventListener("keydown", (ev) => {
        if (ev.key !== "Enter" || ev.shiftKey) {
            return;
        }
        const target = ev.target;
        if (!target) {
            return;
        }

        const isMessageInput =
            target.matches?.("textarea[name='user_message'], input[name='user_message']") ||
            target.getAttribute?.("name") === "user_message";
        if (!isMessageInput) {
            return;
        }

        const formRoot = target.closest?.(".o_form_view");
        if (!formRoot) {
            return;
        }
        const sendButton = formRoot.querySelector("button[name='action_send_message']:not([disabled])");
        if (!sendButton || sendButton.offsetParent === null) {
            return;
        }

        ev.preventDefault();
        sendButton.click();
    });
})();
