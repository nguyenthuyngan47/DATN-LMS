/** @odoo-module **/

/**
 * Webcam UI for face enrollment (student profile) and lesson check-in.
 * Embedding: 16x8 grayscale normalized = 128 dims (face_embedding_utils.FACE_EMBEDDING_DIM).
 */
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

async function rpcCallKw(model, method, args) {
    const payload = {
        id: Date.now(),
        jsonrpc: "2.0",
        method: "call",
        params: {
            model,
            method,
            args,
            kwargs: {},
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
    row.className = "d-flex gap-2 align-items-center flex-wrap";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm";
    const status = document.createElement("span");
    status.className = "text-muted small";
    row.appendChild(btn);
    row.appendChild(status);
    wrap.appendChild(video);
    wrap.appendChild(row);
    el.appendChild(wrap);

    const role = el.dataset.lmsRole;
    if (role === "enroll") {
        btn.textContent = "Capture and Save Face Template";
    } else {
        btn.textContent = "Check In (Take Photo)";
    }

    let stream;
    const startCam = async () => {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            video.srcObject = stream;
            status.textContent = "";
        } catch (e) {
            status.textContent = "Could not open camera: " + (e.message || e);
        }
    };
    startCam();

    btn.addEventListener("click", async () => {
        const vec = buildEmbedding128FromVideo(video);
        if (!vec) {
            status.textContent = "No frame from camera yet. Wait for the video feed, then try again.";
            return;
        }
        const photoB64 = capturePhotoBase64FromVideo(video);
        const json = JSON.stringify(vec);
        btn.disabled = true;
        status.textContent = "Processing…";
        try {
            if (role === "enroll") {
                const sid = parseInt(el.dataset.studentId, 10);
                await rpcCallKw("lms.student", "action_save_face_embedding_json", [
                    [sid],
                    json,
                    photoB64 || false,
                ]);
            } else {
                const lid = parseInt(el.dataset.lessonId, 10);
                await rpcCallKw("lms.lesson", "action_lesson_face_attendance", [
                    [lid],
                    json,
                    photoB64 || false,
                ]);
            }
            status.textContent = "Success. Reloading page…";
            window.setTimeout(() => window.location.reload(), 600);
        } catch (e) {
            status.textContent = (e && e.message) || String(e);
        } finally {
            btn.disabled = false;
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

function bootFaceMount() {
    bindRoots();
    const mo = new MutationObserver(() => bindRoots());
    mo.observe(document.body, { childList: true, subtree: true });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootFaceMount);
} else {
    bootFaceMount();
}
