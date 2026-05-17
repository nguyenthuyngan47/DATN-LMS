/** @odoo-module **/

/**
 * Webcam UI for face enrollment (student profile) and lesson check-in.
 * Embedding: 16x8 grayscale normalized = 128 dims (face_embedding_utils.FACE_EMBEDDING_DIM).
 */
import { _t } from "@web/core/l10n/translation";

const EMBEDDING_DIM = 128;

function buildEmbedding128FromVideo(videoEl) {
    const w = 16;
    const h = 8;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx || !videoEl.videoWidth) {
        return null;
    }
    ctx.drawImage(videoEl, 0, 0, w, h);
    const { data } = ctx.getImageData(0, 0, w, h);
    const vec = [];
    for (let i = 0; i < data.length; i += 4) {
        const g = (data[i] + data[i + 1] + data[i + 2]) / 3 / 255;
        vec.push(g);
    }
    if (vec.length !== EMBEDDING_DIM) {
        return null;
    }
    const mean = vec.reduce((a, b) => a + b, 0) / vec.length;
    const dev = Math.sqrt(vec.reduce((s, x) => s + (x - mean) ** 2, 0) / vec.length) || 1;
    return vec.map((x) => (x - mean) / dev);
}

/** Full-frame JPEG base64 for avatar (student + res.users). */
function capturePhotoBase64FromVideo(videoEl) {
    if (!videoEl.videoWidth) {
        return null;
    }
    const maxW = 640;
    const w = Math.min(maxW, videoEl.videoWidth);
    const h = Math.round((videoEl.videoHeight / videoEl.videoWidth) * w);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
        return null;
    }
    ctx.drawImage(videoEl, 0, 0, w, h);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
    const parts = dataUrl.split(",");
    return parts.length > 1 ? parts[1] : null;
}

function stopStream(stream) {
    if (stream) {
        stream.getTracks().forEach((t) => t.stop());
    }
}

function toDataUrl(b64) {
    if (!b64) {
        return null;
    }
    if (b64.startsWith("data:")) {
        return b64;
    }
    return `data:image/jpeg;base64,${b64}`;
}

function enrollPhotoCacheKey(studentId) {
    return `lms_enroll_photo_${studentId}`;
}

function saveEnrollPhotoCache(studentId, photoB64) {
    const dataUrl = toDataUrl(photoB64);
    if (dataUrl && studentId) {
        sessionStorage.setItem(enrollPhotoCacheKey(studentId), dataUrl);
    }
}

function loadEnrollPhotoCache(studentId) {
    return sessionStorage.getItem(enrollPhotoCacheKey(studentId));
}

function attendancePhotoCacheKey(lessonId) {
    return `lms_attend_photo_${lessonId}`;
}

function saveAttendancePhotoCache(lessonId, photoB64) {
    const dataUrl = toDataUrl(photoB64);
    if (dataUrl && lessonId) {
        sessionStorage.setItem(attendancePhotoCacheKey(lessonId), dataUrl);
    }
}

function loadAttendancePhotoCache(lessonId) {
    return sessionStorage.getItem(attendancePhotoCacheKey(lessonId));
}

function showSnapshotImg(wrap, video, b64OrDataUrl) {
    let src = b64OrDataUrl;
    if (!src) {
        return null;
    }
    if (!src.startsWith("data:") && !src.startsWith("/") && !src.startsWith("http")) {
        src = toDataUrl(b64OrDataUrl);
    }
    video.style.display = "none";
    let img = wrap.querySelector(".lms-face-snapshot");
    if (!img) {
        img = document.createElement("img");
        img.className = "lms-face-snapshot rounded border";
        img.style.maxWidth = "320px";
        img.style.maxHeight = "240px";
        img.style.objectFit = "cover";
        wrap.insertBefore(img, video);
    }
    img.onerror = () => {
        img.removeAttribute("src");
        img.alt = "";
        img.style.display = "none";
    };
    img.style.display = "";
    img.src = src;
    img.alt = _t("Attendance photo");
    return img;
}

function hideSnapshot(wrap, video) {
    const img = wrap.querySelector(".lms-face-snapshot");
    if (img) {
        img.remove();
    }
    video.style.display = "";
}

function lockAttendUi(wrap, message) {
    const btn = wrap.querySelector(".lms-face-capture");
    if (btn) {
        btn.disabled = true;
        btn.style.display = "none";
    }
    const status = wrap.querySelector(".lms-face-status");
    if (status) {
        status.textContent = message;
        status.classList.remove("text-muted");
        status.classList.add("text-success");
    }
}

function ensureEnrollRetakeButton(wrap) {
    const row = wrap.querySelector(".lms-face-actions");
    if (!row) {
        return null;
    }
    let retake = row.querySelector(".lms-face-retake");
    if (!retake) {
        retake = document.createElement("button");
        retake.type = "button";
        retake.className = "btn btn-secondary btn-sm lms-face-retake";
        retake.textContent = _t("Re-register face template");
        row.appendChild(retake);
    }
    retake.style.display = "";
    return retake;
}

async function showEnrollmentLocked(wrap, video, studentId) {
    const src = await resolveEnrollmentPhotoSrc(studentId);
    if (src) {
        showSnapshotImg(wrap, video, src);
    } else {
        video.style.display = "none";
    }
    stopStream(video._lmsStream);
    video._lmsStream = null;
    video.srcObject = null;
    const captureBtn = wrap.querySelector(".lms-face-capture");
    if (captureBtn) {
        captureBtn.style.display = "none";
        captureBtn.disabled = false;
    }
    const status = wrap.querySelector(".lms-face-status");
    if (status) {
        status.textContent = src
            ? _t("Face template registered.")
            : _t("Face template registered (no photo saved — use Re-register to capture a photo).");
        status.classList.remove("text-muted", "text-danger");
        status.classList.add("text-success");
    }
    ensureEnrollRetakeButton(wrap);
}

function setupEnrollRetake(wrap, video, captureBtn, status, startCam) {
    const retake = wrap.querySelector(".lms-face-retake");
    if (!retake) {
        return;
    }
    retake.addEventListener("click", async () => {
        hideSnapshot(wrap, video);
        retake.style.display = "none";
        captureBtn.style.display = "";
        status.textContent = "";
        status.classList.remove("text-success", "text-danger");
        status.classList.add("text-muted");
        await startCam();
    });
}

async function loadLockedEnrollment(wrap, video, studentId) {
    const rows = await rpcCallKw("lms.student", "read", [
        [studentId],
        ["face_embedding_json"],
    ]);
    if (!rows || !rows[0] || !rows[0].face_embedding_json) {
        return false;
    }
    await showEnrollmentLocked(wrap, video, studentId);
    return true;
}

async function rpcCallKw(model, method, args, kwargs = {}) {
    const payload = {
        id: Date.now(),
        jsonrpc: "2.0",
        method: "call",
        params: {
            model,
            method,
            args,
            kwargs,
        },
    };
    const res = await fetch(`/web/dataset/call_kw/${model}/${method}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
        const msg =
            data.error.data?.message ||
            data.error.message ||
            (typeof data.error === "string" ? data.error : JSON.stringify(data.error));
        throw new Error(msg);
    }
    return data.result;
}

async function sessionUid() {
    try {
        const res = await fetch("/web/session/get_session_info", { credentials: "same-origin" });
        const info = await res.json();
        return info.uid || null;
    } catch {
        return null;
    }
}

async function resolveAttendancePhotoUrl(uid) {
    if (!uid) {
        return null;
    }
    const students = await rpcCallKw(
        "lms.student",
        "search_read",
        [[["user_id", "=", uid]]],
        { fields: ["id"], limit: 1 }
    );
    const studentId = students && students[0] && students[0].id;
    const unique = Date.now();
    if (studentId) {
        return `/web/image/lms.student/${studentId}/image_1920?unique=${unique}`;
    }
    return `/web/image?model=res.users&id=${uid}&field=avatar_1920&unique=${unique}`;
}

async function resolveEnrollmentPhotoSrc(studentId) {
    const cached = loadEnrollPhotoCache(studentId);
    if (cached) {
        return cached;
    }
    const rows = await rpcCallKw("lms.student", "read", [[studentId], ["image_1920"]]);
    const imageB64 = rows && rows[0] && rows[0].image_1920;
    if (imageB64) {
        saveEnrollPhotoCache(studentId, imageB64);
        return toDataUrl(imageB64);
    }
    const uid = await sessionUid();
    return resolveAttendancePhotoUrl(uid);
}

async function loadLockedAttendance(wrap, video, lessonId) {
    const rows = await rpcCallKw("lms.lesson", "read", [[lessonId], ["current_user_face_checked_in"]]);
    if (!rows || !rows[0] || !rows[0].current_user_face_checked_in) {
        return false;
    }
    const cached = loadAttendancePhotoCache(lessonId);
    if (cached) {
        showSnapshotImg(wrap, video, cached);
    } else {
        const uid = await sessionUid();
        const src = await resolveAttendancePhotoUrl(uid);
        if (src) {
            showSnapshotImg(wrap, video, src);
        } else {
            video.style.display = "none";
        }
    }
    stopStream(video._lmsStream);
    video._lmsStream = null;
    lockAttendUi(wrap, _t("Attendance recorded. Photo locked."));
    return true;
}

function ensureUi(el) {
    if (el.querySelector(".lms-face-ui")) {
        return;
    }
    const wrap = document.createElement("div");
    wrap.className = "lms-face-ui d-flex flex-column gap-2 my-2";
    const video = document.createElement("video");
    video.setAttribute("playsinline", "");
    video.setAttribute("autoplay", "");
    video.muted = true;
    video.className = "rounded border";
    video.style.maxWidth = "320px";
    video.style.maxHeight = "240px";
    const row = document.createElement("div");
    row.className = "d-flex gap-2 align-items-center flex-wrap lms-face-actions";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm lms-face-capture";
    const status = document.createElement("span");
    status.className = "text-muted small lms-face-status";
    row.appendChild(btn);
    row.appendChild(status);
    wrap.appendChild(video);
    wrap.appendChild(row);
    el.appendChild(wrap);

    const role = el.dataset.lmsRole;
    const isAttend = role === "attend";
    if (role === "enroll") {
        btn.textContent = _t("Capture and Save Face Template");
    } else {
        btn.textContent = _t("Check In (Take Photo)");
    }

    let stream = null;
    const startCam = async () => {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            video._lmsStream = stream;
            video.srcObject = stream;
            status.textContent = "";
        } catch (e) {
            status.textContent = _t("Could not open camera:") + " " + (e.message || e);
        }
    };

    if (isAttend) {
        const lessonId = parseInt(el.dataset.lessonId, 10);
        loadLockedAttendance(wrap, video, lessonId)
            .then((locked) => {
                if (!locked) {
                    startCam();
                }
            })
            .catch(() => startCam());
    } else if (role === "enroll") {
        const studentId = parseInt(el.dataset.studentId, 10);
        const initEnroll = async () => {
            if (el.dataset.lmsFaceRegistered === "1") {
                await showEnrollmentLocked(wrap, video, studentId);
                setupEnrollRetake(wrap, video, btn, status, startCam);
                return;
            }
            const locked = await loadLockedEnrollment(wrap, video, studentId);
            if (locked) {
                setupEnrollRetake(wrap, video, btn, status, startCam);
            } else {
                startCam();
            }
        };
        initEnroll().catch(() => startCam());
    } else {
        startCam();
    }

    btn.addEventListener("click", async () => {
        const vec = buildEmbedding128FromVideo(video);
        if (!vec) {
            status.textContent = _t("No frame from camera yet. Wait for the video feed, then try again.");
            return;
        }
        const photoB64 = capturePhotoBase64FromVideo(video);
        const json = JSON.stringify(vec);
        btn.disabled = true;
        status.textContent = _t("Processing…");

        if (isAttend && photoB64) {
            const lid = parseInt(el.dataset.lessonId, 10);
            saveAttendancePhotoCache(lid, photoB64);
            showSnapshotImg(wrap, video, photoB64);
            stopStream(stream);
            stream = null;
            video._lmsStream = null;
            video.srcObject = null;
        }

        try {
            if (role === "enroll") {
                const sid = parseInt(el.dataset.studentId, 10);
                await rpcCallKw("lms.student", "action_save_face_embedding_json", [
                    [sid],
                    json,
                    photoB64 || false,
                ]);
                if (photoB64) {
                    saveEnrollPhotoCache(sid, photoB64);
                    showSnapshotImg(wrap, video, photoB64);
                    stopStream(stream);
                    stream = null;
                }
                status.textContent = _t("Success. Reloading page…");
                window.setTimeout(() => window.location.reload(), 600);
            } else {
                const lid = parseInt(el.dataset.lessonId, 10);
                await rpcCallKw("lms.lesson", "action_lesson_face_attendance", [
                    [lid],
                    json,
                    photoB64 || false,
                ]);
                if (photoB64) {
                    saveAttendancePhotoCache(lid, photoB64);
                    showSnapshotImg(wrap, video, photoB64);
                }
                lockAttendUi(wrap, _t("Attendance recorded. Photo locked."));
            }
        } catch (e) {
            status.textContent = (e && e.message) || String(e);
            status.classList.add("text-danger");
            if (isAttend) {
                hideSnapshot(wrap, video);
                btn.disabled = false;
                btn.style.display = "";
                if (!stream) {
                    startCam();
                }
            } else {
                btn.disabled = false;
            }
        }
    });
}

function bindRoots() {
    document.querySelectorAll(".o_lms_student_face_root:not([data-lms-face-bound])").forEach((el) => {
        el.dataset.lmsFaceBound = "1";
        ensureUi(el);
    });
    document.querySelectorAll(".o_lms_lesson_face_root:not([data-lms-face-bound])").forEach((el) => {
        el.dataset.lmsFaceBound = "1";
        ensureUi(el);
    });
}

function debounce(fn, ms = 250) {
    let timer = null;
    return (...args) => {
        if (timer) {
            clearTimeout(timer);
        }
        timer = setTimeout(() => fn(...args), ms);
    };
}

function bootFaceMount() {
    if (!document.body) {
        return;
    }
    bindRoots();
    const mo = new MutationObserver(debounce(() => bindRoots(), 300));
    mo.observe(document.body, { childList: true, subtree: true });
}

(() => {
    if (window.__lmsFaceMountBooted) {
        return;
    }
    window.__lmsFaceMountBooted = true;
    const start = () => bootFaceMount();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
