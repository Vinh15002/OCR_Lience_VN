# OCR Plate – nhận diện biển số và quản lý bãi xe

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
- Xem sự kiện gần nhất, xuất dữ liệu thô CSV và xuất báo cáo tổng hợp CSV/PDF có biểu đồ.
- Gán vai trò `IN`/`OUT` cho từng camera và tự ghép thời gian xe vào/ra.
- Xem xe đang ở trong, lịch sử lượt và các trường hợp cần kiểm tra.
- Thanh trạng thái dưới cùng hiển thị trực tiếp: trạng thái chạy, số camera, tổng sự kiện, tốc độ nhận diện (lượt/giây) và **trạng thái barrier**. Bảng sự kiện/lượt được tô màu theo kết quả cổng và INSIDE/COMPLETED/REVIEW.
- Quản lý xe đăng ký (**whitelist/blacklist**) trong thẻ **Đăng ký xe**, quyết định mở/đóng cổng theo hai chế độ và tính **phí gửi xe** theo thời gian.
- **Đối soát ảnh xe vào / xe ra** cho từng lượt, tự gắn cờ lượt đáng ngờ.
- **Thao tác thủ công**: mở cổng, ghi lượt vào/ra bằng tay, sửa biển đọc sai, đổi loại xe.
- **Vé tháng** theo loại xe: bán, gia hạn cộng dồn, cảnh báo sắp hết hạn.
- **Ca trực**: mở/đóng ca và đối soát tiền mặt cuối ca.
- **Tự dọn dữ liệu cũ** theo số ngày cấu hình.
- Giao diện co giãn theo màn hình; các bảng có thanh cuộn ngang/dọc, vùng camera/cài đặt/báo cáo dài có thể cuộn và các panel chính kéo thay đổi kích thước được.

## Đối soát xe vào / xe ra (chống tráo xe)

Đây là bước vận hành nên bắt buộc tại bãi: khi xe ra, nhân viên cần nhìn lại ảnh lúc xe vào. Phần mềm gắn cờ và cung cấp cửa sổ so ảnh nhưng hiện không ép phải xác nhận trước khi mở cổng.

- Bấm đúp một lượt trong tab **Bãi xe** (hoặc nút **🔍 Đối soát**) để mở cửa sổ hai ảnh **XE VÀO / XE RA** cạnh nhau, kèm giờ, cổng, biển đọc được và độ tin cậy từng lần.
- Hệ thống tự gắn cờ những lượt đáng ngờ ở cột **Đối soát**: `Đọc biển yếu` (một trong hai lần OCR dưới 60%), `Gửi quá ngắn` (dưới 30 giây), `Ghi tay`, `Không có lượt vào`. Dòng có cờ được tô cam.
- Xác nhận đúng xe bằng nút **✓ Đúng xe, bỏ cảnh báo** — thao tác được ghi vào nhật ký (`VISIT_VERIFIED`).

## Thao tác thủ công tại chốt

Camera không bao giờ đọc đúng 100%, nên mọi tình huống đều có đường thoát thủ công:

- **🚧 Mở barrier / ⛔ Đóng barrier** trên thanh trên cùng: điều khiển cổng ngay và ghi audit tương ứng.
- **Ghi thủ công** trong tab Bãi xe: nhập biển + loại xe rồi bấm **Ghi xe VÀO** / **Ghi xe RA** cho trường hợp mất vé, biển bẩn, xe không qua camera. Lượt ghi tay được đánh dấu `Ghi tay` để đối soát cuối ca.
- **✏ Sửa biển**: sửa lại biển đọc sai — cập nhật đồng thời lượt và các sự kiện của nó (audit `PLATE_CORRECTED`). Không cho sửa trùng biển đang có xe trong bãi.
- **🏍 Đổi loại xe**: phân loại lại và **tính lại phí** theo biểu phí của loại xe đó (lượt đã thu tiền thì giữ nguyên).

## Vé tháng (tab Đăng ký xe)

- Nhập biển + chủ xe + SĐT + loại xe, chọn số tháng rồi bấm **🎫 Bán / Gia hạn vé tháng**. Giá gợi ý lấy từ `monthly_ticket_fees` theo loại xe × số tháng.
- Gia hạn sớm **cộng dồn** vào số ngày còn lại (hết hạn 01/09, gia hạn ngày 20/08 thêm 1 tháng → 01/10).
- Danh sách hiển thị **Hạn vé** và **Còn lại**; vé còn ≤ 7 ngày tô cam, vé đã hết hạn tô đỏ, kèm dòng cảnh báo tổng hợp phía trên bảng.
- Tiền vé tháng vào doanh thu báo cáo và vào **ca trực đang mở**.

## Ca trực & đối soát tiền mặt

- Nút **🕒 Mở ca** (thanh trên cùng) mở ca với số tiền quỹ đầu ca. Mọi lượt thu tiền và vé tháng sau đó được gắn vào ca này.
- **🕒 Đóng ca** hiện quỹ đầu ca, tiền vé lượt, vé tháng, chuyển khoản và **tiền mặt phải có**; nhập tiền đếm được để ghi khớp/lệch. Giới hạn hiện tại: method `BANK`/`MOMO` đang bị gộp vào tiền mặt, vì vậy phải đối chiếu thêm provider/sao kê khi bật hai phương thức này.
- Bảng **Ca trực & đối soát tiền mặt** trong tab Báo cáo liệt kê các ca, số lệch (tô đỏ khi lệch) và ghi chú; ca đang mở tô xanh.

## Chế độ cổng & phí (tab Cài đặt)

- **Chế độ `all` (bãi tính phí):** mọi xe không nằm blacklist đều được vào; xe đăng ký hiện `MỞ`, xe lạ hiện `Khách`. Khi ra, hệ thống tính phí = `parking_flat_fee` + `parking_hourly_fee` × số giờ vượt `parking_free_minutes`, giới hạn bởi `parking_daily_cap` mỗi ngày, cộng `parking_overnight_fee` cho mỗi lần xe nằm qua mốc `parking_night_hour` (mặc định 22h).
- **Biểu phí theo loại xe:** mỗi lượt mang một loại (`MOTORBIKE` / `CAR` / `BICYCLE`). Loại lấy từ xe đã đăng ký, nếu là khách lạ thì lấy `default_vehicle_type`. Khai báo giá riêng cho ô tô/xe đạp trong `parking_tariffs` hoặc ngay trong bảng **Biểu phí riêng theo loại xe** ở tab Cài đặt; ô để 0 nghĩa là dùng chung biểu phí gốc.
- **Chế độ `registered_only` (kiểm soát ra-vào):** chỉ xe trong whitelist (còn hạn) mới mở cổng; xe lạ hoặc hết hạn bị `TỪ CHỐI`.
- Barrier luôn có mô phỏng (`SimulatedGate`); có sẵn backend TCP và serial, chạy song song qua `CompositeGate`. Trạng thái mô phỏng hết sau `gate_open_seconds`, nhưng controller thật phải tự nhả/pulse hoặc nhận nút/lệnh đóng; timer mô phỏng không tự gửi `CLOSE` đến phần cứng.
- Cột **Cổng** ở bảng nhận dạng và cột **Phí** / **Thanh toán** ở thẻ Bãi xe ghi lại quyết định và số tiền cho từng lượt.

## Thanh toán & doanh thu

- Khi xe ra, lượt được tính phí và đánh dấu `Chưa thanh toán` (khách) hoặc `Miễn` (xe đăng ký/không mất phí).
- Với lượt OUT hoàn tất, có phí và chưa thanh toán, dialog VietQR tự mở. Chọn lượt thủ công rồi bấm **📱 Thu QR** cũng mở cùng dialog.
- **Quét QR không tự chuyển sang Đã thu.** Nút **Đã thu tiền mặt** ghi `CASH`; SePay/Casso chỉ ghi `BANK` khi đọc được giao dịch hợp lệ sau lúc QR mở.
- Nút **Thu MoMo** tạo giao dịch merchant và query trạng thái; chỉ response thành công, đủ tiền mới ghi `MOMO/PAID`.
- Nhãn **Doanh thu hôm nay** cập nhật tổng tiền đã thu và số lượt chưa thu. Xuất CSV kèm cột `payment_status`, `paid_at`.

## Mô phỏng barrier trực quan

Khu **Camera view** có bảng *Mô phỏng barrier*: thanh chắn nâng/hạ động và đèn tín hiệu đỏ/xanh phản ánh đúng trạng thái `SimulatedGate` theo thời gian thực (mở khi cho phép, đỏ + giữ đóng khi từ chối). Hai nút **🚧 Mở barrier** và **⛔ Đóng barrier** trên thanh công cụ cho phép nhân viên điều khiển thủ công; mọi thao tác đều được ghi vào nhật ký. Đây là bản mô phỏng phần mềm để chạy thử toàn bộ luồng khi chưa gắn phần cứng.

## Sửa lỗi OCR biển Việt Nam

`correct_vietnamese_plate` sửa nhầm lẫn chữ↔số theo vị trí (2 số đầu là mã tỉnh, ký tự thứ 3 là chữ series, 5 số cuối là số đăng ký) — ví dụ `S9X3O2345 → 59X302345`. Chỉ chạy khi chuỗi đã gần đủ một biển hoàn chỉnh nên không "bịa" biển từ nhiễu OCR.

## Vận hành như một sản phẩm

- **Barrier phần cứng:** đặt `gate_backend` = `simulated` (mặc định) / `tcp` / `serial`. Với `tcp` khai báo `gate_host`+`gate_port`; với `serial` khai báo `gate_serial_port`+`gate_baudrate`. Lệnh mở là `gate_command` (mặc định `OPEN`), lệnh đóng là `gate_close_command` (mặc định `CLOSE`). Hardware và mô phỏng chạy song song qua `CompositeGate` — barrier trực quan vẫn hoạt động kể cả khi thiết bị offline. Lỗi kết nối không làm sập app (giữ ở `last_error`).
- **Thu tiền QR (VietQR):** chọn một trong 20 ngân hàng rồi khai số tài khoản/tên chủ tài khoản trong Cài đặt. QR chứa đúng số tiền và `GX<id lượt> <biển số>`; kiểm tra tại chỗ chỉ kiểm tra định dạng, không xác minh tài khoản thật.
- **Tự động xác nhận ngân hàng:** đặt `payment_provider` = `sepay` / `casso` + API Token/API Key, không dùng username/password. Transaction phải phát sinh sau lúc mở QR; có `GX<id>` đúng và đủ tiền, hoặc không có GX nhưng đúng tiền và chỉ có một lượt phù hợp. Chuyển thiếu/timestamp lỗi/giao dịch mơ hồ bị bỏ qua. Xem [Thanh toán & đối soát](docs/THANH-TOAN-VA-DOI-SOAT.md).
- **MoMo merchant:** khai Partner Code, Access Key, Secret Key và sandbox/production. App tạo QR rồi query trạng thái; đây không phải credential của ví cá nhân.
- **Tài khoản & phân quyền:** đặt `require_login: true` để bắt đăng nhập (mặc định `admin` / `admin` — đổi ngay khi triển khai). `admin` toàn quyền; `operator` không được backup/xóa dữ liệu/quản lý tài khoản. Nút **👤 Tài khoản** để thêm/xóa người dùng.
- **Báo cáo:** tab **Báo cáo** hiện doanh thu theo ngày + nhật ký hệ thống (audit: đăng nhập, mở/từ chối cổng, thu tiền, backup, xóa dữ liệu). Menu **⬇ Xuất báo cáo** cho phép chọn CSV hoặc PDF; PDF có biểu đồ doanh thu theo ngày, lưu lượng theo giờ, hình thức thanh toán và thời gian gửi xe trước các bảng chi tiết. Sau khi xuất, hộp thoại hiển thị đường dẫn và nút **Mở file** bằng ứng dụng mặc định.
- **Dọn dữ liệu cũ:** đặt **Giữ dữ liệu (ngày)** ở tab Cài đặt (`retention_days`). Khác 0 thì mỗi lần mở app tự xóa lượt, sự kiện và ảnh cũ hơn số ngày đó; nút **🧹 Dọn dữ liệu cũ** chạy ngay (chỉ admin). Xe đang trong bãi không bao giờ bị xóa dù vào từ lâu.
- **Sao lưu:** nút **💾 Sao lưu CSDL** tạo `data/backups/events_YYYYMMDD_HHMMSS.db`. File này không gồm snapshots, `config.json`, model hoặc log; cần sao lưu các phần đó riêng để phục hồi đầy đủ.
- **Tự khởi động & giám sát:** `auto_start: true` tự chạy nhận diện khi mở app; camera mất tín hiệu quá `camera_alert_seconds` giây sẽ hiện **⚠ MẤT KẾT NỐI** trên khung camera.

`qrcode`, `reportlab` và `pyserial` đã nằm trong `requirements.txt`.

## Chạy nhanh

```powershell
python app.py
```

Trong ứng dụng:

1. Vào thẻ **Cài đặt** → khung **Nguồn video / camera**. Chọn **Mở file video…** để thử với file; hoặc nhập `0` vào ô **Camera IP / RTSP / webcam** rồi bấm **Thêm nguồn** để dùng webcam đầu tiên.
   Có thể chọn nhanh một file ở ô **Video mẫu**, chọn **Chiều** `IN`/`OUT`, rồi bấm **Thêm video mẫu** để chạy nhiều video cùng lúc. Nút **Thay toàn bộ bằng video mẫu** thay hết nguồn hiện tại.
2. Với camera IP, nhập URL dạng `rtsp://user:password@ip:554/...`.
3. Điều chỉnh ROI. Với một làn xe máy, nên bắt đầu ở mức rộng 70%, cao 65%.
4. Bấm **▶ Bắt đầu**. Lần đầu PaddleOCR khởi tạo có thể mất một lúc.

Có thể truyền nhiều nguồn ngay từ dòng lệnh:

```powershell
python app.py --source video1.mp4 --source video2.mp4
python app.py --source 0 --source "rtsp://user:password@192.168.1.20:554/stream"
```

Để tạo cấu hình cố định, sao chép `config.example.json` thành `config.json` rồi sửa danh sách `cameras`. Các nguồn thêm từ giao diện cũng được lưu vào `config.json`.

## Các tham số quan trọng

- `roi_width`, `roi_height`: tỷ lệ vùng ROI ở chính giữa ảnh. ROI giờ là **cổng lọc** (giữ biển giao với vùng này) chứ không cắt ảnh trước khi detect, nên có thể để rộng mà không tốn thêm chi phí detect. Với làn cố định, thu hẹp ROI để bỏ qua biển ở làn/hậu cảnh không mong muốn.
- `detection_imgsz`: cạnh ảnh đưa vào YOLO (640/768/960/1280). Lớn hơn = bắt biển nhỏ/ở xa tốt hơn nhưng chậm hơn; mặc định 960. Chỉnh nhanh bằng ô **Kích thước ảnh** trong khung Nhận dạng.
- `frame_skip`: nhịp bỏ frame dự phòng, chỉ được dùng khi `detection_interval_seconds <= 0`; UI hiện luôn đặt chu kỳ tối thiểu 0,1 giây nên cấu hình bình thường chạy theo thời gian, không theo trường này.
- `preview_fps`: FPS hiển thị mục tiêu, độc lập với tốc độ nhận diện; mặc định 20 FPS.
- `detection_interval_seconds`: khoảng nghỉ giữa hai lần detect trên cùng camera; mặc định 0,5 giây và luôn xử lý frame mới nhất.
- `ocr_recognition_model`: mặc định dùng `PP-OCRv6_medium_rec` để đọc biển hai dòng chính xác hơn. Có thể đổi giữa Medium và Tiny trong giao diện; Tiny nhanh hơn nhưng dễ nhầm ký tự trên biển nhỏ.
- `min_votes`: số lần OCR giống nhau trước khi tạo sự kiện.
- `vote_window_seconds`: khoảng thời gian gom phiếu OCR.
- `duplicate_cooldown_seconds`: không ghi lại cùng biển trên cùng camera trong khoảng này.
- `recognized_cache_seconds`: thời gian tối đa giữ kết quả OCR đã xác nhận của một xe đang trong ROI.
- `track_max_missed_frames`: số frame detector được phép hụt trước khi coi xe đã rời ROI.
- `max_plates_per_frame`: số biển tối đa xử lý trong một frame.
- `cameras[].direction`: vai trò camera, chỉ nhận `IN` hoặc `OUT`. Có thể đổi ngay trong bảng nguồn bằng nút **Áp dụng cho nguồn đã chọn**.
- `cameras[].start_delay_seconds`: thời gian chờ trước khi bắt đầu đọc nguồn. Khi mô phỏng bằng cùng một video, đặt camera `OUT` trễ 15 giây để tạo khoảng cách giữa lượt vào và lượt ra.
- `cameras[].loop_video`: `true` để file chạy lại từ đầu khi hết; camera live không dùng tuỳ chọn này.

Preview đọc trực tiếp frame mới nhất của mỗi camera nên vẫn chạy ở `preview_fps` trong khi AI xử lý nền theo `detection_interval_seconds`. Nếu máy yếu khi mở nhiều camera, giảm `preview_fps` xuống 15. Nếu xe chạy nhanh và thường không đủ hai phiếu OCR, giảm `detection_interval_seconds` hoặc tạm đặt `min_votes` bằng 1 để khảo sát, nhưng không nên dùng cấu hình một phiếu khi điều khiển barrier.

## Dữ liệu đầu ra

- `data/events.db`: cơ sở dữ liệu SQLite.
- `data/snapshots/`: ảnh bằng chứng khi một biển được xác nhận.
- CSV được tạo khi chọn **Xuất sự kiện CSV** / **Xuất lượt gửi CSV**.
- Nút **🗑 Xóa toàn bộ dữ liệu nhận dạng** trong thẻ Cài đặt xóa toàn bộ sự kiện, lượt vào/ra và snapshot sau khi xác nhận; cấu hình camera được giữ nguyên.

Thư mục `data/` (database + snapshot) và `__pycache__/` là dữ liệu sinh ra lúc chạy, đã được đưa vào `.gitignore`. Nếu các file này đang bị Git theo dõi từ trước, gỡ khỏi index một lần (giữ nguyên file trên đĩa) bằng:

```powershell
git rm -r --cached data __pycache__ plate_app/__pycache__
```

SQLite gồm `cameras`, `plate_events`, `vehicle_visits`, `registered_vehicles`, `subscriptions`, `shifts`, `users`, `audit_log` và `app_state`. Sự kiện `IN` mở một lượt `INSIDE`; sự kiện `OUT` đóng lượt thành `COMPLETED`. Nếu chỉ có `OUT` mà không tìm thấy lần vào, lượt được đánh dấu `REVIEW` thay vì bị bỏ qua. Database cũ được tự nâng cấp khi khởi động ứng dụng.

## Tài liệu

- [docs/README.md](docs/README.md) — mục lục tài liệu theo vai trò.
- [docs/HUONG-DAN-VAN-HANH.md](docs/HUONG-DAN-VAN-HANH.md) — quy trình một ca cho nhân viên chốt.
- [docs/HUONG-DAN-CAI-DAT.md](docs/HUONG-DAN-CAI-DAT.md) — lắp camera, kết nối và nghiệm thu.
- [docs/THANH-TOAN-VA-DOI-SOAT.md](docs/THANH-TOAN-VA-DOI-SOAT.md) — VietQR, SePay/Casso và MoMo.
- [docs/THAM-CHIEU-CAU-HINH.md](docs/THAM-CHIEU-CAU-HINH.md) — toàn bộ `config.json`.
- [docs/KIEN-TRUC-KY-THUAT.md](docs/KIEN-TRUC-KY-THUAT.md) — module, luồng, DB và giới hạn.
- [docs/XU-LY-SU-CO.md](docs/XU-LY-SU-CO.md) — chẩn đoán theo triệu chứng.
- [packaging/README.md](packaging/README.md) — đóng gói và bàn giao `.exe`.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python -m compileall app.py plate_app tests
```

## Lưu ý trước khi dùng trong doanh nghiệp

Phiên bản hiện tại vẫn dùng Ultralytics và model có trong workspace. Cần rà soát giấy phép trước khi đóng gói thương mại. `config.json` lưu plaintext mật khẩu RTSP, token ngân hàng và MoMo secret; không commit, gửi công khai hoặc chép cấu hình thật vào gói bàn giao. Bật đăng nhập, đổi `admin/admin`, đặt retention phù hợp cho dữ liệu biển số/ảnh và xem các giới hạn nghiệp vụ tại [Kiến trúc kỹ thuật](docs/KIEN-TRUC-KY-THUAT.md#13-giới-hạn-và-điểm-cần-lưu-ý).
