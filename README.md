🔐 Security Camera Dashboard (Raspberry Pi + ESP32-CAM)

Hệ thống giám sát an ninh nội bộ gồm:

Nhiều camera ESP32-CAM có pan/tilt (hoặc webcam giả lập),

Raspberry Pi 4 làm server xử lý,

Backend FastAPI để cung cấp API & xử lý AI,

Dashboard Streamlit để giám sát trực quan.

Mục tiêu: triển khai một giải pháp giám sát cục bộ (LAN), không cần cloud, có thể mở rộng nhiều camera.

📦 1. Yêu cầu phần cứng

Raspberry Pi 4 (khuyến nghị 4GB RAM trở lên).

1 đến 4 module ESP32-CAM có gắn cụm servo pan/tilt
→ Hoặc bạn có thể dùng camera laptop / USB webcam + script giả lập API ESP32-CAM để test.

Router / Wi-Fi nội bộ
Raspberry Pi và các ESP32-CAM phải cùng mạng LAN (cùng subnet).

🧰 2. Yêu cầu phần mềm

Python 3.11

Có thể chạy trực tiếp trên Raspberry Pi.

Hoặc chạy thử trên Windows (môi trường dev/test).

Các thư viện Python chính:

fastapi

uvicorn

streamlit

requests

opencv-python

ultralytics

pillow

numpy

click (dùng cho CLI tiện ích)

🏗 3. Chuẩn bị môi trường (virtual environment)
3.1. Vào thư mục dự án
cd <đường_dẫn_thư_mục_dự_án>


Đây là thư mục gốc của repo (chứa backend/, ui_app/, data/, v.v.).

3.2. Tạo virtual environment
python -m venv venv


Lệnh trên sẽ tạo thư mục venv/ để cô lập thư viện Python cho dự án.

3.3. Kích hoạt môi trường ảo

Trên Linux / Raspberry Pi OS:

source venv/bin/activate


Trên Windows PowerShell:

venv\Scripts\activate


Khi kích hoạt thành công, terminal của bạn sẽ hiện prefix kiểu (venv) ở đầu dòng lệnh.

3.4. Cài thư viện cần thiết
python -m pip install fastapi uvicorn click streamlit requests opencv-python ultralytics pillow numpy


Nếu pip quá cũ, có thể nâng cấp:

python -m pip install --upgrade pip

🚀 4. Chạy Backend (FastAPI)

Backend chịu trách nhiệm:

Load danh sách camera từ file data/cameras.json

Xuất các REST API để Dashboard đọc stream/video/trạng thái

Ghi log sự kiện khi phát hiện người / xâm nhập

Từ thư mục gốc dự án (vẫn đang ở trong venv), chạy:

python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload


Giải thích nhanh tham số:

--host 0.0.0.0 → cho phép máy khác trong LAN truy cập API

--port 8000 → cổng backend

--reload → tự reload khi code thay đổi (tiện cho dev)

Sau khi chạy thành công:

API sẽ sống tại http://<IP_RaspberryPi>:8000

Ví dụ khi dev local trên chính Pi có thể gọi:
http://localhost:8000/docs để xem tài liệu Swagger tự động.

🖥 5. Chạy Dashboard (Streamlit UI)

Dashboard là giao diện web để:

Xem live feed từ từng camera

Theo dõi trạng thái kết nối

Xem log / cảnh báo phát hiện người

Các bước:

Mở một terminal mới (để backend vẫn chạy ở terminal cũ).

Vào thư mục gốc dự án và kích hoạt lại venv (nếu cần).

Chạy lệnh:

python -m streamlit run ui_app/app.py --server.address 0.0.0.0 --server.port 8501


Streamlit sẽ khởi động web UI tại:

Trên máy local/Pi:
http://localhost:8501

Từ máy khác trong cùng mạng LAN (ví dụ laptop của bạn):
http://<IP_RaspberryPi>:8501

Ví dụ nếu Pi có IP 192.168.1.50 thì bạn truy cập từ laptop bằng http://192.168.1.50:8501.

📂 6. Cấu trúc thư mục gợi ý

Bạn có thể tổ chức repo như sau (tham khảo):

security_UI/
├─ backend/
│  ├─ __init__.py
│  ├─ server.py        # FastAPI app (biến app = FastAPI(...))
│  ├─ detectors.py     # AI / YOLO / logic phát hiện người
│  ├─ models.py        # Kiểu dữ liệu trả ra API (pydantic)
│  └─ utils.py         # Hàm tiện ích (ghi log, format frame,...)
│
├─ ui_app/
│  ├─ app.py           # Streamlit dashboard
│  └─ components/      # (tuỳ chọn) các block UI tái sử dụng
│
├─ data/
│  ├─ cameras.json     # Danh sách camera (IP, tên, góc quay, ... )
│  └─ events.log       # Log sự kiện phát hiện người
│
├─ venv/               # Virtual env (không commit lên Git, thêm vào .gitignore)
│
├─ README.md
└─ requirements.txt    # (khuyến nghị) danh sách thư viện pip


Gợi ý file requirements.txt để dễ cài đặt lại môi trường:

fastapi
uvicorn
click
streamlit
requests
opencv-python
ultralytics
pillow
numpy


Khi đó người dùng mới chỉ cần:

python -m pip install -r requirements.txt

🛠 Troubleshooting nhanh

ModuleNotFoundError: No module named 'uvicorn'
→ Bạn quên cài uvicorn hoặc quên kích hoạt venv.
Chạy lại:

source venv/bin/activate
python -m pip install uvicorn


Không xem được dashboard từ máy khác trong LAN

Kiểm tra Raspberry Pi firewall.

Kiểm tra router có chặn client-to-client isolation không.

Nhớ dùng IP thật của Pi chứ không phải localhost.

YOLO / ultralytics quá nặng trên Raspberry Pi

Bạn có thể tắt model nặng và chỉ stream hình ảnh thô.

Hoặc chạy AI inference ở máy mạnh hơn và chỉ gửi kết quả về Pi.

📜 License

(Điền license dự án của bạn ở đây, ví dụ MIT, Apache-2.0,...)

🙌 Credit / Tác giả

Hệ thống được phát triển cho bài toán giám sát an ninh cục bộ (LAN), sử dụng Raspberry Pi làm server thu thập và dashboard hiển thị real-time, hỗ trợ nhiều camera ESP32-CAM pan/tilt.
