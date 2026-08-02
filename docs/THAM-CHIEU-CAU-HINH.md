# Tham chiếu `config.json`

Ứng dụng mặc định đọc `config.json` trong thư mục làm việc. Bản EXE tự chuyển thư mục làm việc về nơi chứa `OCR_Plate.exe`; bản chạy source dùng thư mục nơi bạn gọi lệnh.

Tạo cấu hình ban đầu:

```powershell
Copy-Item .\config.example.json .\config.json
```

Không chèn chú thích `//` vào JSON. Sau khi sửa tay, nên chạy ứng dụng từ console để phát hiện lỗi cú pháp trước khi vận hành.

## 1. Cách đọc và lưu cấu hình

- Nếu file không tồn tại, ứng dụng dùng giá trị mặc định trong `AppConfig`; không tự sao chép file mẫu.
- Khóa cấp cao không được code nhận biết sẽ bị bỏ qua khi đọc và biến mất ở lần lưu sau.
- Mỗi phần tử `cameras` có trường lạ sẽ gây lỗi khi tải.
- `direction` chỉ nhận `IN` hoặc `OUT`; giá trị khác làm ứng dụng không khởi động.
- `start_delay_seconds` được ép tối thiểu về 0.
- Nhiều thao tác trong thẻ **Cài đặt** lưu file ngay. Đóng ứng dụng cũng gọi lưu cấu hình.
- Tham số dòng lệnh `--source` thay danh sách camera trong bộ nhớ; khi ứng dụng lưu/đóng, danh sách thay thế đó có thể được ghi vào file cấu hình.

Chạy với file khác:

```powershell
python app.py --config .\config.demo.json
```

Thêm một hay nhiều nguồn tạm từ CLI:

```powershell
python app.py --source 0 --source "rtsp://user:password@192.168.1.20:554/stream1"
```

## 2. Model và nhận dạng

| Khóa | Kiểu / mặc định code | Ý nghĩa | Sửa trên UI |
| --- | --- | --- | --- |
| `model_path` | string / `license_plate_detector.pt` | Đường dẫn trọng số YOLO phát hiện biển. | Không |
| `roi_width` | float / `0.70` | Tỷ lệ ngang của ROI giữa ảnh, từ 0 đến 1. File mẫu dùng `0.8`. | Có, 20–100% |
| `roi_height` | float / `0.65` | Tỷ lệ dọc của ROI giữa ảnh. File mẫu dùng `0.82`. | Có, 20–100% |
| `detection_confidence` | float / `0.35` | Ngưỡng confidence YOLO. | Không |
| `detection_imgsz` | int / `960` | Kích thước ảnh đầu vào YOLO. | Có: 640/768/960/1280 |
| `ocr_confidence` | float / `0.30` | Ngưỡng điểm OCR tối thiểu. | Không |
| `ocr_recognition_model` | string / `PP-OCRv6_medium_rec` | Model nhận dạng PaddleOCR. | Có: medium/tiny |
| `frame_skip` | int / `3` | Chỉ dùng làm nhịp bỏ frame nếu `detection_interval_seconds <= 0`; UI không cho đặt trường hợp này. | Không |
| `preview_fps` | int / `20` | Giới hạn tốc độ preview giao diện. | Không |
| `detection_interval_seconds` | float / `0.5` | Khoảng tối thiểu giữa hai lần AI xử lý một camera. | Có, 0.1–5 giây |
| `min_votes` | int / `2` | Số lần OCR đồng thuận trước khi tạo sự kiện. | Không |
| `vote_window_seconds` | float / `2.5` | Khoảng thời gian gom phiếu. | Không |
| `duplicate_cooldown_seconds` | float / `10` | Chống ghi lại cùng biển trên cùng camera sau khi xác nhận. | Không |
| `recognized_cache_seconds` | float / `30` | Thời gian giữ cache biển của track đã nhận dạng. | Không |
| `track_max_missed_frames` | int / `2` | Số chu kỳ mất hộp trước khi bỏ track. | Không |
| `max_plates_per_frame` | int / `1` | Số hộp biển tối đa được OCR trong một frame. | Không |

YOLO chạy trên toàn khung hình; ROI dùng để lọc các hộp có giao với vùng giữa, không crop ảnh trước khi detector chạy.

Khuyến nghị vận hành:

- Giữ `min_votes >= 2` khi nối barrier thật.
- Tăng `detection_imgsz` giúp biển nhỏ nhưng tốn CPU/GPU hơn.
- Giảm `detection_interval_seconds` giúp bắt xe nhanh nhưng tăng tải.
- `PP-OCRv6_medium_rec` chính xác hơn; `tiny` nhẹ hơn. PaddleOCR trong code hiện bị ép chạy CPU.

## 3. Camera và video

Mỗi camera có cấu trúc:

```json
{
  "id": "gate-in",
  "name": "Cong vao",
  "uri": "0",
  "enabled": true,
  "loop_video": false,
  "direction": "IN",
  "start_delay_seconds": 0.0
}
```

| Khóa | Bắt buộc | Ý nghĩa |
| --- | --- | --- |
| `id` | Có | Định danh duy nhất, ổn định; dùng trong DB và cooldown. |
| `name` | Có | Tên hiển thị trên camera, sự kiện và báo cáo. |
| `uri` | Có | Webcam (`"0"`), đường dẫn file, HTTP video hoặc RTSP URL. |
| `enabled` | Không | `true` để khởi động nguồn khi bấm Bắt đầu. |
| `loop_video` | Không | Chỉ có ý nghĩa với file: chạy lại từ đầu khi hết. |
| `direction` | Không | `IN` hoặc `OUT`; mặc định `IN`. |
| `start_delay_seconds` | Không | Chờ trước khi mở nguồn; hữu ích khi mô phỏng OUT. |

Với RTSP có credential, URL thường chứa mật khẩu và được lưu rõ trong JSON. Dùng tài khoản camera chỉ có quyền xem, giới hạn mạng truy cập và không đưa file này vào Git.

## 4. Chính sách cổng và thời gian mở

| Khóa | Kiểu / mặc định | Giá trị |
| --- | --- | --- |
| `open_gate_policy` | string / `all` | `all`: cho mọi xe không blacklist; `registered_only`: chỉ xe ALLOW còn hiệu lực. |
| `gate_open_seconds` | float / `4` | Thời gian trạng thái mở của barrier mô phỏng. |

`gate_open_seconds` không đảm bảo relay vật lý tự đóng. Backend TCP/serial chỉ gửi lệnh khi code gọi `open()` hoặc `close()`; bộ điều khiển thật nên hỗ trợ pulse/tự nhả, cảm biến an toàn và nút dừng khẩn cấp.

## 5. Biểu phí

| Khóa | Kiểu / mặc định | Ý nghĩa |
| --- | --- | --- |
| `parking_flat_fee` | number / `0` | Phí cố định cộng một lần mỗi lượt. |
| `parking_hourly_fee` | number / `0` | Phí cho mỗi giờ bắt đầu sau thời gian miễn phí. |
| `parking_free_minutes` | int / `0` | Số phút không tính phần phí giờ. |
| `parking_daily_cap` | number / `0` | Trần cho riêng phần phí giờ trên mỗi 24 giờ bắt đầu; 0 là không trần. |
| `parking_overnight_fee` | number / `0` | Phụ phí cho mỗi mốc giờ đêm đi qua. |
| `parking_night_hour` | int / `22` | Giờ bắt đầu một đêm, từ 0 đến 23. |
| `parking_capacity` | int / `0` | Sức chứa dùng cho KPI; 0 là không biết. Không chặn xe khi đầy. |
| `default_vehicle_type` | string / `MOTORBIKE` | Loại mặc định cho khách/ghi tay. |
| `parking_tariffs` | object / `{}` | Override biểu phí theo loại xe. |

Công thức:

```text
phí = phí cố định
    + min(phí giờ × số giờ bắt đầu, trần ngày × số ngày bắt đầu)
    + phí qua đêm × số mốc giờ đêm đã đi qua
```

Nếu `parking_daily_cap=0`, không áp trần. Trần không giới hạn phí cố định hoặc phí qua đêm. Kết quả được làm tròn về đồng.

Các loại xe: `MOTORBIKE`, `CAR`, `BICYCLE`. Override chỉ cần ghi trường muốn thay; trường thiếu kế thừa biểu phí chung:

```json
"parking_tariffs": {
  "CAR": {
    "flat_fee": 10000,
    "hourly_fee": 5000,
    "daily_cap": 60000,
    "overnight_fee": 30000
  },
  "BICYCLE": {
    "flat_fee": 2000
  }
}
```

UI hiện chỉ cho nhập `flat_fee`, `hourly_fee`, `daily_cap` riêng cho ô tô và xe đạp. Muốn override `free_minutes`, `overnight_fee` hoặc `night_hour`, sửa JSON trực tiếp.

## 6. Vé tháng

`monthly_ticket_fees` là giá một tháng theo loại xe:

```json
"monthly_ticket_fees": {
  "MOTORBIKE": 100000,
  "CAR": 800000,
  "BICYCLE": 50000
}
```

Giá đề xuất khi bán = giá một tháng × số tháng. Nhân viên vẫn có thể sửa số tiền trên UI. Bán vé tạo/cập nhật xe thành `ALLOW`; gia hạn sớm cộng tiếp từ ngày sau hạn hiện có.

## 7. Backend barrier

| Khóa | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `gate_backend` | `simulated` | `simulated`, `tcp` hoặc `serial`. |
| `gate_host` | rỗng | IP/hostname thiết bị TCP. |
| `gate_port` | `8000` | Cổng TCP. |
| `gate_serial_port` | rỗng | Ví dụ `COM3`. |
| `gate_baudrate` | `9600` | Baud của cổng serial. |
| `gate_command` | `OPEN` | Chuỗi gửi khi mở. Code thêm newline. |
| `gate_close_command` | `CLOSE` | Chuỗi gửi khi đóng. Code thêm newline. |

Ví dụ TCP:

```json
{
  "gate_backend": "tcp",
  "gate_host": "192.168.1.50",
  "gate_port": 8000,
  "gate_command": "OPEN",
  "gate_close_command": "CLOSE"
}
```

Ví dụ serial:

```json
{
  "gate_backend": "serial",
  "gate_serial_port": "COM3",
  "gate_baudrate": 9600,
  "gate_command": "OPEN",
  "gate_close_command": "CLOSE"
}
```

Mô phỏng luôn chạy song song qua `CompositeGate`, kể cả khi chọn TCP/serial. Lỗi phần cứng không làm app sập nhưng UI hiện chưa hiển thị `last_error` của backend.

## 8. VietQR và bank feed

| Khóa | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `bank_bin` | rỗng | BIN NAPAS 6 chữ số; UI lấy từ dropdown ngân hàng. |
| `bank_account` | rỗng | Số tài khoản nhận, tối đa 19 ký tự chữ/số. |
| `bank_account_name` | rỗng | Tên hiển thị dưới QR. |
| `payment_provider` | `none` | `none`, `sepay` hoặc `casso`. |
| `payment_api_token` | rỗng | API Token/API Key đọc giao dịch; không phải username/password. |
| `payment_poll_seconds` | `20` | Chu kỳ poll; runtime ép tối thiểu 5 giây. |

Ví dụ:

```json
{
  "bank_bin": "970436",
  "bank_account": "0123456789",
  "bank_account_name": "CONG TY ABC",
  "payment_provider": "sepay",
  "payment_api_token": "REPLACE_WITH_SECRET",
  "payment_poll_seconds": 20
}
```

Xem điều kiện ghép và cách lấy đúng credential tại [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md).

## 9. MoMo

| Khóa | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `momo_partner_code` | rỗng | Partner Code merchant. |
| `momo_access_key` | rỗng | Access Key cùng môi trường. |
| `momo_secret_key` | rỗng | Secret Key HMAC. |
| `momo_environment` | `sandbox` | `sandbox` hoặc `production`. Giá trị khác cũng rơi về URL sandbox trong client. |

Không dùng thông tin đăng nhập ví cá nhân. Không commit ba trường này. Luôn nghiệm thu sandbox trước production.

## 10. Vận hành và dữ liệu

| Khóa | Kiểu / mặc định | Ý nghĩa |
| --- | --- | --- |
| `retention_days` | int / `0` | 0 giữ mãi; lớn hơn 0 tự dọn dữ liệu cũ khi mở app. |
| `require_login` | bool / `false` | Bắt đăng nhập khi khởi động. |
| `auto_start` | bool / `false` | Tự bấm Bắt đầu khoảng 0,9 giây sau khi mở nếu có camera. |
| `camera_alert_seconds` | float / `6` | Thời gian không có frame trước khi UI báo mất kết nối. |
| `data_dir` | string / `data` | Nơi lưu SQLite, snapshots và backups. |

Với `require_login=false`, phiên không có user được phép làm các thao tác admin. Khi triển khai thật nên đặt:

```json
"require_login": true
```

DB mới luôn có `admin/admin`; đổi ngay mật khẩu bằng **Cài đặt → Tài khoản**.

Đặt `data_dir` trên ổ cục bộ có đủ dung lượng. Không đặt SQLite trên thư mục OneDrive/Dropbox, USB rút nóng hoặc ổ mạng đồng bộ.

## 11. Cấu hình mẫu đầy đủ rút gọn

```json
{
  "model_path": "license_plate_detector.pt",
  "roi_width": 0.8,
  "roi_height": 0.82,
  "detection_confidence": 0.35,
  "detection_imgsz": 960,
  "ocr_confidence": 0.3,
  "ocr_recognition_model": "PP-OCRv6_medium_rec",
  "detection_interval_seconds": 0.5,
  "min_votes": 2,
  "open_gate_policy": "all",
  "gate_open_seconds": 4,
  "parking_flat_fee": 5000,
  "parking_hourly_fee": 0,
  "parking_capacity": 100,
  "gate_backend": "simulated",
  "bank_bin": "",
  "bank_account": "",
  "bank_account_name": "",
  "payment_provider": "none",
  "payment_api_token": "",
  "momo_partner_code": "",
  "momo_access_key": "",
  "momo_secret_key": "",
  "momo_environment": "sandbox",
  "require_login": true,
  "auto_start": false,
  "retention_days": 90,
  "data_dir": "data",
  "cameras": [
    {
      "id": "gate-in",
      "name": "Cổng vào",
      "uri": "0",
      "enabled": true,
      "loop_video": false,
      "direction": "IN",
      "start_delay_seconds": 0
    }
  ]
}
```

Giữ các trường còn lại từ [`config.example.json`](../config.example.json) khi tạo file triển khai thực tế.

## 12. Dữ liệu nhạy cảm

Các trường sau lưu plaintext: `cameras[].uri`, `payment_api_token`, `momo_access_key`, `momo_secret_key`, thông tin tài khoản ngân hàng và địa chỉ barrier. Phân quyền file hệ điều hành, không gửi file nguyên bản khi hỗ trợ kỹ thuật và luôn thay bằng giá trị giả trong tài liệu/log công khai.

