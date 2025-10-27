import os, time, glob, json, requests

DATA_DIR = "data"
EVENT_DIR = os.path.join(DATA_DIR, "events")
RECORD_DIR = os.path.join(DATA_DIR, "recordings")
CAMERA_CONFIG_PATH = os.path.join(DATA_DIR, "cameras.json")

os.makedirs(EVENT_DIR, exist_ok=True)
os.makedirs(RECORD_DIR, exist_ok=True)

def _default_cameras():
    """
    Giá trị mặc định nếu chưa có file cameras.json.
    - cam1: giả sử ESP32-CAM thật của bạn (chỉnh lại host/user/pass cho đúng)
    - pc_cam: camera laptop kiểu mock (có thể xóa nếu bạn không muốn mặc định)
    """
    return {
        "cam1": {
            "type": "esp32",
            "host": "http://192.168.137.192",   # <-- chỉnh IP ESP32-CAM của bạn ở đây
            "username": "iot",
            "password": "123456",
            "session": requests.Session(),
            "pan_ch": 1,
            "tilt_ch": 2,
            "pan": 90,
            "tilt": 90,
            "last_login": 0.0,
        },
        # Webcam laptop giả lập ESP32 (laptop_cam_mock.py)
        "pc_cam": {
            "type": "esp32",  # để backend treat như esp32 qua HTTP mock (laptop_cam_mock có cùng API)
            "host": "http://localhost:9000",
            "username": "iot",
            "password": "123456",
            "session": requests.Session(),
            "pan_ch": 1,
            "tilt_ch": 2,
            "pan": 90,
            "tilt": 90,
            "last_login": 0.0,
        },
    }

def _cameras_to_serializable(cameras_dict: dict):
    """
    Chuẩn bị dữ liệu để ghi xuống file JSON.
    (requests.Session() không ghi thẳng được, nên ta bỏ ra / sẽ tạo lại sau)
    """
    cams_out = {}
    for cam_id, cam in cameras_dict.items():
        cams_out[cam_id] = {
            "type":         cam.get("type", "esp32"),
            "host":         cam.get("host", ""),
            "username":     cam.get("username", ""),
            "password":     cam.get("password", ""),
            "pan_ch":       cam.get("pan_ch", 1),
            "tilt_ch":      cam.get("tilt_ch", 2),
            "pan":          cam.get("pan", 90),
            "tilt":         cam.get("tilt", 90),
            "last_login":   cam.get("last_login", 0.0),
            # Nếu camera là loại đặc biệt (ví dụ local webcam),
            # bạn có thể lưu thêm "device_index"
            "device_index": cam.get("device_index", None),
        }
    return cams_out

def _cameras_from_serializable(cams_from_file: dict):
    """
    Khôi phục dict cameras từ json đã lưu:
    - tạo lại session=requests.Session() cho mỗi cam esp32/mock
    """
    restored = {}
    for cam_id, cam in cams_from_file.items():
        restored[cam_id] = {
            "type":         cam.get("type", "esp32"),
            "host":         cam.get("host", ""),
            "username":     cam.get("username", ""),
            "password":     cam.get("password", ""),
            "session":      requests.Session(),  # tạo session mới
            "pan_ch":       cam.get("pan_ch", 1),
            "tilt_ch":      cam.get("tilt_ch", 2),
            "pan":          cam.get("pan", 90),
            "tilt":         cam.get("tilt", 90),
            "last_login":   cam.get("last_login", 0.0),
            "device_index": cam.get("device_index", None),
        }
    return restored

def load_cameras():
    """
    Đọc file cameras.json nếu có, nếu không thì trả default.
    """
    if os.path.isfile(CAMERA_CONFIG_PATH):
        try:
            with open(CAMERA_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return _cameras_from_serializable(raw)
        except Exception as e:
            print("[state] Lỗi đọc cameras.json, dùng default:", e)

    # fallback: default ban đầu
    return _default_cameras()

def save_cameras(cameras_dict: dict):
    """
    Ghi cameras hiện tại xuống file cameras.json
    """
    try:
        serializable = _cameras_to_serializable(cameras_dict)
        with open(CAMERA_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("[state] Lỗi ghi cameras.json:", e)

def list_recordings():
    out = []
    for f in glob.glob(os.path.join(RECORD_DIR, "*.mp4")):
        stat = os.stat(f)
        out.append({
            "file": f,
            "size_kb": int(stat.st_size / 1024),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
        })
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out

# ---------------------------------------------------------
# SYSTEM_STATE: auth, alarm, events, cameras
# cameras sẽ load từ file
# ---------------------------------------------------------
SYSTEM_STATE = {
    "auth": {
        "user": "admin",
        "pass": "123456",
        "logged_in": False,
    },
    "alarm_enabled": True,
    "events": [],
    "cameras": load_cameras(),  # <--- QUAN TRỌNG
}
