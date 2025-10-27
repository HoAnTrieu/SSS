# backend/server.py (ở top)
from .detector import build_detector
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.responses import StreamingResponse, FileResponse
import base64
from fastapi import Query, Request
import cv2
import numpy as np
import requests
import time
import os
import threading
from datetime import datetime

from .state import SYSTEM_STATE, list_recordings, EVENT_DIR, save_cameras
from .utils import save_event_image, play_alarm_sound

app = FastAPI(title="Security Backend Demo")


# Khởi tạo bộ dò người (mock YOLO trên Windows)
detector = build_detector()

# Ghi hình đang chạy (theo camera_id)
# {
#   "cam1": {
#        "thread": <RecorderThread>,
#        "path": "data/recordings/cam1_20251026_224500.mp4",
#        "fps": 10,
#        "start_ts": "20251026_224500"
#   },
#   ...
# }
RECORDERS = {}

RECORD_DIR = os.path.join("data", "recordings")
os.makedirs(RECORD_DIR, exist_ok=True)

# ============================================================
# Helper: đăng nhập ESP32-CAM và giữ session SID
# ============================================================

def camera_login(cam_id: str) -> bool:
    """
    Đảm bảo camera cam_id đã đăng nhập và session có cookie SID hợp lệ.
    - Dùng POST /login với user/pass đã khai báo trong SYSTEM_STATE.
    - Lưu cookie SID vào requests.Session() của camera.
    - Không login lại liên tục: nếu mới login <60s trước -> bỏ qua.
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if cam is None:
        return False

    sess = cam["session"]

    # tránh spam login: nếu login <60s trước thì coi như còn hiệu lực
    if time.time() - cam.get("last_login", 0) < 60:
        return True

    login_url = cam["host"] + "/login"

    data = {
        "user": cam["username"],
        "pass": cam["password"]
        # CSRF token không cần vì AUTH_ENABLE_CSRF=0 trong firmware
    }

    try:
        r = sess.post(login_url, data=data, timeout=2.0, allow_redirects=False)
        # Nếu login ok, firmware trả 302 -> /ui hoặc /first-change (hoặc 200)
        if r.status_code in (200, 302):
            cam["last_login"] = time.time()
            return True
        else:
            print(f"[camera_login] FAIL {cam_id}: status={r.status_code}")
            return False
    except Exception as e:
        print(f"[camera_login] EXCEPTION {cam_id}: {e}")
        return False


def camera_get(cam_id: str, path: str, params=None, timeout=2.0):
    """
    GET tới camera /<path> dùng session (cookie SID).
    - Nếu nhận 302 -> session hết hạn -> login lại -> thử lại 1 lần.
    - Trả về đối tượng Response (có .status_code, .content, ...)
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if cam is None:
        raise HTTPException(status_code=404, detail="camera not found")

    # Ensure login trước
    if not camera_login(cam_id):
        raise HTTPException(status_code=500, detail="camera login failed")

    sess = cam["session"]
    full_url = cam["host"] + path

    try:
        r = sess.get(full_url, params=params, timeout=timeout, allow_redirects=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"camera request failed: {e}")

    # Nếu session SID hết hạn -> camera trả 302 về /login
    if r.status_code == 302:
        if not camera_login(cam_id):
            raise HTTPException(status_code=500, detail="camera re-login failed")
        r = cam["session"].get(full_url, params=params, timeout=timeout, allow_redirects=False)

    return r


def fetch_frame_bgr(cam_id: str):
    """
    Lấy 1 frame BGR từ camera (dùng /capture). Raise nếu lỗi.
    """
    # gọi /capture qua camera_get (tự xử lý login/302)
    r = camera_get(cam_id, "/capture", timeout=2.0)
    if r.status_code != 200:
        raise RuntimeError(f"capture failed status={r.status_code}")

    jpg_bytes = r.content
    img_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)

    if frame is None:
        raise RuntimeError("decode failed (frame is None)")

    return frame


# ============================================================
# RecorderThread: ghi video có timestamp
# ============================================================

class RecorderThread(threading.Thread):
    def __init__(self, cam_id: str, out_path: str, fps: int = 10):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.out_path = out_path
        self.fps = fps
        self.running = True
        self.writer = None

    def draw_timestamp(self, frame_bgr):
        """
        Vẽ timestamp ở góc dưới trái của frame.
        """
        ts_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5
        thickness = 1
        color = (0, 255, 0)  # xanh lá cho dễ nhìn
        # vị trí: x=10, y=đáy-10
        x = 10
        y = frame_bgr.shape[0] - 10
        cv2.putText(
            frame_bgr,
            ts_text,
            (x, y),
            font,
            scale,
            color,
            thickness,
            cv2.LINE_AA
        )
        return frame_bgr

    def run(self):
        interval = 1.0 / float(self.fps)

        while self.running:
            try:
                frame = fetch_frame_bgr(self.cam_id)
            except Exception as e:
                print(f"[RecorderThread] frame error {self.cam_id}: {e}")
                time.sleep(interval)
                continue

            # vẽ timestamp lên frame
            frame_with_ts = self.draw_timestamp(frame.copy())

            # khởi tạo VideoWriter khi biết kích thước frame
            if self.writer is None:
                h, w = frame_with_ts.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self.writer = cv2.VideoWriter(
                    self.out_path,
                    fourcc,
                    self.fps,
                    (w, h)
                )
                if not self.writer.isOpened():
                    print("[RecorderThread] cannot open VideoWriter for", self.out_path)
                    self.running = False
                    break

            # ghi frame
            self.writer.write(frame_with_ts)

            time.sleep(interval)

        # cleanup khi dừng
        if self.writer is not None:
            self.writer.release()


# ============================================================
# AUTH CHO DASHBOARD STREAMLIT
# ============================================================

@app.post("/api/login")
def api_login(payload: dict):
    """
    Đăng nhập dashboard (Streamlit), KHÔNG phải login camera.
    """
    user = payload.get("username")
    pw = payload.get("password")

    if user == SYSTEM_STATE["auth"]["user"] and pw == SYSTEM_STATE["auth"]["pass"]:
        SYSTEM_STATE["auth"]["logged_in"] = True
        return {"status": "ok", "msg": "login success"}

    raise HTTPException(status_code=401, detail="bad credentials")


@app.post("/api/logout")
def api_logout():
    SYSTEM_STATE["auth"]["logged_in"] = False
    return {"status": "ok"}


# ============================================================
# CAMERA MANAGEMENT
# ============================================================

@app.get("/api/cameras")
def api_cameras():
    """
    Trả về camera list (không lộ password).
    Giao diện Live & Control dùng cái này.
    """
    cams_out = []
    for cam_id, cam_info in SYSTEM_STATE["cameras"].items():
        host = cam_info["host"]

        # kiểm tra online bằng cách thử /status
        online = False
        try:
            r = camera_get(cam_id, "/status", timeout=1.5)
            if r.status_code == 200:
                online = True
        except Exception as e:
            print(f"[api_cameras] {cam_id} offline: {e}")
            online = False

        cams_out.append({
            "cam_id": cam_id,
            "host": host,
            "online": online,
            "pan": cam_info["pan"],
            "tilt": cam_info["tilt"]
        })

    return {"cameras": cams_out}


@app.get("/api/cameras_full")
def api_cameras_full():
    """
    Trả về danh sách camera kèm thông tin đăng nhập để UI Camera Manager hiển thị.
    KHÔNG trả session object; chỉ trả những trường an toàn để xem/quản lý.
    """
    cams_out = []
    for cam_id, cam_info in SYSTEM_STATE["cameras"].items():
        cams_out.append({
            "cam_id": cam_id,
            "host": cam_info["host"],
            "username": cam_info["username"],
            "password": cam_info["password"],
            "pan": cam_info["pan"],
            "tilt": cam_info["tilt"],
            "pan_ch": cam_info["pan_ch"],
            "tilt_ch": cam_info["tilt_ch"],
        })
    return {"cameras": cams_out}


@app.post("/api/add_camera")
def api_add_camera(payload: dict):
    """
    Thêm camera ESP32-CAM (hoặc mock laptop_cam_mock).
    payload:
      {
        "cam_id": "cam3",
        "host": "http://192.168.137.150",
        "username": "iot",
        "password": "123456"
      }
    """
    cam_id = payload.get("cam_id")
    host = payload.get("host")
    username = payload.get("username")
    password = payload.get("password")

    if not cam_id or not host or not username or not password:
        raise HTTPException(status_code=400, detail="missing required fields")

    if cam_id in SYSTEM_STATE["cameras"]:
        raise HTTPException(status_code=400, detail="camera id already exists")

    SYSTEM_STATE["cameras"][cam_id] = {
        "type": "esp32",  # mock laptop_cam_mock cũng giả ESP32 API nên cứ để "esp32"
        "host": host,
        "username": username,
        "password": password,
        "session": requests.Session(),
        "pan_ch": 1,
        "tilt_ch": 2,
        "pan": 90,
        "tilt": 90,
        "last_login": 0.0
    }

    # LƯU XUỐNG DISK
    save_cameras(SYSTEM_STATE["cameras"])

    return {"status": "ok", "msg": f"{cam_id} added"}


@app.post("/api/remove_camera")
def api_remove_camera(payload: dict):
    """
    Xóa camera khỏi hệ thống và lưu lại cameras.json
    payload: { "cam_id": "cam3" }
    """
    cam_id = payload.get("cam_id")
    if not cam_id:
        raise HTTPException(status_code=400, detail="cam_id missing")

    if cam_id not in SYSTEM_STATE["cameras"]:
        raise HTTPException(status_code=404, detail="camera not found")

    # Nếu đang ghi hình cam này -> dừng luôn
    if cam_id in RECORDERS:
        rec = RECORDERS[cam_id]
        rec["thread"].running = False
        rec["thread"].join(timeout=1.0)
        del RECORDERS[cam_id]

    del SYSTEM_STATE["cameras"][cam_id]

    # LƯU XUỐNG DISK
    save_cameras(SYSTEM_STATE["cameras"])

    return {"status": "ok", "msg": f"{cam_id} removed"}


# ============================================================
# SERVO CONTROL
# ============================================================

@app.post("/api/servo/{cam_id}")
def api_servo(cam_id: str, payload: dict):
    """
    Điều khiển pan/tilt servo (ESP32-CAM).
    Sau khi chỉnh pan/tilt thành công -> lưu cameras.json
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")

    pan = int(payload.get("pan", cam["pan"]))
    tilt = int(payload.get("tilt", cam["tilt"]))

    try:
        r1 = camera_get(
            cam_id,
            "/servo",
            params={"ch": cam.get("pan_ch", 1), "val": pan},
            timeout=1.0
        )
        r2 = camera_get(
            cam_id,
            "/servo",
            params={"ch": cam.get("tilt_ch", 2), "val": tilt},
            timeout=1.0
        )

        if (r1.status_code not in (200, 302)) or (r2.status_code not in (200, 302)):
            raise HTTPException(status_code=500, detail="servo control failed (bad status)")

        cam["pan"] = pan
        cam["tilt"] = tilt

        # LƯU XUỐNG DISK (ghi luôn pan/tilt mới)
        save_cameras(SYSTEM_STATE["cameras"])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"servo control failed: {e}")

    return {"status": "ok", "pan": pan, "tilt": tilt}


# ============================================================
# ALARM TOGGLE
# ============================================================

@app.post("/api/toggle_alarm")
def api_toggle_alarm(payload: dict):
    enabled = bool(payload.get("enabled", True))
    SYSTEM_STATE["alarm_enabled"] = enabled
    return {"status": "ok", "alarm_enabled": enabled}


# ============================================================
# EVENTS & IMAGES
# ============================================================

@app.get("/api/events")
def api_events():
    return {"events": SYSTEM_STATE["events"]}


@app.get("/api/event_image")
def api_event_image(path: str):
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="not found")


# ============================================================
# RECORDINGS (DANH SÁCH VIDEO)
# ============================================================

@app.get("/api/recordings")
def api_recordings():
    """
    Liệt kê các file .mp4 đã ghi trong data/recordings
    (bao gồm cả các file vừa dừng ghi).
    """
    return {"recordings": list_recordings()}


@app.get("/api/download_video")
def api_download_video(file: str):
    if os.path.isfile(file):
        filename_only = os.path.basename(file)
        return FileResponse(file, media_type="video/mp4", filename=filename_only)
    raise HTTPException(status_code=404, detail="not found")

from fastapi.responses import StreamingResponse

@app.get("/api/preview_video")
def api_preview_video(request: Request, file: str = Query(...)):
    """
    Stream video/mp4 với hỗ trợ HTTP Range (206 Partial Content)
    để trình duyệt trong Streamlit có thể play/tua bình thường.

    - Nếu browser gửi header Range: bytes=start-end
      -> trả 206 + Content-Range + Content-Length, chỉ gửi đoạn đó.
    - Nếu không có Range
      -> trả full file (200).

    Thêm Access-Control-Allow-Origin: * để cho phép phát chéo cổng
    (UI chạy port 8501, backend port 8000).
    """

    if not os.path.isfile(file):
        raise HTTPException(status_code=404, detail="not found")

    file_size = os.path.getsize(file)
    range_header = request.headers.get("range")

    def iter_file_range(start: int, end: int):
        """đọc file từ byte start..end (inclusive) và yield ra chunk"""
        with open(file, "rb") as f:
            f.seek(start)
            bytes_to_read = end - start + 1
            chunk_size = 1024 * 1024  # 1MB
            while bytes_to_read > 0:
                read_chunk = f.read(min(chunk_size, bytes_to_read))
                if not read_chunk:
                    break
                bytes_to_read -= len(read_chunk)
                yield read_chunk

    # Nếu client yêu cầu Range (ví dụ "bytes=0-")
    if range_header:
        # ví dụ "bytes=1000-2000" hoặc "bytes=1000-"
        # Ta parse thô cho đơn giản
        try:
            units, rng = range_header.split("=")
            if units.strip().lower() != "bytes":
                raise ValueError("Not bytes range")
            start_str, end_str = rng.split("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1
        except Exception:
            # Range header lỗi -> fallback full file
            start = 0
            end = file_size - 1

        if end >= file_size:
            end = file_size - 1
        if start < 0:
            start = 0

        content_length = end - start + 1

        resp = StreamingResponse(
            iter_file_range(start, end),
            media_type="video/mp4",
            status_code=206,  # quan trọng! báo partial content
        )

        resp.headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(content_length)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # Không có Range header -> trả full file
    def iter_file_full():
        with open(file, "rb") as f:
            while True:
                data = f.read(1024 * 1024)
                if not data:
                    break
                yield data

    resp = StreamingResponse(
        iter_file_full(),
        media_type="video/mp4",
        status_code=200,
    )
    resp.headers["Content-Length"] = str(file_size)
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-cache"
    return resp



# ============================================================
# GHI HÌNH: START / STOP / STATUS
# ============================================================

@app.post("/api/record/start/{cam_id}")
def api_record_start(cam_id: str, payload: dict | None = None):
    """
    Bắt đầu ghi hình từ camera cam_id.
    Tự động chèn timestamp vào mỗi frame (góc dưới).
    payload (optional): { "fps": 8 }  # mặc định 10fps, giới hạn 1..10
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")

    # giới hạn fps
    fps = 10
    if payload and "fps" in payload:
        try:
            fps = int(payload["fps"])
        except:
            pass
    if fps < 1:
        fps = 1
    if fps > 10:
        fps = 10

    # nếu đã ghi rồi thì báo luôn
    if cam_id in RECORDERS:
        return {
            "status": "already_recording",
            "file": RECORDERS[cam_id]["path"],
            "fps": RECORDERS[cam_id]["fps"],
            "start_ts": RECORDERS[cam_id]["start_ts"]
        }

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{cam_id}_{ts}.mp4"
    out_path = os.path.join(RECORD_DIR, filename)
    os.makedirs(RECORD_DIR, exist_ok=True)

    rec_thread = RecorderThread(cam_id, out_path, fps=fps)
    rec_thread.start()

    RECORDERS[cam_id] = {
        "thread": rec_thread,
        "path": out_path,
        "fps": fps,
        "start_ts": ts
    }

    return {
        "status": "recording_started",
        "file": out_path,
        "fps": fps,
        "start_ts": ts
    }


@app.post("/api/record/stop/{cam_id}")
def api_record_stop(cam_id: str):
    """
    Dừng ghi hình từ camera cam_id.
    """
    if cam_id not in RECORDERS:
        raise HTTPException(status_code=404, detail="not recording")

    rec = RECORDERS[cam_id]
    rec["thread"].running = False
    rec["thread"].join(timeout=1.0)
    final_file = rec["path"]

    del RECORDERS[cam_id]

    return {
        "status": "recording_stopped",
        "file": final_file
    }


@app.get("/api/record/status")
def api_record_status():
    """
    Xem các camera hiện đang ghi.
    """
    out = []
    for cid, rec in RECORDERS.items():
        out.append({
            "cam_id": cid,
            "file": rec["path"],
            "fps": rec["fps"],
            "start_ts": rec["start_ts"]
        })
    return {"active_recordings": out}


# ============================================================
# DETECT FRAME (CHỤP 1 FRAME TỪ CAMERA, LƯU ẢNH, MOCK YOLO)
# ============================================================

@app.get("/api/detect_frame/{cam_id}")
def api_detect_frame(cam_id: str):
    """
    - Chụp 1 frame từ camera.
    - Chạy detect người bằng detector (YOLO mock hoặc YOLO thật sau này).
    - Luôn trả JSON, kể cả khi lỗi.
    - Nếu phát hiện người -> lưu event + còi.
    """
    try:
        cam = SYSTEM_STATE["cameras"].get(cam_id)
        if not cam:
            # camera không tồn tại
            return {
                "detected": False,
                "boxes": [],
                "max_confidence": 0.0,
                "saved_image": None,
                "note": f"camera {cam_id} not found"
            }

        # LẤY FRAME
        try:
            frame = fetch_frame_bgr(cam_id)
        except HTTPException as e:
            return {
                "detected": False,
                "boxes": [],
                "max_confidence": 0.0,
                "saved_image": None,
                "note": f"camera error: {e.detail}"
            }
        except Exception as e:
            return {
                "detected": False,
                "boxes": [],
                "max_confidence": 0.0,
                "saved_image": None,
                "note": f"camera error: {e}"
            }

        # DETECT
        boxes = detector.detect_person(frame)
        detected = len(boxes) > 0
        max_conf = max([b["conf"] for b in boxes], default=0.0)

        # LƯU ẢNH SỰ KIỆN
        annotated_frame = detector.annotate(frame, boxes)
        saved_path = save_event_image(cam_id, annotated_frame)
        
        # Nếu phát hiện người -> ghi event + phát còi
        if detected:
            SYSTEM_STATE["events"].insert(0, {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "cam_id": cam_id,
                "img_path": saved_path,
                "confidence": max_conf
            })
            if SYSTEM_STATE["alarm_enabled"]:
                play_alarm_sound()

        # JSON trả về
        return {
            "detected": detected,
            "boxes": boxes,
            "max_confidence": max_conf,
            "saved_image": saved_path,
            "note": ""  # chuỗi rỗng = không lỗi
        }

    except Exception as e:
        # fallback cuối cùng
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "saved_image": None,
            "note": f"internal error: {e}"
        }

#-------------------------------------------
# DETECT VÀ TRẢ VỀ ẢNH ANNOTATE DẠNG BASE64
#-------------------------------------------
@app.get("/api/detect_only_frame/{cam_id}")
def api_detect_only_frame(cam_id: str):
    """
    - Lấy 1 frame từ camera.
    - Chạy detector.detect_person + detector.annotate.
    - KHÔNG lưu event, KHÔNG thêm vào SYSTEM_STATE["events"].
    - Có phát còi báo động nếu phát hiện người (kèm cooldown để đỡ kêu điên cuồng).
    - Trả JSON: {detected, max_confidence, annotated_jpeg_b64, note}
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if not cam:
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "annotated_jpeg_b64": None,
            "note": f"camera {cam_id} not found"
        }

    # 1) Lấy frame JPEG từ camera (/capture)
    try:
        r = camera_get(cam_id, "/capture", timeout=2.0)
    except HTTPException as e:
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "annotated_jpeg_b64": None,
            "note": f"camera error: {e.detail}"
        }
    except Exception as e:
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "annotated_jpeg_b64": None,
            "note": f"camera error: {e}"
        }

    if r.status_code != 200:
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "annotated_jpeg_b64": None,
            "note": f"capture failed status={r.status_code}"
        }

    jpg_bytes = r.content
    img_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)

    if frame is None:
        return {
            "detected": False,
            "boxes": [],
            "max_confidence": 0.0,
            "annotated_jpeg_b64": None,
            "note": "decode failed (frame is None)"
        }

    # 2) Chạy AI detect người
    boxes = detector.detect_person(frame)
    detected = len(boxes) > 0
    max_conf = max([b["conf"] for b in boxes], default=0.0)

    # 3) Vẽ khung bounding box
    annotated = detector.annotate(frame, boxes)

    # 4) Mã hóa annotated frame thành JPEG base64 để UI hiển thị realtime
    ok, enc_jpg = cv2.imencode(".jpg", annotated)
    if not ok:
        b64 = None
    else:
        b64 = base64.b64encode(enc_jpg.tobytes()).decode("utf-8")

    # 5) Phát còi báo động nếu có người và báo động đang bật,
    #    dùng cooldown để không beep mỗi frame
    if detected and SYSTEM_STATE.get("alarm_enabled", True):
        now = time.time()
        last = SYSTEM_STATE.get("last_alarm_ts", 0.0)
        if now - last > 1.0:  # cooldown 1 giây
            try:
                play_alarm_sound()
            except Exception as e:
                print(f"[alarm] play_alarm_sound error: {e}")
            SYSTEM_STATE["last_alarm_ts"] = now

    # 6) Trả kết quả cho UI
    return {
        "detected": detected,
        "boxes": boxes,
        "max_confidence": max_conf,
        "annotated_jpeg_b64": b64,
        "note": ""
    }

# -----------------------------------------------------------
# LIVE STREAM FRAME (LẤY 1 FRAME MỚI NHẤT TỪ CAMERA)
# -----------------------------------------------------------

@app.get("/api/stream_frame/{cam_id}")
def api_stream_frame(cam_id: str):
    """
    Lấy 1 frame "live" từ camera, không lưu, không detect.
    Trả về image/jpeg bytes để UI hiển thị gần realtime.
    """
    cam = SYSTEM_STATE["cameras"].get(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="camera not found")

    try:
        frame = fetch_frame_bgr(cam_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"camera error: {e}")

    # mã hóa BGR -> JPEG
    ok, jpg_buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise HTTPException(status_code=500, detail="jpeg encode failed")

    return Response(content=jpg_buf.tobytes(), media_type="image/jpeg")
