# Hướng dẫn lắp đặt & cấu hình OCR Plate

Tài liệu dành cho kỹ thuật lắp đặt và người cấu hình bãi xe — làm theo đúng thứ tự từ trên xuống là chạy được. Quy trình sử dụng hằng ngày nằm tại [Hướng dẫn vận hành](HUONG-DAN-VAN-HANH.md); mục lục đầy đủ nằm tại [docs/README.md](README.md).

---

## 0. Đi nhanh trong 5 bước

| # | Việc cần làm | Mất bao lâu | Mục tham khảo |
| :-: | --- | :-: | --- |
| 1 | Cài phần mềm lên máy tính | 5–20 phút | [§2](#2-cài-phần-mềm) |
| 2 | Lắp camera, lấy được địa chỉ camera | 30–60 phút | [§3](#3-lắp-camera-đúng-cách) · [§4](#4-kết-nối-camera-vào-phần-mềm) |
| 3 | Thêm camera vào app, gán VÀO / RA | 5 phút | [§4.5](#45-thêm-camera-trong-ứng-dụng) |
| 4 | Căn vùng quét, chạy thử vài xe | 15 phút | [§5](#5-căn-vùng-quét-roi) |
| 5 | Khai báo giá vé, vé tháng, tài khoản QR | 10 phút | [§6](#6-cấu-hình-nghiệp-vụ) |

> **Mẹo:** làm xong mỗi mục hãy chạy thử ngay với 1–2 xe thật. Đừng cấu hình hết rồi mới thử.

---

## 1. Chuẩn bị

### 1.1 Máy tính

| Hạng mục | Tối thiểu | Khuyến nghị |
| --- | --- | --- |
| Hệ điều hành | Windows 10 64-bit | Windows 11 64-bit |
| CPU | Core i3 thế hệ 8 | Core i5 thế hệ 10 trở lên |
| RAM | 8 GB | 16 GB |
| Ổ cứng trống | 10 GB | 50 GB (SSD) |
| Card đồ hoạ | Không bắt buộc | NVIDIA (nhanh hơn rõ rệt) |
| Mạng | Cùng mạng LAN với camera | Dây LAN, không dùng Wi-Fi cho camera |

Một làn xe máy chạy tốt trên máy i5 + 8 GB RAM. Từ **3 camera trở lên** nên dùng i5/i7 + 16 GB.

### 1.2 Thiết bị

- **Camera:** 2 chiếc (một cho làn VÀO, một cho làn RA). Nếu chỉ có một lối vào–ra chung thì 1 chiếc cũng chạy được, nhưng không đối soát được vào/ra.
- **Barrier:** không bắt buộc lúc đầu — phần mềm có sẵn barrier mô phỏng để chạy thử toàn bộ quy trình.
- **Nguồn điện dự phòng (UPS):** rất nên có, tránh mất dữ liệu khi cúp điện.

---

## 2. Cài phần mềm

### Cách A — Dùng bản đóng gói (khuyến nghị cho máy ở bãi)

1. Chép cả thư mục `OCR_Plate` (bản `dist`) vào ổ đĩa, ví dụ `D:\OCR_Plate`.
2. Chạy `OCR_Plate.exe`.
3. Lần chạy đầu có thể mất từ vài chục giây đến vài phút để chép/nạp model nhận dạng — đây là bình thường.

> Không đặt phần mềm trong `C:\Program Files` (Windows chặn ghi dữ liệu vào đó).
> Chi tiết cách tạo bản đóng gói: xem [packaging/README.md](../packaging/README.md).

### Cách B — Chạy từ mã nguồn (máy kỹ thuật, để chỉnh sửa)

```powershell
# 1. Cài Python 3.10 (nhớ tick "Add Python to PATH")
# 2. Trong thư mục dự án:
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

`qrcode`, `reportlab` và `pyserial` đã có trong `requirements.txt`, không cần cài lại riêng.

### 2.1 Kiểm tra sau khi cài

Mở app lên, thấy đủ 5 thẻ: **Giám sát · Bãi xe · Đăng ký xe · Báo cáo · Cài đặt** là cài đúng. Với bản EXE, nên chạy thêm `OCR_Plate.exe --self-test`; `SELFTEST PASSED` trong `logs/selftest.log` xác nhận detector và OCR nạp được, nhưng chưa thay cho kiểm thử camera/thanh toán/barrier.

---

## 3. Lắp camera đúng cách

Đây là yếu tố quyết định **90% độ chính xác**. Phần mềm tốt đến mấy cũng không cứu được camera lắp sai.

### 3.1 Thông số lắp đặt cho xe máy

| Thông số | Giá trị nên dùng | Vì sao |
| --- | --- | --- |
| Chiều cao camera | **0,9 – 1,3 m** so với mặt đất | Ngang tầm biển số xe máy |
| Khoảng cách tới xe | **2 – 3 m** | Biển chiếm đủ lớn trong khung hình |
| Góc chếch ngang | **≤ 30°** | Chếch quá thì chữ bị méo, đọc sai |
| Góc chúc xuống | **10° – 20°** | Tránh chói đèn pha |
| Độ phân giải | **2 MP (1080p)** là đủ | Cao hơn chỉ tốn CPU |
| Tốc độ màn trập | **1/500 s trở lên** | Chống nhoè khi xe đang chạy |
| Chiều rộng khung hình | Biển số chiếm **≥ 1/10** chiều ngang | Nhỏ hơn thì OCR đọc sai |

### 3.2 Ba lỗi lắp đặt hay gặp

| ❌ Sai | ✅ Đúng |
| --- | --- |
| Camera treo cao 3 m chúc xuống | Hạ xuống ngang tầm biển số (~1,1 m) |
| Camera hướng thẳng ra ngoài trời (ngược sáng) | Quay vào trong, hoặc làm mái che |
| Chỉ có đèn đường vàng, tối om ban đêm | Gắn đèn LED trắng hoặc dùng camera có đèn hồng ngoại |

### 3.3 Ánh sáng ban đêm

- Đèn LED trắng ~10 W chiếu chếch vào vùng biển số là đủ cho một làn xe máy.
- **Không** chiếu đèn thẳng vào camera và **không** để đèn ngay sau lưng xe.
- Biển số phản quang + đèn hồng ngoại của camera thường cho ảnh rõ nhất ban đêm.

---

## 4. Kết nối camera vào phần mềm

Phần mềm nhận **4 loại nguồn**. Chọn loại bạn đang có:

| Loại nguồn | Ghi vào ô **Camera IP / RTSP / webcam** | Dùng khi nào |
| --- | --- | --- |
| Webcam USB | `0` (webcam thứ hai là `1`) | Chạy thử tại bàn |
| Điện thoại Android | `http://<IP-điện-thoại>:8080/video` | Dựng thử nhanh, không cần mua camera |
| Camera IP / đầu ghi | `rtsp://user:pass@<IP>:554/...` | Lắp đặt thật |
| File video | Nút **Mở file video…** | Diễn tập, huấn luyện nhân viên |

### 4.1 Webcam USB

Nhập `0` vào ô địa chỉ rồi bấm **Thêm nguồn**. Nếu máy có nhiều webcam, thử `1`, `2`…

### 4.2 Dùng điện thoại Android làm camera

1. Cài ứng dụng **IP Webcam** từ CH Play.
2. Mở app → kéo xuống cuối → **Start server**.
3. Màn hình hiện địa chỉ dạng `http://192.168.1.25:8080`.
4. Địa chỉ điền vào phần mềm là địa chỉ đó **cộng thêm `/video`**:

   ```
   http://192.168.1.25:8080/video
   ```

5. Điện thoại và máy tính phải **chung một mạng Wi-Fi**.

### 4.3 Camera IP (RTSP)

Địa chỉ RTSP khác nhau theo hãng. Thay `admin`, `matkhau`, `192.168.1.108` bằng thông tin camera của bạn:

| Hãng | Luồng chính (nét, dùng để nhận dạng) | Luồng phụ (nhẹ hơn) |
| --- | --- | --- |
| **Hikvision** | `rtsp://admin:matkhau@192.168.1.108:554/Streaming/Channels/101` | `.../Streaming/Channels/102` |
| **Dahua / Imou / KBVision** | `rtsp://admin:matkhau@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0` | `...&subtype=1` |
| **Ezviz** | `rtsp://admin:matkhau@192.168.1.108:554/h264/ch1/main/av_stream` | `.../ch1/sub/av_stream` |
| **Đầu ghi (NVR/DVR)** | Giống hãng tương ứng, đổi số kênh: `101`, `201`, `301`… hoặc `channel=2` | — |

> Mật khẩu có ký tự đặc biệt (`@`, `:`, `/`) phải được URL-encode đúng. Ưu tiên tạo một tài khoản camera chỉ có quyền xem thay vì làm yếu mật khẩu quản trị.

### 4.4 Tìm địa chỉ IP và thử trước bằng VLC

Luôn thử bằng VLC **trước khi** đưa vào phần mềm — để biết lỗi nằm ở camera hay ở app.

1. Tìm IP camera: mở CMD, gõ `arp -a`, hoặc dùng phần mềm dò tìm của hãng (SADP cho Hikvision, ConfigTool cho Dahua).
2. Mở **VLC** → `Media` → `Open Network Stream` → dán địa chỉ RTSP → **Play**.
3. Thấy hình = địa chỉ đúng, chép nguyên xi vào phần mềm.
4. Không thấy hình → xem [§8 Xử lý sự cố](#8-xử-lý-sự-cố).

### 4.5 Thêm camera trong ứng dụng

1. Vào thẻ **Cài đặt** → khung **Nguồn video / camera**.
2. Ở nhóm tuỳ chọn phía trên danh sách, chọn **Chiều** = `IN` (cổng vào) hoặc `OUT` (cổng ra), nhập **Trễ (s)** và chọn **Lặp video** nếu nguồn là file cần chạy lại.
3. Dán địa chỉ vào ô **Camera IP / RTSP / webcam**.
4. Bấm **Thêm nguồn**. Nút này lấy Chiều/Trễ/Lặp từ nhóm phía trên, không lấy nhóm chỉnh sửa phía dưới bảng.
5. Lặp lại cho camera thứ hai.
6. Bấm **▶ Bắt đầu** ở thanh trên cùng, sang thẻ **Giám sát** để xem hình.

Muốn đổi nguồn đã thêm: chọn dòng trong bảng → chỉnh **Chiều / Trễ / Lặp video** ở nhóm dưới bảng → bấm **Áp dụng cho nguồn đã chọn**. File không bật lặp sẽ chuyển sang `finished` khi chạy hết; file bật lặp sẽ mở lại từ đầu.

**Trạng thái hiển thị trên khung camera:**

| Trạng thái | Ý nghĩa |
| --- | --- |
| `connected` | Đang chạy bình thường |
| `reconnecting` | Mất tín hiệu, đang tự kết nối lại |
| `cannot open` | Sai địa chỉ / sai mật khẩu / camera chưa bật |
| `finished` | File đã chạy hết và không bật lặp |
| `⚠ MẤT KẾT NỐI` | Quá `camera_alert_seconds` giây không có hình |

---

## 5. Căn vùng quét (ROI)

Vùng quét là khung ở giữa màn hình — phần mềm **chỉ ghi nhận biển số nằm trong vùng này**, giúp bỏ qua xe ở làn bên cạnh hoặc xe ngoài đường.

1. Vào **Cài đặt** → khung **Nhận dạng**.
2. Chỉnh **Vùng quét ngang %** và **dọc %**. Khởi điểm tốt cho một làn xe máy: **ngang 70, dọc 65**.
3. Nhìn khung camera: vạch kẻ phải bao trọn vị trí biển số khi xe dừng trước barrier.
4. Bấm **Lưu thông số nhận dạng**.

### 5.1 Tinh chỉnh khi nhận dạng chưa tốt

| Hiện tượng | Chỉnh ô nào | Chỉnh thế nào |
| --- | --- | --- |
| Không bắt được xe ở xa | **Kích thước ảnh** | Tăng 960 → 1280 (chậm hơn) |
| Máy chạy nặng, hình giật | **Kích thước ảnh** / `preview_fps` | Giảm về 640 / 15 |
| Xe chạy nhanh, hay bị bỏ sót | **Chu kỳ nhận dạng (s)** | Giảm 0,5 → 0,3 |
| Bắt nhầm xe làn bên | **Vùng quét ngang %** | Thu hẹp lại |
| Đọc sai ký tự trên biển nhỏ | **Mô hình OCR** | Chọn `PP-OCRv6_medium_rec` rồi bấm **Áp dụng** |
| Một xe bị ghi 2 lần | `duplicate_cooldown_seconds` (trong `config.json`) | Tăng 10 → 20 |

> **Không** đặt `min_votes = 1` khi đã đấu barrier thật: một lần đọc nhầm sẽ mở cổng cho xe lạ.

---

## 6. Cấu hình nghiệp vụ

### 6.1 Chọn chế độ hoạt động

Vào **Cài đặt** → **Cổng & phí gửi xe** → mục *Chế độ cổng*:

| Chế độ | Dùng cho | Cách hoạt động |
| --- | --- | --- |
| `all` | Bãi giữ xe thu phí | Mọi xe không nằm danh sách đen đều vào được, xe lạ tính tiền theo lượt |
| `registered_only` | Chung cư, công ty, trường học | Chỉ xe trong danh sách đăng ký còn hạn mới được mở cổng |

### 6.2 Khai báo giá vé

| Ô nhập | Ý nghĩa | Ví dụ bãi xe máy |
| --- | --- | --- |
| Phí/lượt | Thu cố định mỗi lượt | `3.000` |
| Phí/giờ | Cộng thêm theo từng giờ bắt đầu | `0` (hoặc `2.000` nếu tính giờ) |
| Miễn phí (phút) | Gửi dưới mức này không tính tiền giờ | `15` |
| Trần/ngày | Mức tối đa tiền giờ trong một ngày | `20.000` |
| Phí qua đêm | Cộng thêm mỗi đêm xe nằm lại | `5.000` |
| Giờ tính đêm | Mốc bắt đầu tính một đêm | `22` |
| Sức chứa | Tổng số chỗ trong bãi | `200` |

Ô tô, xe đạp khai giá riêng ở bảng **Biểu phí riêng theo loại xe** ngay bên dưới (để `0` = dùng chung giá ở trên).
Bấm **Áp dụng quy tắc** để lưu.

### 6.3 Tài khoản nhận tiền QR (VietQR)

Vào **Cài đặt** → khung **Thu tiền QR (VietQR)**:

| Ô nhập | Nhập gì |
| --- | --- |
| Mã NH (BIN) | Chọn ngân hàng trong danh sách dropdown |
| Số tài khoản | Chỉ chữ và số, không dấu cách |
| Tên chủ TK | Viết hoa không dấu, ví dụ `NGUYEN VAN A` |

Dropdown hiện có 20 ngân hàng: VietinBank, Vietcombank, BIDV, Agribank, MBBank, Techcombank, ACB, VPBank, TPBank, Sacombank, HDBank, VIB, SHB, Eximbank, MSB, OCB, LPBank, PVcomBank, ABBANK và SeABank.

Bấm **Lưu & kiểm tra**. Phần mềm báo ngay:

- `✓ Tài khoản hợp lệ` → dữ liệu đủ đúng định dạng để tạo QR.
- `⚠ …` → sửa theo đúng nội dung báo lỗi.

Kiểm tra tại chỗ không gọi ngân hàng để xác minh số tài khoản hoặc tên chủ tài khoản. Bắt buộc quét thử bằng app ngân hàng trước nghiệm thu.

Nội dung chuyển khoản được phần mềm tự điền sẵn dạng `GX<số lượt> <biển số>` — đây là chìa khoá để đối soát tự động ở mục sau.

### 6.3b Tự động báo "đã nhận tiền" (khuyến nghị)

Bật đối soát tự động thì phần mềm tự tick khi tiền về, **cửa sổ QR tự đóng** và lượt chuyển sang `✓ Đã thu`. Không có feed, QR vẫn tạo được nhưng ứng dụng không biết tiền đã về. Nút trong dialog là **Đã thu tiền mặt**, không phải xác nhận chuyển khoản.

**Cách hoạt động:** phần mềm poll dịch vụ đối soát, rồi ghép giao dịch với lượt xe theo nội dung `GX<số lượt>`. Giao dịch phải phát sinh sau lúc dialog QR của lượt được mở. Nếu nội dung bị mất, phần mềm chỉ ghép khi số tiền bằng chính xác và có đúng một lượt đang nợ mức đó.

**Các bước:**

| Bước | Việc làm |
| :-: | --- |
| 1 | Đăng ký tài khoản tại **[my.sepay.vn](https://my.sepay.vn)** (hoặc **[casso.vn](https://casso.vn)**) và liên kết tài khoản ngân hàng của bãi xe |
| 2 | Tạo **API Token/API Key có quyền đọc giao dịch** theo tài liệu của provider; không dùng tên đăng nhập/mật khẩu |
| 3 | Trong app: **Cài đặt** → **Thu tiền QR** → khung *Tự động xác nhận đã nhận tiền* |
| 4 | Chọn **Dịch vụ** = `sepay` (hoặc `casso`), dán **API token**, đặt **Chu kỳ** = `20` giây |
| 5 | Bấm **Áp dụng** → dòng trạng thái phải hiện `✓ Đang theo dõi · … lượt chờ thu` |
| 6 | Tạo một lượt thử, mở QR, chuyển đúng số tiền và giữ nguyên nội dung `GX...` hiện trên QR → chờ lượt tự chuyển sang `✓ Đã thu` |

**Những điều nên biết:**

- Trường token chỉ nhận API Token/API Key. Không nhập username, password SePay hay thông tin Internet Banking.
- Máy tính phải có Internet. Mất mạng thì dòng trạng thái hiện `⚠`, phần mềm vẫn chạy bình thường và nhân viên thu tay như cũ.
- Khách chuyển **thiếu tiền** thì phần mềm **không** tự xác nhận (tránh thất thoát); chuyển thừa thì vẫn tính là đã thu.
- Mỗi giao dịch chỉ khớp được một lượt, không thể tick hai lần cho cùng một lượt.
- Lượt thu tự động ghi nhân viên là `(tự động)`, phương thức `BANK`, kèm mã giao dịch ngân hàng để đối chiếu sau này.
- Chỉ quét QR mà chưa chuyển tiền không làm đổi trạng thái.

Client SePay hiện dùng endpoint API v1 legacy. Xem toàn bộ điều kiện matching, lưu ý phiên bản API và xử lý lỗi tại [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md).

### 6.3c MoMo merchant

MoMo là lựa chọn riêng trong thẻ **Bãi xe**, không dùng `payment_api_token`. Vào **Cài đặt → Ví điện tử MoMo**, chọn `sandbox`/`production`, nhập **Partner Code**, **Access Key**, **Secret Key** của tài khoản MoMo for Business rồi bấm **Lưu MoMo**.

- Không dùng thông tin ví MoMo cá nhân.
- Dòng sẵn sàng chỉ kiểm tra đã nhập đủ ba trường; phải chạy một giao dịch sandbox để xác thực thật.
- Ứng dụng tạo giao dịch merchant và query trạng thái; chỉ thành công, đủ tiền mới đánh dấu `MOMO/PAID`.
- Credential được lưu rõ trong `config.json`; không gửi hoặc đưa file này vào bản phát hành công khai.

### 6.4 Vé tháng

Thẻ **Đăng ký xe** → nhập biển số, chủ xe, SĐT, loại xe → chọn số tháng → **🎫 Bán / Gia hạn vé tháng**.
Giá mặc định theo loại xe sửa trong `config.json` ở mục `monthly_ticket_fees`.

### 6.5 Ca trực

Đầu ca, nhân viên bấm **🕒 Mở ca** và nhập tiền quỹ lẻ. Cuối ca bấm **🕒 Đóng ca**, nhập số tiền đếm được — phần mềm báo khớp hay lệch bao nhiêu.

> Giới hạn hiện tại: giao dịch tự động `BANK` và `MOMO` đang bị bộ tính ca gộp vào tiền mặt phải có. Khi bật hai phương thức này, phải đối chiếu thêm provider/sao kê cho tới khi logic được sửa.

### 6.6 Tài khoản đăng nhập

Mặc định **không** bắt đăng nhập. Khi triển khai thật:

1. Mở `config.json`, đặt `"require_login": true`.
2. Mở lại app, đăng nhập `admin` / `admin`.
3. Vào **Cài đặt** → **👤 Tài khoản** → **đổi ngay mật khẩu admin** và tạo tài khoản `operator` cho nhân viên.

Operator vẫn có thể vận hành camera, sửa cấu hình, thu tiền và điều khiển barrier; chỉ bị chặn quản lý tài khoản, backup, purge và xóa dữ liệu nhận dạng. Ứng dụng hiện chưa có nút logout/chuyển người dùng.

---

## 7. Nghiệm thu trước khi chạy thật

Chạy đủ các mục này rồi mới bàn giao cho nhân viên:

- [ ] Cả hai camera hiện `connected`, hình rõ, không ngược sáng.
- [ ] Chạy thử **10 xe** ban ngày: đọc đúng ít nhất 9 biển.
- [ ] Chạy thử **10 xe** ban đêm: đọc đúng ít nhất 8 biển.
- [ ] Xe vào rồi ra → lượt chuyển sang `COMPLETED`, phí tính đúng.
- [ ] Xe OUT có phí → dialog QR tự mở; chỉ quét, chưa chuyển tiền → vẫn **Chưa thanh toán**.
- [ ] Bấm đúp một lượt → thấy đủ **ảnh xe vào và ảnh xe ra**.
- [ ] Thử xe trong danh sách đen → cổng **không** mở.
- [ ] Thử ghi tay một lượt (giả lập mất vé) → lượt hiện cờ *Ghi tay*.
- [ ] Quét thử mã QR bằng app ngân hàng → đúng số tiền, đúng tên chủ tài khoản.
- [ ] Nếu bật SePay/Casso: chuyển đúng tiền và nội dung `GX...` → lượt tự thành **Đã thu**.
- [ ] Nếu bật MoMo: giao dịch sandbox thành công → lượt tự thành **Đã thu**.
- [ ] Nút **Đã thu tiền mặt** ghi `CASH`; không dùng nút này cho chuyển khoản.
- [ ] Video file bật/tắt **Lặp video** có hành vi đúng.
- [ ] Hai nút mở/đóng barrier thủ công phản hồi và có audit; cổng thật có cảm biến an toàn.
- [ ] Xuất báo cáo CSV và PDF → PDF có biểu đồ, nút **Mở file** hoạt động.
- [ ] Mở ca → thu thử → đóng ca; nếu có BANK/MOMO, đối chiếu thêm sao kê do giới hạn đã nêu.
- [ ] Bấm **💾 Sao lưu CSDL** → có file trong `data/backups/`.

---

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Camera báo `cannot open` | Sai địa chỉ, sai mật khẩu, khác lớp mạng | Thử lại bằng VLC ([§4.4](#44-tìm-địa-chỉ-ip-và-thử-trước-bằng-vlc)); đổi mật khẩu bỏ ký tự đặc biệt |
| Camera hay `reconnecting` | Wi-Fi yếu, luồng chính quá nặng | Đi dây LAN, hoặc dùng luồng phụ (`102` / `subtype=1`) |
| Hình có nhưng không bắt được biển | Biển nằm ngoài vùng quét, camera quá cao | Nới vùng quét, hạ camera xuống ~1,1 m |
| Đọc sai ký tự (`S` ↔ `5`, `O` ↔ `0`) | Ảnh nhỏ hoặc mờ | Tăng **Kích thước ảnh** lên 1280, chuyển **Mô hình OCR** sang `medium` |
| Ban đêm không đọc được | Thiếu sáng, màn trập chậm | Gắn đèn LED trắng, chỉnh màn trập ≥ 1/500 s |
| Một xe ghi thành 2 lượt | Cooldown quá ngắn | Tăng `duplicate_cooldown_seconds` lên 20 |
| Lượt hiện `REVIEW` | Có xe ra mà không có xe vào | Đối soát ảnh và xử lý theo quy trình; IN ghi sau không tự ghép lại lượt REVIEW cũ |
| Máy chạy giật, chậm | Quá nhiều camera hoặc ảnh quá lớn | Giảm `preview_fps` xuống 15, **Kích thước ảnh** xuống 640 |
| Nút QR không hiện mã | Chưa cài gói `qrcode` | Chạy `pip install qrcode` (bản đóng gói đã có sẵn) |
| Đối soát tự động báo `⚠ HTTP 401` | Sai API token hoặc token đã bị xoá | Tạo lại token ở my.sepay.vn rồi dán lại, bấm **Áp dụng** |
| Tiền đã về mà không tự tick | QR chưa được mở trước giao dịch, sai GX, thiếu tiền, timestamp/token/tài khoản sai hoặc số tiền mơ hồ | Kiểm tra trạng thái feed và [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md); không ghi giả phương thức tiền mặt |
| Không mở được cổng cho khách quen | Đang ở chế độ `registered_only` | Đổi sang `all`, hoặc thêm xe vào danh sách đăng ký |

Xem quy trình chi tiết tại [Xử lý sự cố](XU-LY-SU-CO.md). Bản EXE ghi lỗi vào `logs/app.log`; chạy source nên lấy traceback từ console. Luôn che token, Secret Key và mật khẩu RTSP trước khi gửi.

---

## 9. Bảo trì định kỳ

| Việc | Tần suất |
| --- | --- |
| Lau ống kính camera | Hàng tuần |
| Kiểm tra ảnh chụp ban đêm còn rõ không | Hàng tuần |
| Bấm **💾 Sao lưu CSDL**; khi app đã dừng, sao lưu thêm `data/snapshots` và `config.json` vào nơi được bảo vệ | Hàng tuần |
| Rà danh sách vé tháng sắp hết hạn (dòng cảnh báo cam ở thẻ Đăng ký xe) | Hàng tuần |
| Đối chiếu báo cáo doanh thu với tiền thực thu | Cuối mỗi ca |
| Đặt **Giữ dữ liệu (ngày)** để tự xoá dữ liệu cũ (ví dụ 180 ngày) | Cài một lần |

---

## 10. Tài liệu liên quan

- [Mục lục tài liệu](README.md) — chọn tài liệu theo vai trò
- [Hướng dẫn vận hành](HUONG-DAN-VAN-HANH.md) — quy trình một ca tại chốt
- [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md) — VietQR, SePay/Casso và MoMo
- [Tham chiếu cấu hình](THAM-CHIEU-CAU-HINH.md) — ý nghĩa toàn bộ `config.json`
- [Kiến trúc kỹ thuật](KIEN-TRUC-KY-THUAT.md) — module, DB, luồng và giới hạn
- [Xử lý sự cố](XU-LY-SU-CO.md) — chẩn đoán theo triệu chứng
- [Hướng dẫn đóng gói](../packaging/README.md) — tạo bản `.exe` mang đi cài
