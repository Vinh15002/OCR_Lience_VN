# Kiến trúc kỹ thuật OCR Plate

Tài liệu dành cho lập trình viên, người bảo trì và đơn vị tích hợp camera/barrier/thanh toán. Nội dung phản ánh mã nguồn hiện tại, không phải kiến trúc mục tiêu trong tương lai.

## 1. Tổng quan

OCR Plate là ứng dụng desktop Tkinter chạy cục bộ. Một tiến trình quản lý giao diện, luồng camera, AI, SQLite, barrier và các kết nối thanh toán.

```text
File / Webcam / RTSP
        │
        ▼
CameraStream (một thread cho mỗi nguồn, giữ frame mới nhất)
        │
        ▼
MultiCameraProcessor (một worker xử lý các camera)
        │
        ▼
YOLO detector → lọc ROI → PaddleOCR → sửa biển Việt Nam
        │
        ▼
ConsensusTracker + cache/cooldown
        │
        ▼
EventStore.record()
  ├─ lưu snapshot và plate_events
  ├─ ghép vehicle_visits IN/OUT
  ├─ quyết định ALLOW/GUEST/DENY
  └─ tính phí khi OUT
        │
        ▼
Queue → Tk main thread
  ├─ bảng và preview
  ├─ barrier
  ├─ dialog thanh toán
  └─ báo cáo

SePay/Casso polling ─┐
MoMo create/query ───┼─→ Tk main thread → EventStore → UI
                     ┘
```

## 2. Cấu trúc mã nguồn

| Thành phần | Trách nhiệm |
| --- | --- |
| `app.py` | Entry point chạy source, gọi `plate_app.ui.main()`. |
| `plate_app/ui.py` | Dựng UI, điều phối camera/AI, nghiệp vụ, barrier, thanh toán, ca và báo cáo. |
| `plate_app/config.py` | Dataclass cấu hình, đọc/ghi JSON, dựng tariff/bank/MoMo client. |
| `plate_app/video.py` | Đọc webcam/RTSP/file, reconnect và loop video. |
| `plate_app/recognition.py` | YOLO, PaddleOCR CPU, normalize/correct plate, tracking, voting, processor đa camera. |
| `plate_app/storage.py` | SQLite repository, snapshot, ghép lượt, đăng ký xe, user, audit, ca, backup/export/retention. |
| `plate_app/parking.py` | Quyết định truy cập, loại xe và công thức phí thuần. |
| `plate_app/gate.py` | Interface và backend simulated/TCP/serial/composite. |
| `plate_app/payment.py` | Danh mục 20 ngân hàng và tạo VietQR EMVCo cục bộ. |
| `plate_app/bankfeed.py` | Client SePay/Casso, normalize và ghép giao dịch. |
| `plate_app/momo.py` | Tạo chữ ký HMAC, tạo/query giao dịch MoMo. |
| `plate_app/auth.py` | Hash mật khẩu và role. |
| `plate_app/analytics.py` | Truy vấn/tổng hợp, CSV nhiều section và PDF ReportLab có biểu đồ. |
| `plate_app/charts.py` | Biểu đồ Tk Canvas responsive. |
| `plate_app/fileops.py` | Mở file bằng ứng dụng mặc định theo hệ điều hành. |
| `packaging/launcher.py` | Entry point PyInstaller, log, seed OCR model và `--self-test`. |
| `packaging/OCR_Plate.spec` | Thu thập thư viện native/model cho bản onedir. |
| `packaging/build_exe.ps1` | Build và stage config/model/video/runtime directory. |

## 3. Khởi động và kết thúc

### Chạy source

`app.py` gọi parser trong `ui.main()`:

```powershell
python app.py [--config config.json] [--source URI]...
```

`--source` có thể lặp và thay danh sách camera đã đọc từ JSON trong bộ nhớ.

### Trình tự khởi động

1. Tính kích thước cửa sổ theo màn hình và tải cấu hình.
2. Tạo `EventStore`; khởi tạo/migrate SQLite và seed `admin/admin` nếu bảng user rỗng.
3. Khôi phục ca đang mở, dựng barrier và UI.
4. Đồng bộ camera JSON vào bảng `cameras`.
5. Tải sự kiện/lượt gần nhất và áp retention.
6. Khởi động bank feed nếu provider/token đủ cấu hình.
7. Hiện đăng nhập nếu `require_login=true`.
8. Nếu `auto_start=true`, bắt đầu nhận dạng sau khoảng 900 ms.

Khi đóng: lưu các thiết lập UI, dừng bank-feed, dừng processor/camera và hủy cửa sổ.

### Bản đóng gói

Launcher đổi working directory về thư mục chứa EXE, chuyển stdout/stderr vào `logs/app.log`, chép model OCR đóng gói vào cache PaddleX rồi gọi cùng `ui.main()`.

## 4. Mô hình đồng thời

| Thread | Số lượng | Giao tiếp |
| --- | --- | --- |
| Tk main thread | 1 | Sở hữu mọi widget và phần lớn thao tác nghiệp vụ/DB. |
| `CameraStream` | 1 mỗi camera enabled | Cập nhật frame mới nhất và sequence dưới lock. |
| `MultiCameraProcessor` | 1 | Đọc snapshot nguồn, chạy AI, đẩy `ProcessedFrame`/`ProcessorStatus` vào queue. |
| Bank feed | 0 hoặc 1 | Poll API, dùng `self.after()` chuyển matching/UI về Tk thread. |
| MoMo workers | Theo dialog/query | Gọi HTTP nền rồi dùng `after()` trả kết quả về UI. |

`EventStore` mở kết nối SQLite riêng mỗi thao tác, bật foreign keys và WAL. Các đường ghi quan trọng dùng một `threading.Lock`. Không gọi trực tiếp Tk widget từ worker thread.

## 5. Pipeline camera và nhận dạng

### Nguồn video

`parse_video_source()` xử lý chuỗi toàn số không trùng đường dẫn như webcam index; file/URL khác được chuyển cho OpenCV. Camera live lỗi sẽ thử mở lại sau khoảng một giây.

Khi file hết:

- `loop_video=false`: trạng thái `finished`;
- `loop_video=true`: mở lại từ đầu sau nhịp reconnect.

`start_delay_seconds` trì hoãn trước lần mở đầu, phù hợp mô phỏng IN và OUT bằng video.

### Detector và ROI

- YOLO dự đoán trên toàn frame với `detection_confidence` và `detection_imgsz`.
- ROI là hình chữ nhật ở giữa theo `roi_width/roi_height`.
- Chỉ hộp có giao ROI mới đi tiếp; các hộp gần tâm ROI được ưu tiên.
- Số hộp xử lý giới hạn bởi `max_plates_per_frame`.

### OCR và chuẩn hóa

- Crop biển được phóng khi nhỏ.
- Biển có tỷ lệ gần vuông được tách hai dòng trước OCR.
- Paddle `TextRecognition` dùng `device="cpu"` để bản portable không phụ thuộc CUDA/cuDNN.
- Kết quả bỏ ký tự ngoài A–Z/0–9, sửa một số nhầm chữ/số theo vị trí, rồi kiểm tra dạng biển Việt Nam hợp lý.

### Consensus, track và cooldown

- `ConsensusTracker` yêu cầu `min_votes` cùng chuỗi trong `vote_window_seconds`.
- Cooldown theo `(camera_id, plate)` tránh ghi trùng trong `duplicate_cooldown_seconds`.
- Track đã nhận dạng được cache để không OCR lại liên tục; bị bỏ khi quá số frame mất dấu hoặc hết thời gian cache.
- Chỉ sự kiện đã xác nhận mới ghi ảnh annotated snapshot và DB.

Preview đọc frame mới nhất độc lập với chu kỳ AI, nên `preview_fps` và `detection_interval_seconds` có thể tinh chỉnh riêng.

## 6. Quyết định truy cập và ghép lượt

### Quyết định cổng

Thứ tự trong `decide_access()`:

1. Xe có `access=DENY` luôn bị từ chối.
2. Xe `ALLOW`, active và trong khoảng ngày hiệu lực được cho phép.
3. Xe lạ/hết hạn trở thành `GUEST` ở policy `all`, hoặc `DENY` ở `registered_only`.

Ngày bắt đầu/kết thúc có hiệu lực theo ngày lịch và bao gồm cả hai đầu.

### Máy trạng thái lượt

```text
không có lượt + IN  → INSIDE
INSIDE + IN lặp     → vẫn INSIDE, event mới gắn cùng lượt
INSIDE + OUT        → COMPLETED → EXEMPT hoặc UNPAID
không có lượt + OUT → REVIEW(no_entry)
```

Unique partial index bảo đảm tối đa một lượt `INSIDE` cho mỗi biển. Lượt `REVIEW` thiếu IN không tự ghép lại khi sau đó nhập một IN mới.

### Cờ đối soát

- `low_confidence`: confidence thấp nhất của hai đầu dưới 0.6.
- `short_stay`: thời lượng dưới 30 giây.
- `manual`: đầu vào của lượt được ghi tay.
- `no_entry`: OUT không có lượt IN.

Ghi tay không có ảnh, được audit và không tự gọi `_handle_gate()`.

## 7. Tính phí

`Tariff._time_fee()` tính:

1. cộng `flat_fee`;
2. trừ `free_minutes` khỏi thời lượng trước khi tính phần giờ;
3. làm tròn lên theo giờ bắt đầu;
4. nếu có `daily_cap`, giới hạn phần giờ theo số khoảng 24 giờ bắt đầu;
5. cộng `overnight_fee` cho mỗi mốc `night_hour` nằm sau entry và trước exit;
6. làm tròn tổng về đồng.

`TariffTable` chọn biểu phí theo `vehicle_type`, còn trường override thiếu kế thừa biểu phí chung. Xe `ALLOW` hợp lệ và mọi lượt phí 0 được `EXEMPT`; khách có phí dương là `UNPAID`.

## 8. Barrier

`build_gate()` luôn dựng `SimulatedGate`. Nếu backend là TCP/serial, nó thêm backend thật vào `CompositeGate`; vì thế simulator vẫn phản hồi khi phần cứng lỗi.

- TCP mở một socket cho mỗi lệnh, gửi `<command>\n`.
- Serial mở cổng cho mỗi lệnh, gửi `<command>\n` rồi đóng.
- Exception phần cứng được giữ ở `last_error`, không làm sập app.
- `SimulatedGate` hết trạng thái mở sau `gate_open_seconds`.
- Hết thời gian mô phỏng không tự gọi `close()` cho backend thật.

Event `ALLOW` hoặc `GUEST` ở cả chiều IN/OUT gọi mở cổng. `DENY` chỉ tạo cảnh báo/audit. Nút thủ công gọi trực tiếp `open()`/`close()` và không kiểm tra quyền truy cập hoặc thanh toán.

## 9. Thanh toán

### Trạng thái

- `UNPAID`: UI **Chưa thanh toán**.
- `PAID`: UI **Đã thu**.
- `EXEMPT`: UI **Miễn**.

`mark_paid()` chỉ cập nhật lượt `COMPLETED` chưa `PAID`, nhờ đó bấm lặp không thu hai lần. Không có chức năng hoàn tiền/reopen payment trong UI.

### Tiền mặt

Ghi `payment_method=CASH`, `paid_at`, `collected_by`, `shift_id` và audit `PAYMENT_CASH`.

### VietQR và feed ngân hàng

Payload tạo cục bộ; mở dialog ghi `bank_payment_request:<visit_id>` vào `app_state`. Bank worker đọc SePay/Casso, main thread bỏ transaction đã xử lý, lấy pending, áp `match_requested()` rồi ghi `BANK`/reference/audit.

Matching ưu tiên mã `GX<id>`; nếu không có mã chỉ ghép số tiền chính xác khi duy nhất. Transaction phải không cũ hơn lúc QR mở và phải có timestamp parse được.

SePay client hiện dùng legacy v1 `/userapi/transactions/list`; Casso dùng `/v2/transactions` với `Apikey`.

### MoMo

Client ký HMAC-SHA256, tạo `captureWallet`, rồi UI query mỗi 5 giây. Chỉ response `resultCode=0` và đủ tiền mới ghi `MOMO`. Hiện không có IPN server; URL callback là placeholder và polling là nguồn xác nhận tại app.

Xem chi tiết tại [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md).

## 10. Schema SQLite

File mặc định: `data/events.db`.

| Bảng | Dữ liệu chính |
| --- | --- |
| `plate_events` | Camera, biển, confidence, thời gian, snapshot, IN/OUT, visit, kết quả/lý do truy cập, AUTO/MANUAL. |
| `cameras` | ID, tên, chiều, URI, enabled, delay và thời điểm sync. `loop_video` chỉ nằm trong JSON. |
| `vehicle_visits` | Event vào/ra, trạng thái, thời lượng, phí/thanh toán, loại xe, cờ, người thu, ca, note/reference. |
| `app_state` | Key/value cho cursor feed, mốc mở QR và transaction đã xử lý. |
| `registered_vehicles` | Whitelist/blacklist, chủ/SĐT, loại xe, hiệu lực và active. |
| `shifts` | Người trực, thời điểm, quỹ đầu, tiền đếm/kỳ vọng và ghi chú. |
| `subscriptions` | Lịch sử vé tháng, số tháng, tiền, hiệu lực, người bán và ca. |
| `users` | Username, password hash, role và active. |
| `audit_log` | Timestamp, username, action và detail. |

Foreign key khai báo trực tiếp chủ yếu từ `vehicle_visits.entry_event_id/exit_event_id` tới `plate_events`. Các liên kết visit/shift/plate khác là liên kết logic.

Migration dùng kiểm tra `PRAGMA table_info` và `ALTER TABLE ADD COLUMN`; chưa có bảng version hay framework migration.

## 11. Dữ liệu, sao lưu và retention

```text
data/
├─ events.db
├─ snapshots/*.jpg
└─ backups/events_YYYYMMDD_HHMMSS.db
```

- Snapshot chỉ sinh cho sự kiện tự động xác nhận thành công.
- Backup dùng SQLite backup API và chỉ chứa DB; không gồm ảnh, config, model, log.
- Retention xóa lượt không `INSIDE` cũ hơn cutoff, sau đó xóa event không còn được tham chiếu và file snapshot tương ứng.
- Xe đang trong bãi luôn được giữ.
- Retention không xóa xe đăng ký, subscription, shift, user, audit hoặc app state.
- **Xóa toàn bộ dữ liệu nhận dạng** chỉ xóa `vehicle_visits`, `plate_events`, reset sequence và xóa snapshots; các bảng nghiệp vụ khác vẫn còn.

Không chạy nhiều instance ghi cùng DB và không đặt DB trên ổ mạng/đồng bộ cloud.

## 12. Báo cáo

`analytics.DateRange` bao gồm ngày đầu và cuối. Lượt được đưa vào kỳ theo `exit_at`; event theo `detected_at`.

Các nhóm dữ liệu:

- KPI doanh thu đã thu/chưa thu, IN/OUT, thời gian, công suất và chất lượng;
- doanh thu theo ngày, payment mix, thu theo user;
- traffic theo giờ/thứ, histogram thời gian;
- top plate, lý do từ chối, vé tháng và shifts.

CSV báo cáo là một file UTF-8 BOM gồm nhiều section. PDF dùng ReportLab, có bốn biểu đồ (doanh thu ngày, lưu lượng giờ, phương thức, thời gian gửi) rồi các bảng chi tiết. `fileops.open_with_default_app()` mở file theo Windows/macOS/Linux.

## 13. Giới hạn và điểm cần lưu ý

Các mục dưới đây là hành vi/thiếu sót hiện tại cần biết trước khi cam kết nghiệp vụ:

1. **Barrier OUT mở trước thanh toán.** `_handle_gate()` chạy trước `_maybe_open_exit_payment()`; payment không khóa cổng.
2. **Đối soát ca phân loại sai `BANK`/`MOMO`.** `shift_totals()` chỉ coi method đúng bằng `QR` là không tiền mặt; hai method đang dùng bị cộng vào tiền mặt phải có.
3. **Thu theo nhân viên bỏ sót ngân hàng/MoMo.** Analytics chỉ đọc audit `PAYMENT_CASH`/`PAYMENT_QR`, còn runtime ghi `PAYMENT_BANK`/`PAYMENT_MOMO`.
4. **Backup không phải bản sao lưu đầy đủ.** Ảnh, config, model và log không nằm trong file DB backup.
5. **CSV sự kiện có lệch header.** Query `SELECT *` có cột `source`, nhưng header export hiện chưa mô tả đủ tất cả cột được ghi.
6. **Cursor bank feed dùng chung.** `bank_feed_since_id` không namespace theo provider; đổi SePay/Casso có thể dùng cursor không tương thích.
7. **Chỉ giao dịch sau lúc mở QR mới được xét.** Đây là cơ chế chống replay, nhưng giao dịch khách chuyển trước khi dialog mở không tự khớp.
8. **Secret lưu plaintext trong JSON.** Bao gồm RTSP password, payment token và MoMo secret.
9. **Lỗi barrier thật chưa hiện trên UI.** Backend giữ `last_error`, simulator vẫn có thể trông như hoạt động bình thường.
10. **Một ca toàn hệ thống.** Không hỗ trợ ca độc lập theo nhiều chốt/máy.
11. **Báo cáo có vài phạm vi khác nhau.** Bảng ca UI lấy 30 ca mới nhất và audit lấy 200 dòng mới nhất, không theo khoảng ngày đang chọn; `currently_inside` là trạng thái toàn cục tại thời điểm xem.
12. **Không có logout/đổi user trong phiên.** Phải đóng/mở lại để đăng nhập người khác.
13. **Không có refund.** UI không đảo một lượt đã thu hoặc quản lý hoàn tiền.

Các mục 1–3 ảnh hưởng trực tiếp vận hành và quyết toán, nên ưu tiên sửa trước triển khai thương mại.

## 14. Authentication và audit

- Mật khẩu dùng PBKDF2-HMAC-SHA256, salt ngẫu nhiên, 200.000 vòng.
- Role: `admin`, `operator`; không cho xóa admin active cuối cùng.
- UI chỉ bắt admin cho quản lý user, backup, purge và xóa recognition data.
- Operator vẫn có thể sửa camera, phí, registry, barrier và payment settings.
- Khi login tắt, `current_user=None` vượt qua `_require_admin()`.

Audit action quan trọng gồm LOGIN, GATE_OPEN/GATE_DENY, GATE_MANUAL, MANUAL_IN/OUT, PAYMENT_CASH/BANK/MOMO, SHIFT_OPEN/CLOSE, BACKUP, PURGE và các chỉnh sửa lượt.

## 15. Kiểm thử và kiểm tra tĩnh

Chạy từ thư mục project:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py plate_app tests
```

Self-test bản đóng gói:

```powershell
.\OCR_Plate.exe --self-test
Get-Content -Encoding UTF8 .\logs\selftest.log
```

`SELFTEST PASSED` chỉ chứng minh detector và OCR nạp/chạy được. Nó không kiểm tra camera thật, DB write, VietQR, provider, MoMo, PDF hay barrier.

Khi thay đổi logic nghiệp vụ, cần bổ sung test tối thiểu cho:

- state transition IN/OUT/REVIEW;
- access decision và tariff;
- payment idempotence, mốc request và giao dịch mơ hồ;
- shift totals theo mọi phương thức;
- export CSV/PDF;
- config round-trip và camera validation.

## 16. Điểm mở rộng

- Backend barrier mới: triển khai `GateController.open/close/is_open`, rồi thêm vào `build_gate()`.
- Provider ngân hàng mới: chuẩn hóa response về `BankTransaction`, cung cấp `fetch()` và thêm vào `build_feed()`.
- Báo cáo mới: viết truy vấn trong `analytics.py`, đưa section vào `_report_sections()` và UI nếu cần.
- Migration lớn: nên thêm schema version và transaction migration thay cho chuỗi `ALTER TABLE` rời rạc.
- Triển khai nhiều chốt: cần chuyển persistence và lock nghiệp vụ khỏi SQLite cục bộ sang dịch vụ trung tâm; không chỉ chia sẻ file DB.

