# 1) Cài gói:
## pip install fastapi uvicorn opencv-python

# 2) Chạy server mock trên laptop:
## uvicorn fake_cam:app --host 0.0.0.0 --port 9000 --reload
#
# Giả lập ESP32-CAM bằng webcam laptop:
# - /login (GET/POST): set cookie SID, trả 302 như ESP32
# - /capture: trả ảnh JPEG từ webcam
# - /stream: trả MJPEG (multipart/x-mixed-replace)
# - /status: JSON tượng trưng
# - /servo?ch=1&val=.. /servo?ch=2&val=..: lưu pan/tilt "ảo"
#
# Chạy:
#   pip install fastapi uvicorn opencv-python
#   uvicorn laptop_cam_mock:app --host 0.0.0.0 --port 9000 --reload
#
# Thêm vào UI:
#   cam_id: pc_cam
#   host:   http://<IP_laptop>:9000 hoac http://localhost:9000
#   user:   iot
#   pass:   123456
#   
# Gợi ý Windows: dùng CAP_DSHOW để mở webcam nhanh hơn.

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, StreamingResponse
from starlette.responses import RedirectResponse
from starlette.middleware.cors import CORSMiddleware
import cv2
import time
import threading
import os

# ========= Cấu hình đơn giản =========
USERNAME = os.environ.get("MOCK_CAM_USER", "iot")
PASSWORD = os.environ.get("MOCK_CAM_PASS", "123456")
DEVICE_INDEX = int(os.environ.get("MOCK_CAM_DEVICE", "0"))  # 0 = webcam mặc định

# ========= Trạng thái giả lập =========
SESSION_SID = None
SERVO = {"pan": 90, "tilt": 90}   # giữ giá trị servo "ảo"

# ========= Webcam grabber (thread) =========
class FrameGrabber:
    def __init__(self, device_index=0):
        # Trên Windows, CAP_DSHOW giúp mở nhanh và ổn định hơn
        self.cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(device_index)
        self.lock = threading.Lock()
        self.latest = None
        self.running = False
        self.th = None

        # Kích thước gợi ý (không bắt buộc)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    def start(self):
        if self.running:
            return
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def stop(self):
        self.running = False
        if self.th and self.th.is_alive():
            self.th.join(timeout=1.0)
        if self.cap:
            self.cap.release()

    def _loop(self):
        while self.running:
            ok, frame = self.cap.read()
            if ok:
                with self.lock:
                    self.latest = frame
            time.sleep(0.01)  # ~100 fps max, giảm CPU

    def get_frame(self):
        with self.lock:
            if self.latest is None:
                return None
            return self.latest.copy()

grabber = FrameGrabber(DEVICE_INDEX)
grabber.start()

# ========= FastAPI =========
app = FastAPI(title="Mock ESP32-CAM by Laptop Webcam")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# ========= Helpers =========
def logged_in(req: Request) -> bool:
    sid = req.cookies.get("SID")
    return bool(sid) and sid == SESSION_SID

def login_ok(user: str, pw: str) -> bool:
    return user == USERNAME and pw == PASSWORD

def require_login_or_redirect(req: Request):
    if not logged_in(req):
        # Giống ESP32: trả 302 Location: /login
        return RedirectResponse(url="/login", status_code=302)
    return None

# ========= Routes =========

@app.get("/login", response_class=HTMLResponse)
def login_get():
    # Form đơn giản giống ESP32 (không bắt buộc CSRF)
    return """
    <!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Login</title></head>
    <body>
      <h3>Login Mock ESP32-CAM</h3>
      <form method="POST" action="/login">
        <label>User</label><input name="user" /><br/>
        <label>Pass</label><input type="password" name="pass" /><br/>
        <button type="submit">Login</button>
      </form>
    </body></html>
    """

@app.post("/login")
async def login_post(req: Request):
    global SESSION_SID
    body = await req.body()
    # Parse x-www-form-urlencoded very simply
    payload = body.decode("utf-8")
    def _get(k):
        for pair in payload.split("&"):
            if "=" in pair:
                key, val = pair.split("=", 1)
                if key == k:
                    return val.replace("+"," ")
        return ""
    user = _get("user")
    pw   = _get("pass")
    if not login_ok(user, pw):
        # Về /login lại
        return RedirectResponse(url="/login", status_code=302)

    # set session cookie SID
    SESSION_SID = f"{int(time.time())}"
    resp = RedirectResponse(url="/ui", status_code=302)  # giống ESP32 chuyển qua UI
    resp.set_cookie("SID", SESSION_SID, httponly=True, samesite="lax", path="/")
    return resp

@app.get("/logout")
def logout():
    global SESSION_SID
    SESSION_SID = None
    resp = RedirectResponse(url="/login", status_code=302)
    # clear cookie
    resp.delete_cookie("SID", path="/")
    return resp

@app.get("/ui", response_class=HTMLResponse)
def ui(req: Request):
    redir = require_login_or_redirect(req)
    if redir: return redir
    return """
    <!doctype html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
    <body>
      <h3>Mock ESP32-CAM UI</h3>
      <p>Thiết bị giả lập bằng webcam laptop. Dùng /capture, /stream từ backend của bạn.</p>
      <p><a href="/capture" target="_blank">Test /capture</a> | <a href="/stream" target="_blank">Test /stream</a> | <a href="/status" target="_blank">/status</a></p>
    </body></html>
    """

@app.get("/status")
def status(req: Request):
    redir = require_login_or_redirect(req)
    if redir: return redir
    # JSON tượng trưng, chỉ để backend thấy HTTP 200 là "online"
    return JSONResponse({
        "framesize": 7,  # ví dụ VGA
        "quality": 10,
        "brightness": 0,
        "contrast": 0,
        "saturation": 0,
        "hmirror": 0,
        "vflip": 0,
        "pan": SERVO["pan"],
        "tilt": SERVO["tilt"],
        "device": "laptop-webcam"
    })

@app.get("/capture")
def capture(req: Request):
    redir = require_login_or_redirect(req)
    if redir: return redir

    frame = grabber.get_frame()
    if frame is None:
        raise HTTPException(status_code=500, detail="no frame")

    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise HTTPException(status_code=500, detail="encode failed")
    return Response(content=buf.tobytes(), media_type="image/jpeg", headers={
        "Access-Control-Allow-Origin": "*",
        "Content-Disposition": "inline; filename=capture.jpg"
    })

BOUNDARY = "123456789000000000000987654321"

def mjpeg_generator():
    # Tạo stream MJPEG (multipart/x-mixed-replace)
    while True:
        frame = grabber.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            continue
        part = (
            f"--{BOUNDARY}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(buf)}\r\n\r\n"
        ).encode("utf-8") + buf.tobytes() + b"\r\n"
        yield part
        time.sleep(0.06)  # ~16 fps

@app.get("/stream")
def stream(req: Request):
    redir = require_login_or_redirect(req)
    if redir: return redir

    headers = {
        "Age": "0",
        "Cache-Control": "no-cache, private",
        "Pragma": "no-cache",
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Access-Control-Allow-Origin": "*"
    }
    return StreamingResponse(mjpeg_generator(), headers=headers)

@app.get("/servo")
def servo(req: Request, ch: int, val: int):
    redir = require_login_or_redirect(req)
    if redir: return redir

    # Giả lập: chỉ lưu lại pan/tilt
    v = max(0, min(180, int(val)))
    if ch == 1:
        SERVO["pan"] = v
    elif ch == 2:
        SERVO["tilt"] = v
    else:
        return PlainTextResponse("invalid channel", status_code=400)

    return JSONResponse({"ch": ch, "angle": v})

# Tuỳ chọn: LED, control... nếu cần backend gọi.
@app.get("/led")
def led(req: Request, val: int):
    redir = require_login_or_redirect(req)
    if redir: return redir
    # Không làm gì, chỉ trả “OK”
    return PlainTextResponse("1" if val else "0")

# ---- Graceful shutdown ----
import atexit
@atexit.register
def _cleanup():
    try:
        grabber.stop()
    except:
        pass
