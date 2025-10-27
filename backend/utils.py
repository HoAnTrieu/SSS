# backend/utils.py
import os
import time
import platform
import cv2
import subprocess
from pathlib import Path

from .state import EVENT_DIR  # EVENT_DIR là string path từ state.py


def save_event_image(cam_id: str, frame) -> str:
    """
    Lưu 1 frame BGR (numpy array) xuống thư mục EVENT_DIR
    với tên dạng camID_timestamp.jpg
    Trả về đường dẫn (string) tới file vừa lưu.
    """
    os.makedirs(EVENT_DIR, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{cam_id}_{ts}.jpg"
    filepath = os.path.join(EVENT_DIR, filename)

    try:
        cv2.imwrite(filepath, frame)
    except Exception as e:
        print(f"[save_event_image] Lỗi ghi ảnh: {e}")
        return ""

    return filepath


def _try_spawn(command_list):
    """
    Helper nhỏ: chạy lệnh phát âm thanh dạng non-blocking.
    Không raise lỗi nếu fail.
    """
    try:
        subprocess.Popen(
            command_list,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"[alarm] spawn fail for {command_list}: {e}")
        return False


def play_alarm_sound():
    """
    Phát âm báo động khi phát hiện người.

    - Windows:
        dùng winsound.Beep với pattern hú ngắn.
    - Raspberry Pi / Linux:
        1. Nếu tìm thấy file alarm.wav trong thư mục backend (hoặc project root),
           dùng aplay/paplay để phát file đó (không chặn luồng chính).
        2. Nếu không có âm thanh .wav, thử beep tần số bằng 'play' (sox).
        3. Cuối cùng fallback: gửi bell '\a'.
    """
    try:
        system_name = platform.system()

        if system_name == "Windows":
            try:
                import winsound
                # pattern hú nghe rõ hơn 3 tiếng beep
                winsound.Beep(1400, 180)
                winsound.Beep(1000, 180)
                winsound.Beep(1400, 250)
            except Exception as e:
                print(f"[alarm] winsound error: {e}")
            return

        # Linux / Raspberry Pi
        # -------------------------------------------------
        # 1. Tìm file alarm.wav (bạn tự bỏ file còi hú vô đây)
        #    gợi ý: đặt alarm.wav tại project_root/backend/alarm.wav
        #           hoặc project_root/alarm.wav
        possible_paths = [
            Path(__file__).parent / "alarm.wav",
            Path(__file__).resolve().parents[1] / "alarm.wav",
        ]
        wav_path = None
        for p in possible_paths:
            if p.is_file():
                wav_path = str(p)
                break

        if wav_path:
            # Ưu tiên 'aplay', nếu không có thì 'paplay'
            if _try_spawn(["aplay", wav_path]):
                return
            if _try_spawn(["paplay", wav_path]):
                return
            # Nếu cả 2 không có thì rơi xuống fallback tiếp

        # 2. Không có wav hoặc không chạy được aplay?
        #    thử dùng 'play' (lệnh sox) tạo tone hú
        #    (nhiều bản Raspbian có thể cài sẵn sox hoặc bạn có thể `sudo apt install sox`)
        if _try_spawn(["play", "-nq", "-t", "alsa", "synth", "0.4", "sin", "1500"]):
            return

        # 3. Fallback cuối: bell ký tự ASCII. Có thể im lặng, nhưng không crash.
        #    (chạy trong nền để không block, nhưng thật ra echo kiểu này nhanh nên không đáng kể)
        _try_spawn(["/bin/sh", "-c", "printf '\\a'"])

    except Exception as e:
        print(f"[play_alarm_sound] cảnh báo âm thanh lỗi: {e}")
