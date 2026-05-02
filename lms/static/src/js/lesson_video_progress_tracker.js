/** @odoo-module **/

// Track lesson video progress while video is playing.
(() => {
    if (window.__lmsLessonVideoProgressTrackerPatched) {
        return;
    }
    window.__lmsLessonVideoProgressTrackerPatched = true;

    const tracked = new Map();

    const getTrackerState = (videoEl) => {
        if (!tracked.has(videoEl)) {
            tracked.set(videoEl, {
                timerId: null,
                lastSentSecond: 0,
            });
        }
        return tracked.get(videoEl);
    };

    const parseLessonId = (videoEl) => {
        const raw = videoEl?.dataset?.lmsLessonId;
        const lessonId = Number.parseInt(raw || "", 10);
        return Number.isFinite(lessonId) && lessonId > 0 ? lessonId : null;
    };

    const callProgressRpc = async (videoEl) => {
        const lessonId = parseLessonId(videoEl);
        if (!lessonId) {
            return;
        }
        const state = getTrackerState(videoEl);
        const currentSecond = Math.max(0, Math.floor(videoEl.currentTime || 0));
        if (currentSecond <= state.lastSentSecond && !videoEl.ended && !videoEl.paused) {
            return;
        }
        state.lastSentSecond = Math.max(state.lastSentSecond, currentSecond);
        const payload = {
            id: Date.now(),
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "lms.lesson",
                method: "action_update_current_user_progress",
                args: [[lessonId], currentSecond, currentSecond, Math.max(0, Math.floor(videoEl.duration || 0))],
                kwargs: {},
            },
        };
        try {
            await fetch("/web/dataset/call_kw/lms.lesson/action_update_current_user_progress", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify(payload),
            });
        } catch (_e) {
            // Silent fail: tracking should never block playback.
        }
    };

    const stopTracking = (videoEl) => {
        const state = getTrackerState(videoEl);
        if (state.timerId) {
            clearInterval(state.timerId);
            state.timerId = null;
        }
    };

    const startTracking = (videoEl) => {
        const lessonId = parseLessonId(videoEl);
        if (!lessonId) {
            return;
        }
        const state = getTrackerState(videoEl);
        if (state.timerId) {
            return;
        }
        callProgressRpc(videoEl);
        state.timerId = setInterval(() => {
            if (videoEl.paused || videoEl.ended) {
                stopTracking(videoEl);
                return;
            }
            callProgressRpc(videoEl);
        }, 5000);
    };

    document.addEventListener(
        "play",
        (ev) => {
            const target = ev.target;
            if (!(target instanceof HTMLVideoElement)) {
                return;
            }
            if (!target.classList.contains("lms-video-tracker")) {
                return;
            }
            startTracking(target);
        },
        true
    );

    const stopEvents = ["pause", "ended", "abort", "emptied"];
    for (const eventName of stopEvents) {
        document.addEventListener(
            eventName,
            (ev) => {
                const target = ev.target;
                if (!(target instanceof HTMLVideoElement) || !target.classList.contains("lms-video-tracker")) {
                    return;
                }
                callProgressRpc(target);
                stopTracking(target);
            },
            true
        );
    }
})();
