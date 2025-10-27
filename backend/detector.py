# backend/detector.py

import time
import cv2
import numpy as np

class BaseDetector:
    """
    Interface chung để server.py dùng.
    Các class con phải có:
      - detect_person(frame_bgr) -> list[ {bbox:[x1,y1,x2,y2], conf:float} ]
      - annotate(frame_bgr, boxes) -> frame_bgr_annotated
    """
    def detect_person(self, frame_bgr):
        raise NotImplementedError

    def annotate(self, frame_bgr, boxes):
        # Vẽ khung và score lên ảnh để lưu log / hiển thị sự kiện
        out = frame_bgr.copy()
        for b in boxes:
            (x1, y1, x2, y2) = b["bbox"]
            conf = b["conf"]
            color = (0, 0, 255)  # đỏ BGR
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

            label = f"person {conf:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(out, (x1, y1 - th - 4), (x1 + tw + 4, y1), color, -1)
            cv2.putText(out, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1, cv2.LINE_AA)
        return out


class MockDetector(BaseDetector):
    """
    Fallback khi không có YOLO / torch (ví dụ Raspberry Pi yếu).
    Luôn trả về [] (không phát hiện), để hệ thống vẫn chạy ổn định.
    """
    def __init__(self):
        print("[MockDetector] Using MOCK detector (no torch/ultralytics).")

    def detect_person(self, frame_bgr):
        # không detect thật, luôn trả empty
        return []


class YoloDetector(BaseDetector):
    """
    YOLOv8n chạy thật bằng ultralytics.
    """
    def __init__(self, model_path="yolov8n.pt", conf_thres=0.6):
        from ultralytics import YOLO  # import ở đây để tránh ImportError khi module ko tồn tại
        self.model = YOLO(model_path)
        self.conf_thres = conf_thres
        self.person_class_id = 0  # class "person" trong COCO

        # In ra info để debug
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"
        print(f"[YoloDetector] Loaded {model_path} (conf={conf_thres}) on {device}")

    def detect_person(self, frame_bgr):
        """
        frame_bgr: numpy array BGR (OpenCV)
        YOLO muốn RGB.
        Trả list box [{'bbox':[x1,y1,x2,y2], 'conf':float}, ...] chỉ cho class 'person'.
        """
        img_rgb = frame_bgr[..., ::-1]

        # chạy model
        t0 = time.time()
        results = self.model.predict(
            img_rgb,
            classes=[self.person_class_id],  # chỉ người
            conf=self.conf_thres,
            verbose=False
        )
        infer_time = (time.time() - t0) * 1000.0  # ms
        # bạn có thể in ra infer_time để đo tốc độ

        boxes_out = []
        # results là list, mỗi phần tử là 1 frame
        for r in results:
            # r.boxes là tensor kết quả cho frame này
            for b in r.boxes:
                # xyxy shape: [1,4]
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                conf = float(b.conf[0].item())
                boxes_out.append({
                    "bbox": [int(x1), int(y1), int(x2), int(y2)],
                    "conf": conf
                })
        return boxes_out


def build_detector():
    """
    Tự động chọn YOLO thật nếu có ultralytics + torch OK.
    Nếu lỗi import / lỗi GPU / lỗi kiến trúc (Pi không cài được torch),
    dùng MockDetector để hệ thống không sập.
    """
    try:
        # thử import ultralytics trước
        import ultralytics  # noqa: F401
        # thử import torch (nhiều khi ultralytics có nhưng torch fail trên Pi)
        import torch  # noqa: F401
    except Exception as e:
        print(f"[build_detector] Fallback to MockDetector ({e})")
        return MockDetector()

    # Nếu import ok thì dùng YOLOv8n
    try:
        return YoloDetector(model_path="yolov8n.pt", conf_thres=0.6)
    except Exception as e:
        # nếu model load lỗi thì vẫn fallback
        print(f"[build_detector] YOLO init failed, fallback to MockDetector ({e})")
        return MockDetector()
