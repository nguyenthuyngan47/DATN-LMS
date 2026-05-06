/** @odoo-module **/

/**
 * Gắn UI webcam + nút lên các mount div từ Html compute (học viên / điểm danh bài học).
 * Embedding: 16x8 grayscale chuẩn hóa = 128 chiều (khớp face_embedding_utils.FACE_EMBEDDING_DIM).
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
        btn.textContent = "Chụp và lưu mẫu khuôn mặt";
    } else {
        btn.textContent = "Điểm danh (chụp ảnh)";
    }

    let stream;
    const startCam = async () => {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
            video.srcObject = stream;
            status.textContent = "";
        } catch (e) {
            status.textContent = "Không mở được camera: " + (e.message || e);
        }
    };
    startCam();

    btn.addEventListener("click", async () => {
        const vec = buildEmbedding128FromVideo(video);
        if (!vec) {
            status.textContent = "Chưa có hình từ camera. Đợi video hiển thị rồi thử lại.";
            return;
        }
        const json = JSON.stringify(vec);
        btn.disabled = true;
        status.textContent = "Đang xử lý…";
        try {
            if (role === "enroll") {
                const sid = parseInt(el.dataset.studentId, 10);
                await rpcCallKw("lms.student", "action_save_face_embedding_json", [[sid], json]);
            } else {
                const lid = parseInt(el.dataset.lessonId, 10);
                await rpcCallKw("lms.lesson", "action_lesson_face_attendance", [[lid], json]);
            }
            status.textContent = "Thành công. Đang tải lại trang…";
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
