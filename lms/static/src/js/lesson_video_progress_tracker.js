// Track lesson video progress + study session from lesson detail open.
(() => {
    if (window.__lmsLessonVideoProgressTrackerPatched) {
        return;
    }
    window.__lmsLessonVideoProgressTrackerPatched = true;

    const tracked = new Map();
    let activeLessonSessionId = null;
    let sessionPingTimer = null;

    const parseLessonIdFromLocation = () => {
        const hash = window.location.hash || "";
        if (!hash.includes("model=lms.lesson")) {
            return null;
        }
        const match = hash.match(/(?:[?&;]|#.*[?&;])id=(\d+)/) || hash.match(/\bid=(\d+)/);
        const lessonId = Number.parseInt(match?.[1] || "", 10);
        return Number.isFinite(lessonId) && lessonId > 0 ? lessonId : null;
    };

    const callLessonKw = async (method, lessonId) => {
        const payload = {
            id: Date.now(),
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "lms.lesson",
                method,
                args: [[lessonId]],
                kwargs: {},
            },
        };
        try {
            await fetch(`/web/dataset/call_kw/lms.lesson/${method}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "same-origin",
                body: JSON.stringify(payload),
            });
        } catch (_e) {
            // Silent fail: must not block the lesson UI.
        }
    };

    const stopSessionPing = () => {
        if (sessionPingTimer) {
            clearInterval(sessionPingTimer);
            sessionPingTimer = null;
        }
    };

    const startSessionPing = (lessonId) => {
        stopSessionPing();
        sessionPingTimer = setInterval(() => {
            if (parseLessonIdFromLocation() === lessonId) {
                callLessonKw("action_ping_lesson_session", lessonId);
            } else {
                stopSessionPing();
            }
        }, 30000);
    };

    const syncLessonSessionFromUrl = () => {
        const lessonId = parseLessonIdFromLocation();
        if (!lessonId) {
            activeLessonSessionId = null;
            stopSessionPing();
            return;
        }
        if (lessonId === activeLessonSessionId) {
            return;
        }
        activeLessonSessionId = lessonId;
        callLessonKw("action_register_lesson_session_start", lessonId);
        startSessionPing(lessonId);
    };

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

    const hardenVideoPlayback = (videoEl) => {
        videoEl.setAttribute("controlsList", "nodownload noplaybackrate");
        videoEl.setAttribute("disablePictureInPicture", "");
        videoEl.oncontextmenu = (ev) => ev.preventDefault();
    };

    const startTracking = (videoEl) => {
        const lessonId = parseLessonId(videoEl);
        if (!lessonId) {
            return;
        }
        hardenVideoPlayback(videoEl);
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

    const onVideoActivity = (ev) => {
        const target = ev.target;
        if (!(target instanceof HTMLVideoElement)) {
            return;
        }
        if (!target.classList.contains("lms-video-tracker")) {
            return;
        }
        if (ev.type === "play") {
            startTracking(target);
            return;
        }
        callProgressRpc(target);
        stopTracking(target);
    };

    for (const eventName of ["play", "pause", "ended"]) {
        document.addEventListener(eventName, onVideoActivity, true);
    }

    const bindExistingVideos = () => {
        document.querySelectorAll("video.lms-video-tracker").forEach(hardenVideoPlayback);
    };
    const debounce = (fn, ms = 250) => {
        let timer = null;
        return (...args) => {
            if (timer) {
                clearTimeout(timer);
            }
            timer = setTimeout(() => fn(...args), ms);
        };
    };
    const startDomHooks = () => {
        if (!document.body) {
            return;
        }
        bindExistingVideos();
        const mo = new MutationObserver(debounce(bindExistingVideos, 300));
        mo.observe(document.body, { childList: true, subtree: true });
    };
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startDomHooks, { once: true });
    } else {
        startDomHooks();
    }

    window.addEventListener("hashchange", syncLessonSessionFromUrl);
    syncLessonSessionFromUrl();
    window.addEventListener("beforeunload", () => {
        const lessonId = activeLessonSessionId;
        if (lessonId) {
            callLessonKw("action_ping_lesson_session", lessonId);
        }
    });

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
