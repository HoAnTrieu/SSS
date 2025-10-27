import requests, time, io
import streamlit as st
from PIL import Image
import requests
import io
import time
import base64
import urllib.parse


BACKEND = "http://localhost:8000"  # backend FastAPI

st.set_page_config(page_title="IoT Security Dashboard", layout="wide")

# ---- session state cho login dashboard ----
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

def do_login(user, pw):
    r = requests.post(f"{BACKEND}/api/login",
                      json={"username": user, "password": pw})
    if r.status_code == 200:
        st.session_state.logged_in = True
        st.success("Login thành công")
    else:
        st.error("Sai tài khoản hoặc mật khẩu")

def do_logout():
    requests.post(f"{BACKEND}/api/logout")
    st.session_state.logged_in = False


def get_recording_status_for_cam(backend_base, cam_id):
    """
    Hỏi backend /api/record/status rồi xem cam_id này có đang record không.
    Trả về dict:
    {
      "recording": True/False,
      "file": "...mp4" hoặc None,
      "fps": 8,
      "start_ts": "20251026_224500"
    }
    """
    try:
        r = requests.get(f"{backend_base}/api/record/status", timeout=5)
        if r.status_code != 200:
            return {"recording": False, "file": None, "fps": None, "start_ts": None}
        data = r.json()
    except Exception:
        return {"recording": False, "file": None, "fps": None, "start_ts": None}

    active_list = data.get("active_recordings", [])
    for item in active_list:
        if item.get("cam_id") == cam_id:
            return {
                "recording": True,
                "file": item.get("file"),
                "fps": item.get("fps"),
                "start_ts": item.get("start_ts")
            }

    return {"recording": False, "file": None, "fps": None, "start_ts": None}

def render_ai_frame_from_b64(b64_str):
    """
    Nhận chuỗi base64 JPEG từ backend (/api/detect_only_frame)
    -> trả về ảnh PIL.Image để st.image() dùng.
    """
    if not b64_str:
        return None
    try:
        raw = base64.b64decode(b64_str)
        return Image.open(io.BytesIO(raw))
    except Exception:
        return None

# ---- nếu chưa đăng nhập -> hiện màn hình login và dừng ----
if not st.session_state.logged_in:
    st.title("Đăng nhập hệ thống giám sát an ninh")
    user = st.text_input("Username", value="admin")
    pw = st.text_input("Password", type="password", value="123456")
    if st.button("Login"):
        do_login(user, pw)
    st.stop()

# ---- Giao diện chính sau login ----
st.sidebar.title("Menu")
page = st.sidebar.radio(
    "Chọn trang",
    ["Live & Control", "Events", "Recordings", "Camera Manager"]
)

if st.sidebar.button("Đăng xuất"):
    do_logout()
    st.rerun()

st.title("Hệ thống Giám sát An ninh IoT")





# ============================================================
# PAGE: Live & Control
# ============================================================
if page == "Live & Control":
    # lấy danh sách camera từ backend
    cams_resp = requests.get(f"{BACKEND}/api/cameras").json()
    cams = cams_resp["cameras"]

    if len(cams) == 0:
        st.error("Chưa có camera nào trong hệ thống. Vào Camera Manager để thêm.")
        st.stop()

    tab_single, tab_multi = st.tabs(["Điều khiển 1 camera", "Xem nhiều camera"])

    # --------------------------------------------------------
    # TAB 1: Điều khiển 1 camera
    # --------------------------------------------------------
    with tab_single:
        st.subheader("Điều khiển từng camera + xem trực tiếp + phát hiện người")

        cam_options = [c["cam_id"] for c in cams]
        cam_id = st.selectbox("Chọn camera", cam_options, key="single_cam_select")
        selected_cam = [c for c in cams if c["cam_id"] == cam_id][0]

        st.write(f"IP: {selected_cam['host']}")
        st.write(f"Trạng thái: {'ONLINE' if selected_cam['online'] else 'OFFLINE'}")
        st.write(f"Pan hiện tại: {selected_cam['pan']}°")
        st.write(f"Tilt hiện tại: {selected_cam['tilt']}°")

        col1, col2 = st.columns(2)

        # --------------------------------------------------------
        # KHỐI TRÁI: Xem trực tiếp + Detect Now
        # --------------------------------------------------------
        with col1:
            st.markdown("### 🔴 Xem trực tiếp (gần realtime)")

            live_mode = st.checkbox(
                "Bắt đầu xem live từ camera này",
                key="single_live_enable"
            )
            ai_live_mode = st.checkbox(
                "Bật AI detect (vẽ khung người) trong live",
                key="single_ai_live_enable"
            )
            fps_single = st.slider(
                "FPS hiển thị (tối đa 10)",
                1, 10, 5,
                key="single_fps_slider"
            )

            live_placeholder = st.empty()
            detect_status_placeholder = st.empty()  # để hiển thị trạng thái PHÁT HIỆN / OK

            if live_mode:
                delay = 1.0 / float(fps_single)
                for i in range(100):
                    if ai_live_mode:
                        # gọi detect_only_frame để lấy ảnh có vẽ bbox + kết quả AI
                        try:
                            dr = requests.get(
                                f"{BACKEND}/api/detect_only_frame/{cam_id}",
                                timeout=4
                            )
                            if dr.status_code == 200:
                                det_json = dr.json()
                                img_ai = render_ai_frame_from_b64(det_json.get("annotated_jpeg_b64"))
                                if img_ai is not None:
                                    live_placeholder.image(
                                        img_ai,
                                        caption=f"{cam_id} AI live frame {i}",
                                        width='stretch'
                                    )
                                else:
                                    live_placeholder.warning("Không decode được ảnh AI.")
                                # cập nhật trạng thái phát hiện
                                if det_json.get("note"):
                                    detect_status_placeholder.warning(det_json["note"])
                                elif det_json.get("detected"):
                                    detect_status_placeholder.warning(
                                        f"🚨 PHÁT HIỆN NGƯỜI! conf={det_json.get('max_confidence',0):.2f}"
                                    )
                                else:
                                    detect_status_placeholder.success("✅ Không phát hiện người")
                            else:
                                live_placeholder.warning("Detect API lỗi")
                        except Exception as e:
                            live_placeholder.error(f"Lỗi detect live: {e}")
                    else:
                        # chế độ live thường (không AI)
                        try:
                            frame_resp = requests.get(
                                f"{BACKEND}/api/stream_frame/{cam_id}",
                                timeout=3
                            )
                            if frame_resp.status_code == 200:
                                img_bytes = frame_resp.content
                                live_placeholder.image(
                                    Image.open(io.BytesIO(img_bytes)),
                                    caption=f"{cam_id} live frame {i}",
                                    width='stretch'
                                )
                            else:
                                live_placeholder.warning("Không lấy được frame từ camera.")
                                break
                        except Exception as e:
                            live_placeholder.error(f"Lỗi kết nối: {e}")
                            break

                    time.sleep(delay)

            st.markdown("### 👁️ Phát hiện người (Detect Now)")
            if st.button("Detect Now"):
                try:
                    r = requests.get(f"{BACKEND}/api/detect_frame/{cam_id}", timeout=5)
                except Exception as e:
                    st.error(f"Không gọi được backend detect: {e}")
                else:
                    if r.status_code != 200:
                        st.error(
                            f"Lỗi backend detect (HTTP {r.status_code}): {r.text[:200]}"
                        )
                    else:
                        # cố parse JSON
                        try:
                            det = r.json()
                        except Exception as e:
                            st.error(
                                f"Parse JSON thất bại: {e}\nTrả về: {r.text[:200]}"
                            )
                        else:
                            # --- xử lý kết quả ---
                            if det.get("note"):
                                st.warning(det["note"])

                            if det.get("detected"):
                                st.warning(
                                    f"🚨 PHÁT HIỆN NGƯỜI! conf={det.get('max_confidence',0):.2f}"
                                )
                            else:
                                st.success("✅ Không phát hiện người.")

                            # hiển thị ảnh snapshot nếu có
                            if det.get("saved_image"):
                                st.write("Ảnh snapshot:")
                                try:
                                    img_bytes = requests.get(
                                        f"{BACKEND}/api/event_image",
                                        params={"path": det["saved_image"]},
                                        timeout=5
                                    ).content
                                    st.image(
                                        Image.open(io.BytesIO(img_bytes)),
                                        caption=det["saved_image"],
                                        width='stretch'
                                    )
                                except Exception as ee:
                                    st.error(f"Không tải được ảnh snapshot: {ee}")

        # --------------------------------------------------------
        # KHỐI PHẢI: Servo + Báo động + Ghi hình
        # --------------------------------------------------------
        with col2:
            # ---- Servo ----
            st.markdown("### 🧭 Điều khiển Servo")
            new_pan = st.slider(
                "Pan (0-180°)",
                0, 180,
                selected_cam["pan"],
                key="pan_slider_single"
            )
            new_tilt = st.slider(
                "Tilt (0-180°)",
                0, 180,
                selected_cam["tilt"],
                key="tilt_slider_single"
            )

            if st.button("📡 Cập nhật góc Servo"):
                try:
                    r = requests.post(
                        f"{BACKEND}/api/servo/{cam_id}",
                        json={"pan": new_pan, "tilt": new_tilt},
                        timeout=5
                    )
                    if r.status_code == 200:
                        st.success("Đã gửi lệnh servo")
                    else:
                        st.error(f"Lỗi servo: {r.status_code} {r.text[:200]}")
                except Exception as e:
                    st.error(f"Gửi servo lỗi: {e}")

            st.divider()

            # ---- Báo động ----
            st.markdown("### 🔊 Báo động âm thanh")
            alarm_enabled_toggle = st.checkbox(
                "Bật báo động khi phát hiện người",
                value=True,
                key="alarm_toggle_single"
            )
            # Gửi trạng thái báo động về backend (best effort, không chặn UI)
            try:
                requests.post(
                    f"{BACKEND}/api/toggle_alarm",
                    json={"enabled": alarm_enabled_toggle},
                    timeout=3
                )
            except Exception:
                pass

            st.divider()

            # ---- Ghi hình thủ công ----
            st.markdown("### ⏺ Ghi hình thủ công (có timestamp)")

            # Hỏi backend xem cam này có đang ghi không
            rec_status = get_recording_status_for_cam(BACKEND, cam_id)

            if rec_status["recording"]:
                st.success(
                    f"🔴 ĐANG GHI: {rec_status['file']}\n"
                    f"{rec_status['fps']} fps | bắt đầu lúc {rec_status['start_ts']}"
                )
            else:
                st.info("🟡 Chưa ghi hình.")

            # chọn fps khi start
            rec_fps = st.slider(
                "FPS ghi hình",
                min_value=1,
                max_value=10,
                value=5,
                key="rec_fps_slider_single"
            )

            col_rec1, col_rec2 = st.columns(2)
            with col_rec1:
                if st.button("▶️ Start Recording"):
                    try:
                        r = requests.post(
                            f"{BACKEND}/api/record/start/{cam_id}",
                            json={"fps": rec_fps},
                            timeout=5
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if data.get("status") == "recording_started":
                                st.success(f"🚀 Bắt đầu ghi: {data.get('file')}")
                            elif data.get("status") == "already_recording":
                                st.warning("⚠ Camera này đang ghi rồi.")
                            else:
                                st.write(data)
                        else:
                            st.error(f"❌ Start record lỗi: {r.status_code} {r.text[:200]}")
                    except Exception as e:
                        st.error(f"Start record request fail: {e}")

            with col_rec2:
                if st.button("⏹ Stop Recording"):
                    try:
                        r = requests.post(
                            f"{BACKEND}/api/record/stop/{cam_id}",
                            timeout=5
                        )
                        if r.status_code == 200:
                            data = r.json()
                            if data.get("status") == "recording_stopped":
                                st.success(f"💾 Đã lưu file: {data.get('file')}")
                            else:
                                st.write(data)
                        else:
                            st.error(f"❌ Stop record lỗi: {r.status_code} {r.text[:200]}")
                    except Exception as e:
                        st.error(f"Stop record request fail: {e}")

            st.caption("Video .mp4 có timestamp sẽ xuất hiện ở tab 'Recordings' sau khi bạn Stop.")


    # --------------------------------------------------------
    # TAB 2: Xem nhiều camera cùng lúc
    # --------------------------------------------------------
    with tab_multi:
        st.subheader("Xem nhiều camera cùng lúc (tối đa 4 cam)")

        # danh sách camera khả dụng
        cam_choices_multi = [c["cam_id"] for c in cams]

        selected_multi = st.multiselect(
            "Chọn camera để xem:",
            cam_choices_multi,
            default=cam_choices_multi[:4],
            key="multi_cam_select"
        )

        # chỉ lấy tối đa 4 cam cho layout 2x2
        selected_multi = selected_multi[:4]

        if len(selected_multi) == 0:
            st.info("Chọn ít nhất 1 camera để xem.")
            st.stop()

        # =====================================================
        # PHẦN QUẢN LÝ GHI HÌNH HÀNG LOẠT
        # =====================================================
        st.markdown("### 🎥 Điều khiển ghi hình hàng loạt")
        col_ctrl_a, col_ctrl_b = st.columns(2)

        # Chọn fps dùng khi bấm Start Recording nhiều cam
        with col_ctrl_a:
            fps_multi_record = st.slider(
                "FPS ghi hình cho tất cả cam được chọn",
                min_value=1,
                max_value=10,
                value=5,
                key="multi_rec_fps_slider"
            )

            if st.button("▶️ Start Recording các cam đã chọn", key="multi_start_record_btn"):
                msgs = []
                for cid in selected_multi:
                    try:
                        rr = requests.post(
                            f"{BACKEND}/api/record/start/{cid}",
                            json={"fps": fps_multi_record},
                            timeout=5,
                        )
                        if rr.status_code == 200:
                            data = rr.json()
                            if data.get("status") == "recording_started":
                                msgs.append(f"{cid}: bắt đầu ghi ({data.get('file')})")
                            elif data.get("status") == "already_recording":
                                msgs.append(f"{cid}: đang ghi rồi")
                            else:
                                msgs.append(f"{cid}: {data}")
                        else:
                            msgs.append(f"{cid}: lỗi HTTP {rr.status_code}")
                    except Exception as e:
                        msgs.append(f"{cid}: lỗi kết nối {e}")

                st.write("Kết quả Start:")
                for m in msgs:
                    st.write("- " + m)

        with col_ctrl_b:
            if st.button("⏹ Stop Recording các cam đã chọn", key="multi_stop_record_btn"):
                msgs = []
                for cid in selected_multi:
                    try:
                        rr = requests.post(
                            f"{BACKEND}/api/record/stop/{cid}",
                            timeout=5,
                        )
                        if rr.status_code == 200:
                            data = rr.json()
                            if data.get("status") == "recording_stopped":
                                msgs.append(f"{cid}: dừng ghi, lưu {data.get('file')}")
                            else:
                                msgs.append(f"{cid}: {data}")
                        else:
                            msgs.append(f"{cid}: lỗi HTTP {rr.status_code}")
                    except Exception as e:
                        msgs.append(f"{cid}: lỗi kết nối {e}")

                st.write("Kết quả Stop:")
                for m in msgs:
                    st.write("- " + m)

        # Trạng thái hiện tại từng camera (có đang record không)
        st.markdown("#### Trạng thái ghi hình hiện tại")
        for cid in selected_multi:
            st_status = get_recording_status_for_cam(BACKEND, cid)
            if st_status["recording"]:
                st.success(
                    f"{cid}: 🔴 ĐANG GHI  "
                    f"({st_status['fps']} fps, file={st_status['file']}, start={st_status['start_ts']})"
                )
            else:
                st.info(f"{cid}: 🟡 Không ghi hình")

        st.divider()

        # =====================================================
        # PHẦN DETECT NGƯỜI ĐỒNG THỜI NHIỀU CAM
        # =====================================================

        st.markdown("### 🧠 Phát hiện người đồng thời trên nhiều camera")

        detect_multi_btn = st.button("🔎 Detect người trên các cam đã chọn", key="multi_detect_btn")

        if detect_multi_btn:
            # results_detect_multi là danh sách để show phía dưới
            results_detect_multi = []

            for cid in selected_multi:
                try:
                    r = requests.get(f"{BACKEND}/api/detect_frame/{cid}", timeout=5)
                except Exception as e:
                    results_detect_multi.append({
                        "cam_id": cid,
                        "ok": False,
                        "err": f"Không gọi được backend detect: {e}",
                        "det": None
                    })
                    continue

                if r.status_code != 200:
                    results_detect_multi.append({
                        "cam_id": cid,
                        "ok": False,
                        "err": f"Lỗi backend detect (HTTP {r.status_code}): {r.text[:200]}",
                        "det": None
                    })
                else:
                    # cố parse JSON
                    try:
                        det = r.json()
                    except Exception as e:
                        results_detect_multi.append({
                            "cam_id": cid,
                            "ok": False,
                            "err": f"Parse JSON thất bại: {e} | Trả về: {r.text[:200]}",
                            "det": None
                        })
                    else:
                        results_detect_multi.append({
                            "cam_id": cid,
                            "ok": True,
                            "err": None,
                            "det": det
                        })

            # render kết quả detect
            st.markdown("#### Kết quả Detect")
            for item in results_detect_multi:
                cid = item["cam_id"]
                box = st.container()
                with box:
                    st.markdown(f"**Camera `{cid}`**")
                    if not item["ok"]:
                        st.error(item["err"])
                        continue

                    det = item["det"]

                    # Thông tin note từ backend (VD: "camera error: ...")
                    note_msg = det.get("note", "")
                    if note_msg:
                        st.warning(note_msg)

                    # Người?
                    if det.get("detected"):
                        st.warning(
                            f"🚨 PHÁT HIỆN NGƯỜI! conf={det.get('max_confidence',0):.2f}"
                        )
                    else:
                        st.success("✅ Không phát hiện người.")

                    # show snapshot có bounding box
                    if det.get("saved_image"):
                        try:
                            img_bytes = requests.get(
                                f"{BACKEND}/api/event_image",
                                params={"path": det["saved_image"]},
                                timeout=5
                            ).content
                            st.image(
                                Image.open(io.BytesIO(img_bytes)),
                                caption=f"{cid} snapshot {det['saved_image']}",
                               width='stretch'
                            )
                        except Exception as ee:
                            st.error(f"Không tải được ảnh snapshot: {ee}")

        st.divider()

        # =====================================================
        # PHẦN XEM LIVE / SNAPSHOT NHIỀU CAM
        # =====================================================
        st.markdown("### 👀 Xem nhiều cam (live / snapshot)")

        fps_multi = st.slider(
            "FPS hiển thị tất cả cam (tối đa 10)",
            1, 10, 5,
            key="multi_fps_slider"
        )
        live_multi = st.checkbox(
            "🔴 Bắt đầu xem live đồng thời",
            key="multi_live_enable"
        )
        ai_multi_live = st.checkbox(
            "Bật AI detect (vẽ khung người) trong live nhiều cam",
            key="multi_ai_live_enable"
        )

        st.caption("Tip: nếu tắt live, bạn có thể chụp snapshot 1 lần bằng nút bên dưới.")
        refresh_multi = st.button(
            "📸 Chụp snapshot tất cả cam (1 lần)",
            key="multi_snapshot_btn"
        )

        # Chuẩn bị layout 2x2
        colA, colB = st.columns(2)
        colC, colD = st.columns(2)
        grid_cols = [colA, colB, colC, colD]

        # placeholders cho từng ô
        frame_placeholders = [c.empty() for c in grid_cols]
        info_placeholders = [c.empty() for c in grid_cols]
        detect_placeholders = [c.empty() for c in grid_cols]  # trạng thái AI từng cam

        def render_one_cam(i_slot: int, cam_id_slot: str, frame_idx: int = None, just_once=False):
            # tìm info camera
            cam_info_slot_list = [c for c in cams if c["cam_id"] == cam_id_slot]
            if not cam_info_slot_list:
                info_placeholders[i_slot].warning(f"{cam_id_slot}: không tìm thấy trong danh sách.")
                return
            cam_info_slot = cam_info_slot_list[0]

            cam_host = cam_info_slot["host"]
            cam_online = cam_info_slot["online"]

            # header mô tả (cam id + ip + trạng thái)
            caption_header = (
                f"**{cam_id_slot}**  \n"
                f"`{cam_host}` - {'ONLINE' if cam_online else 'OFFLINE'}"
            )
            info_placeholders[i_slot].markdown(caption_header)

            # nếu bật AI live
            if ai_multi_live:
                try:
                    dr = requests.get(
                        f"{BACKEND}/api/detect_only_frame/{cam_id_slot}",
                        timeout=4
                    )
                    if dr.status_code == 200:
                        det_json = dr.json()

                        # decode annotated frame
                        img_ai = render_ai_frame_from_b64(det_json.get("annotated_jpeg_b64"))
                        if img_ai is not None:
                            cap_text = f"{cam_id_slot} AI live"
                            if frame_idx is not None:
                                cap_text = f"{cam_id_slot} AI live frame {frame_idx}"
                            frame_placeholders[i_slot].image(
                                img_ai,
                                caption=cap_text,
                                width='stretch'
                            )
                        else:
                            frame_placeholders[i_slot].warning("Không decode được ảnh AI.")

                        # trạng thái phát hiện người
                        if det_json.get("note"):
                            detect_placeholders[i_slot].warning(det_json["note"])
                        elif det_json.get("detected"):
                            detect_placeholders[i_slot].warning(
                                f"🚨 Người! conf={det_json.get('max_confidence',0):.2f}"
                            )
                        else:
                            detect_placeholders[i_slot].success("✅ Không phát hiện người")
                    else:
                        frame_placeholders[i_slot].warning("Detect API lỗi")
                except Exception as e:
                    frame_placeholders[i_slot].error(f"Lỗi detect live: {e}")
                    if just_once:
                        return

            else:
                # chế độ live thường (không AI)
                try:
                    fr = requests.get(
                        f"{BACKEND}/api/stream_frame/{cam_id_slot}",
                        timeout=3
                    )
                    if fr.status_code == 200:
                        img_b = fr.content
                        cap_text = f"{cam_id_slot} snapshot"
                        if frame_idx is not None:
                            cap_text = f"{cam_id_slot} live frame {frame_idx}"
                        frame_placeholders[i_slot].image(
                            Image.open(io.BytesIO(img_b)),
                            caption=cap_text,
                            width='stretch'
                        )
                    else:
                        frame_placeholders[i_slot].warning("Không lấy được frame.")
                except Exception as e:
                    frame_placeholders[i_slot].error(f"Lỗi kết nối: {e}")
                    if just_once:
                        return

        # Snapshot one-shot
        if refresh_multi and not live_multi:
            for idx, cam_id_slot in enumerate(selected_multi):
                render_one_cam(idx, cam_id_slot, frame_idx=None, just_once=True)

        # Live loop
        if live_multi:
            delay_multi = 1.0 / float(fps_multi)
            for frame_idx in range(100):  # ~100 frame cho mỗi lần bật
                for idx, cam_id_slot in enumerate(selected_multi):
                    render_one_cam(idx, cam_id_slot, frame_idx=frame_idx, just_once=False)
                time.sleep(delay_multi)

# ============================================================
# PAGE: Events
# ============================================================
elif page == "Events":
    st.subheader("Sự kiện phát hiện người")
    ev_resp = requests.get(f"{BACKEND}/api/events").json()
    events = ev_resp["events"]

    if not events:
        st.info("Chưa có sự kiện nào.")
    else:
        for ev in events:
            with st.expander(
                f"[{ev['ts']}] Cam {ev['cam_id']}  conf={ev['confidence']:.2f}"
            ):
                img_bytes = requests.get(
                    f"{BACKEND}/api/event_image",
                    params={"path": ev["img_path"]}
                ).content
                st.image(
                    Image.open(io.BytesIO(img_bytes)),
                    caption=ev["img_path"],
                    width='stretch'
                )

# ============================================================
# PAGE: Recordings
# ============================================================
elif page == "Recordings":
    st.subheader("📼 Video đã ghi")

    # lấy danh sách file từ backend
    rec_resp = requests.get(f"{BACKEND}/api/recordings").json()
    recs = rec_resp.get("recordings", [])

    if len(recs) == 0:
        st.info("Chưa có video ghi hình nào.")
        st.stop()

    # Chọn 1 file để xem
    # recs: bạn đang trả dạng list các dict/paths? 
    # Giả sử mỗi phần tử là path tuyệt đối hoặc tương đối. 
    # Ta sẽ hiển thị tên file = os.path.basename()
    file_display_names = []
    for r in recs:
        # nếu recordings là chuỗi path thì dùng luôn r,
        # còn nếu là dict {"file": "..."} thì adapt lại
        if isinstance(r, dict) and "file" in r:
            fpath = r["file"]
        else:
            fpath = str(r)
        file_display_names.append(fpath)

    chosen_file = st.selectbox(
        "Chọn video để xem:",
        options=file_display_names,
        index=0
    )

    # Nút tải về
    dl_url = f"{BACKEND}/api/download_video?file={urllib.parse.quote(chosen_file)}"
    st.markdown(f"[⬇️ Tải video]({dl_url})")

    # Phát trực tiếp trên trang
    preview_url = f"{BACKEND}/api/preview_video?file={urllib.parse.quote(chosen_file)}"
    st.markdown("### ▶️ Xem trực tiếp")
    st.video(preview_url)

    st.markdown("---")
    st.write("Danh sách tất cả video có sẵn:")
    for f in file_display_names:
        st.write("-", f)


# ============================================================
# PAGE: Camera Manager (thêm / xóa camera)
# ============================================================
elif page == "Camera Manager":
    st.subheader("Quản lý Camera ESP32-CAM")

    st.markdown("#### Danh sách camera hiện có")
    data_full = requests.get(f"{BACKEND}/api/cameras_full").json()
    cams = data_full["cameras"]

    if not cams:
        st.info("Hiện chưa có camera nào trong hệ thống.")
    else:
        for cam in cams:
            st.markdown(
                f"- **{cam['cam_id']}**  "
                f"(type: `{cam.get('type','esp32')}`)  "
                f"(IP: `{cam.get('host','')}`)  "
                f"User: `{cam.get('username','')}`  "
                f"Pass: `{cam.get('password','')}`  "
                f"(pan={cam.get('pan',0)} tilt={cam.get('tilt',0)})"
            )

    st.markdown("---")

    # --- Form thêm camera mới ---
    st.markdown("### ➕ Thêm camera mới (ESP32-CAM hoặc laptop_cam)")
    with st.form("add_cam_form"):
        new_cam_id = st.text_input("Camera ID (ví dụ: cam2, cam3, pc_cam)")
        new_host = st.text_input("Địa chỉ host/IP (ví dụ: http://192.168.137.192 hoặc http://localhost:9000)")
        new_user = st.text_input("Username đăng nhập camera")
        new_pass = st.text_input("Password đăng nhập camera", type="password")
        submitted = st.form_submit_button("Thêm camera")

        if submitted:
            if not new_cam_id or not new_host or not new_user or not new_pass:
                st.error("Thiếu trường thông tin.")
            else:
                resp = requests.post(
                    f"{BACKEND}/api/add_camera",
                    json={
                        "cam_id": new_cam_id,
                        "host": new_host,
                        "username": new_user,
                        "password": new_pass
                    }
                )
                if resp.status_code == 200:
                    st.success(f"Đã thêm camera {new_cam_id}")
                    st.rerun()
                else:
                    st.error(f"Lỗi khi thêm camera: {resp.text}")

    st.markdown("---")

    # --- Form xóa camera ---
    st.markdown("### ❌ Xóa camera")
    if cams:
        cam_choices = [c["cam_id"] for c in cams]
        cam_to_remove = st.selectbox("Chọn camera để xóa", cam_choices)
        if st.button("Xóa camera này"):
            resp = requests.post(
                f"{BACKEND}/api/remove_camera",
                json={"cam_id": cam_to_remove}
            )
            if resp.status_code == 200:
                st.success(f"Đã xóa {cam_to_remove}")
                st.rerun()
            else:
                st.error(f"Lỗi khi xóa camera: {resp.text}")
    else:
        st.info("Không có camera nào để xóa.")
