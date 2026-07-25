# OCR Plate – ứng dụng nhận diện biển số xe máy

Ứng dụng desktop đọc video, webcam hoặc camera RTSP. Detector chạy trên **toàn khung hình** rồi chỉ giữ lại biển **giao với vùng ROI ở giữa**, nên biển ở sát đáy khung (xe gần camera nhất) không còn bị cắt cụt như khi crop cứng theo ROI.

## Tính năng hiện tại

- Nhận một hoặc nhiều nguồn video/camera.
- Hiển thị nhiều camera dạng lưới.
- ROI giữa màn hình, điều chỉnh được chiều rộng và chiều cao.
- Phát hiện biển bằng model `license_plate_detector.pt`.
- OCR bằng PaddleOCR.
- Xác nhận một biển qua nhiều khung hình trước khi ghi sự kiện.
- Sau khi xác nhận, cache biển đang hiện diện và không chạy OCR lại cho đến khi xe rời ROI.
- Chống ghi trùng cùng biển trong khoảng cooldown.
- Lưu ảnh sự kiện và SQLite trong thư mục `data/`.
- Xem sự kiện gần nhất và xuất CSV.
- Gán vai trò `IN`/`OUT` cho từng camera và tự ghép thời gian xe vào/ra.
- Xem xe đang ở trong, lịch sử lượt và các trường hợp cần kiểm tra.
- Thanh trạng thái dưới cùng hiển thị trực tiếp: trạng thái chạy, số camera, tổng sự kiện, tốc độ nhận diện (lượt/giây) và **trạng thái barrier**. Bảng sự kiện/lượt được tô màu theo kết quả cổng và INSIDE/COMPLETED/REVIEW.
- Quản lý xe đăng ký (**whitelist/blacklist**) trong tab **Vehicles**, quyết định mở/đóng cổng theo hai chế độ và tính **phí gửi xe** theo thời gian.

## Chế độ cổng & phí (tab Vehicles)

- **Chế độ `all` (bãi tính phí):** mọi xe không nằm blacklist đều được vào; xe đăng ký hiện `MỞ`, xe lạ hiện `Khách`. Khi ra, hệ thống tính phí = `parking_flat_fee` + `parking_hourly_fee` × số giờ vượt `parking_free_minutes`.
- **Chế độ `registered_only` (kiểm soát ra-vào):** chỉ xe trong whitelist (còn hạn) mới mở cổng; xe lạ hoặc hết hạn bị `TỪ CHỐI`.
- Barrier hiện được **mô phỏng bằng phần mềm** (`SimulatedGate`): sự kiện được phép sẽ "mở cổng" trong `gate_open_seconds` giây rồi tự đóng, thể hiện ở thanh trạng thái. Muốn gắn phần cứng thật (relay USB/TCP, Arduino…) chỉ cần viết một lớp con của `GateController` trong [gate.py](plate_app/gate.py) — phần còn lại không phải sửa.
- Cột **Gate** ở tab Events và cột **Phí/TT** ở tab Visits ghi lại quyết định và số tiền cho từng lượt.

## Thanh toán & doanh thu (mô phỏng)

- Khi xe ra, lượt được tính phí và đánh dấu `Nợ` (khách) hoặc `Miễn` (xe đăng ký/không mất phí).
- Chọn lượt trong tab Visits rồi bấm **Tiền mặt** hoặc **QR** để thu tiền → chuyển sang `✓ Đã thu`.
- Nhãn **Doanh thu hôm nay** cập nhật tổng tiền đã thu và số lượt chưa thu. Xuất CSV kèm cột `payment_status`, `paid_at`.

## Mô phỏng barrier trực quan

Khu **Camera view** có bảng *Mô phỏng barrier*: thanh chắn nâng/hạ động và đèn tín hiệu đỏ/xanh phản ánh đúng trạng thái `SimulatedGate` theo thời gian thực (mở khi cho phép, đỏ + giữ đóng khi từ chối). Đây là bản mô phỏng phần mềm để chạy thử toàn bộ luồng khi chưa gắn phần cứng.

## Sửa lỗi OCR biển Việt Nam

`correct_vietnamese_plate` sửa nhầm lẫn chữ↔số theo vị trí (2 số đầu là mã tỉnh, ký tự thứ 3 là chữ series, 5 số cuối là số đăng ký) — ví dụ `S9X3O2345 → 59X302345`. Chỉ chạy khi chuỗi đã gần đủ một biển hoàn chỉnh nên không "bịa" biển từ nhiễu OCR.

## Vận hành như một sản phẩm

- **Barrier phần cứng:** đặt `gate_backend` = `simulated` (mặc định) / `tcp` / `serial`. Với `tcp` khai báo `gate_host`+`gate_port`; với `serial` khai báo `gate_serial_port`+`gate_baudrate`. Lệnh gửi là `gate_command` (mặc định `OPEN`). Hardware và mô phỏng chạy song song qua `CompositeGate` — barrier trực quan vẫn hoạt động kể cả khi thiết bị offline. Lỗi kết nối không làm sập app (giữ ở `last_error`).
- **Thu tiền QR (VietQR):** đặt `bank_bin` + `bank_account` + `bank_account_name`. Nút **QR** ở tab Visits mở mã VietQR đúng số tiền để khách quét, bấm *Xác nhận đã thu* để chốt. Cần gói `qrcode` để hiện ảnh (nếu thiếu sẽ hiện chuỗi payload).
- **Tài khoản & phân quyền:** đặt `require_login: true` để bắt đăng nhập (mặc định `admin` / `admin` — đổi ngay khi triển khai). `admin` toàn quyền; `operator` không được backup/xóa dữ liệu/quản lý tài khoản. Nút **👤 Tài khoản** để thêm/xóa người dùng.
- **Báo cáo:** nút **📊 Báo cáo** hiện doanh thu theo ngày + nhật ký hệ thống (audit: đăng nhập, mở/từ chối cổng, thu tiền, backup, xóa dữ liệu).
- **Sao lưu:** nút **💾 Backup** tạo bản sao `data/backups/events_YYYYMMDD_HHMMSS.db` bằng SQLite backup API (an toàn khi đang chạy).
- **Tự khởi động & giám sát:** `auto_start: true` tự chạy nhận diện khi mở app; camera mất tín hiệu quá `camera_alert_seconds` giây sẽ hiện **⚠ MẤT KẾT NỐI** trên khung camera.

Cài thêm (tùy chọn) cho QR và cổng serial: `pip install qrcode pyserial`.

## Chạy nhanh

```powershell
python app.py
```

Trong ứng dụng:

1. Chọn **Add video** để thử với file video; hoặc nhập `0` rồi chọn **Add camera/RTSP** để dùng webcam đầu tiên.
   Có thể chọn nhanh một file trong danh sách **Sample**, chọn vai trò `IN`/`OUT`, rồi nhấn **Add sample** để chạy nhiều video cùng lúc. Nút **Replace** thay toàn bộ nguồn hiện tại bằng sample đang chọn.
2. Với camera IP, nhập URL dạng `rtsp://user:password@ip:554/...`.
3. Điều chỉnh ROI. Với một làn xe máy, nên bắt đầu ở mức rộng 70%, cao 65%.
4. Chọn **Start**. Lần đầu PaddleOCR khởi tạo có thể mất một lúc.

Có thể truyền nhiều nguồn ngay từ dòng lệnh:

```powershell
python app.py --source video1.mp4 --source video2.mp4
python app.py --source 0 --source "rtsp://user:password@192.168.1.20:554/stream"
```

Để tạo cấu hình cố định, sao chép `config.example.json` thành `config.json` rồi sửa danh sách `cameras`. Các nguồn thêm từ giao diện cũng được lưu vào `config.json`.

## Các tham số quan trọng

- `roi_width`, `roi_height`: tỷ lệ vùng ROI ở chính giữa ảnh. ROI giờ là **cổng lọc** (giữ biển giao với vùng này) chứ không cắt ảnh trước khi detect, nên có thể để rộng mà không tốn thêm chi phí detect. Với làn cố định, thu hẹp ROI để bỏ qua biển ở làn/hậu cảnh không mong muốn.
- `detection_imgsz`: cạnh ảnh đưa vào YOLO (640/768/960/1280). Lớn hơn = bắt biển nhỏ/ở xa tốt hơn nhưng chậm hơn; mặc định 960. Chỉnh nhanh bằng ô **Img size** trên thanh công cụ.
- `frame_skip`: chỉ nhận diện mỗi N frame để giảm tải.
- `preview_fps`: FPS hiển thị mục tiêu, độc lập với tốc độ nhận diện; mặc định 20 FPS.
- `detection_interval_seconds`: khoảng nghỉ giữa hai lần detect trên cùng camera; mặc định 0,5 giây và luôn xử lý frame mới nhất.
- `ocr_recognition_model`: mặc định dùng `PP-OCRv6_medium_rec` để đọc biển hai dòng chính xác hơn. Có thể đổi giữa Medium và Tiny trong giao diện; Tiny nhanh hơn nhưng dễ nhầm ký tự trên biển nhỏ.
- `min_votes`: số lần OCR giống nhau trước khi tạo sự kiện.
- `vote_window_seconds`: khoảng thời gian gom phiếu OCR.
- `duplicate_cooldown_seconds`: không ghi lại cùng biển trên cùng camera trong khoảng này.
- `recognized_cache_seconds`: thời gian tối đa giữ kết quả OCR đã xác nhận của một xe đang trong ROI.
- `track_max_missed_frames`: số frame detector được phép hụt trước khi coi xe đã rời ROI.
- `max_plates_per_frame`: số biển tối đa xử lý trong một frame.
- `cameras[].direction`: vai trò camera, chỉ nhận `IN` hoặc `OUT`. Có thể đổi ngay trong bảng Sources bằng nút **Apply**.
- `cameras[].start_delay_seconds`: thời gian chờ trước khi bắt đầu đọc nguồn. Khi mô phỏng bằng cùng một video, đặt camera `OUT` trễ 15 giây để tạo khoảng cách giữa lượt vào và lượt ra.

Preview đọc trực tiếp frame mới nhất của mỗi camera nên vẫn chạy ở `preview_fps` trong khi AI xử lý nền theo `detection_interval_seconds`. Nếu máy yếu khi mở nhiều camera, giảm `preview_fps` xuống 15. Nếu xe chạy nhanh và thường không đủ hai phiếu OCR, giảm `detection_interval_seconds` hoặc tạm đặt `min_votes` bằng 1 để khảo sát, nhưng không nên dùng cấu hình một phiếu khi điều khiển barrier.

## Dữ liệu đầu ra

- `data/events.db`: cơ sở dữ liệu SQLite.
- `data/snapshots/`: ảnh bằng chứng khi một biển được xác nhận.
- CSV được tạo khi chọn **Export CSV**.
- Nút **Clear saved plates** trong tab Events xóa toàn bộ sự kiện, lượt vào/ra và snapshot sau khi xác nhận; cấu hình camera được giữ nguyên.

Thư mục `data/` (database + snapshot) và `__pycache__/` là dữ liệu sinh ra lúc chạy, đã được đưa vào `.gitignore`. Nếu các file này đang bị Git theo dõi từ trước, gỡ khỏi index một lần (giữ nguyên file trên đĩa) bằng:

```powershell
git rm -r --cached data __pycache__ plate_app/__pycache__
```

SQLite gồm ba bảng chính: `cameras`, `plate_events` và `vehicle_visits`. Sự kiện `IN` mở một lượt `INSIDE`; sự kiện `OUT` đóng lượt thành `COMPLETED`. Nếu chỉ có `OUT` mà không tìm thấy lần vào, lượt được đánh dấu `REVIEW` thay vì bị bỏ qua. Database cũ được tự nâng cấp khi khởi động ứng dụng.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python -m compileall app.py plate_app tests
```

## Lưu ý trước khi dùng trong doanh nghiệp

Phiên bản hiện tại vẫn dùng Ultralytics và model hiện có trong workspace. Cần rà soát giấy phép Ultralytics hoặc chuyển detector trước khi đóng gói thương mại. Không đưa mật khẩu RTSP thật vào repository; nên tách secret ra khỏi `config.json` khi triển khai chính thức.
