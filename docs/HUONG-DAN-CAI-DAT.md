# Hướng dẫn lắp đặt & cấu hình OCR Plate

Tài liệu dành cho người vận hành bãi xe — làm theo đúng thứ tự từ trên xuống là chạy được.
Không cần biết lập trình.

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
3. Lần chạy đầu tiên mất **1–3 phút** để nạp model nhận dạng — đây là bình thường.

> Không đặt phần mềm trong `C:\Program Files` (Windows chặn ghi dữ liệu vào đó).
> Chi tiết cách tạo bản đóng gói: xem [packaging/README.md](../packaging/README.md).

### Cách B — Chạy từ mã nguồn (máy kỹ thuật, để chỉnh sửa)

```powershell
# 1. Cài Python 3.10 (nhớ tick "Add Python to PATH")
# 2. Trong thư mục dự án:
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
pip install qrcode pyserial      # tuỳ chọn: mã QR thu tiền + barrier cổng COM
python app.py
```

### 2.1 Kiểm tra sau khi cài

Mở app lên, thấy đủ 5 thẻ: **Giám sát · Bãi xe · Đăng ký xe · Báo cáo · Cài đặt** là cài đúng.

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

> Mật khẩu có ký tự đặc biệt (`@`, `:`, `/`) sẽ làm hỏng địa chỉ. Hãy **đổi sang mật khẩu chỉ gồm chữ và số**.

### 4.4 Tìm địa chỉ IP và thử trước bằng VLC

Luôn thử bằng VLC **trước khi** đưa vào phần mềm — để biết lỗi nằm ở camera hay ở app.

1. Tìm IP camera: mở CMD, gõ `arp -a`, hoặc dùng phần mềm dò tìm của hãng (SADP cho Hikvision, ConfigTool cho Dahua).
2. Mở **VLC** → `Media` → `Open Network Stream` → dán địa chỉ RTSP → **Play**.
3. Thấy hình = địa chỉ đúng, chép nguyên xi vào phần mềm.
4. Không thấy hình → xem [§8 Xử lý sự cố](#8-xử-lý-sự-cố).

### 4.5 Thêm camera trong ứng dụng

1. Vào thẻ **Cài đặt** → khung **Nguồn video / camera**.
2. Dán địa chỉ vào ô **Camera IP / RTSP / webcam**.
3. Ở hàng dưới bảng, chọn **Chiều** = `IN` (cổng vào) hoặc `OUT` (cổng ra) — *bắt buộc đúng*, nếu không phần mềm sẽ ghép nhầm lượt vào/ra.
4. Bấm **Thêm nguồn**.
5. Lặp lại cho camera thứ hai.
6. Bấm **▶ Bắt đầu** ở thanh trên cùng, sang thẻ **Giám sát** để xem hình.

Muốn đổi vai trò camera đã thêm: chọn dòng trong bảng → chọn **Chiều** → bấm **Áp dụng cho nguồn đã chọn**.

**Trạng thái hiển thị trên khung camera:**

| Trạng thái | Ý nghĩa |
| --- | --- |
| `connected` | Đang chạy bình thường |
| `reconnecting` | Mất tín hiệu, đang tự kết nối lại |
| `cannot open` | Sai địa chỉ / sai mật khẩu / camera chưa bật |
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
| Mã NH (BIN) | 6 chữ số theo bảng dưới |
| Số tài khoản | Chỉ chữ và số, không dấu cách |
| Tên chủ TK | Viết hoa không dấu, ví dụ `NGUYEN VAN A` |

Mã BIN các ngân hàng phổ biến:

| Ngân hàng | BIN | Ngân hàng | BIN |
| --- | :-: | --- | :-: |
| Vietcombank | 970436 | ACB | 970416 |
| VietinBank | 970415 | VPBank | 970432 |
| BIDV | 970418 | TPBank | 970423 |
| Agribank | 970405 | Sacombank | 970403 |
| Techcombank | 970407 | MSB | 970426 |
| MB Bank | 970422 | VIB | 970441 |
| HDBank | 970437 | SHB | 970443 |
| OCB | 970448 | — | — |

Bấm **Lưu & kiểm tra**. Phần mềm báo ngay:

- `✓ Tài khoản hợp lệ` → nút **📱 Thu QR** ở thẻ Bãi xe đã dùng được.
- `⚠ …` → sửa theo đúng nội dung báo lỗi.

Nội dung chuyển khoản được phần mềm tự điền sẵn dạng `GX<số lượt> <biển số>` — đây là chìa khoá để đối soát tự động ở mục sau.

### 6.3b Tự động báo "đã nhận tiền" (khuyến nghị)

Không có mục này thì nhân viên phải tự nhìn tin nhắn ngân hàng rồi bấm *Xác nhận đã thu* — chậm và dễ nhầm.
Bật đối soát tự động thì phần mềm tự tick khi tiền về, **cửa sổ QR tự đóng** và lượt chuyển sang `✓ Đã thu`.

**Cách hoạt động:** phần mềm hỏi dịch vụ đối soát mỗi 20 giây xem có giao dịch đến mới không, rồi ghép giao dịch với lượt xe theo nội dung `GX<số lượt>`. Nếu nội dung bị mất, phần mềm ghép theo số tiền — nhưng **chỉ khi có đúng một lượt chưa thanh toán đúng số tiền đó**, không bao giờ đoán bừa.

**Các bước:**

| Bước | Việc làm |
| :-: | --- |
| 1 | Đăng ký tài khoản tại **[my.sepay.vn](https://my.sepay.vn)** (hoặc **[casso.vn](https://casso.vn)**) và liên kết tài khoản ngân hàng của bãi xe |
| 2 | Vào *Cấu hình công ty → API Access* → **Thêm API** → sao chép **API Token** |
| 3 | Trong app: **Cài đặt** → **Thu tiền QR** → khung *Tự động xác nhận đã nhận tiền* |
| 4 | Chọn **Dịch vụ** = `sepay` (hoặc `casso`), dán **API token**, đặt **Chu kỳ** = `20` giây |
| 5 | Bấm **Áp dụng** → dòng trạng thái phải hiện `✓ Đang theo dõi · … lượt chờ thu` |
| 6 | Thử chuyển 2.000đ vào chính tài khoản đó với nội dung `GX1` → trong ~20 giây lượt số 1 phải tự chuyển sang `✓ Đã thu` |

**Những điều nên biết:**

- Token chỉ có quyền **đọc** danh sách giao dịch đến — không rút được tiền.
- Máy tính phải có Internet. Mất mạng thì dòng trạng thái hiện `⚠`, phần mềm vẫn chạy bình thường và nhân viên thu tay như cũ.
- Khách chuyển **thiếu tiền** thì phần mềm **không** tự xác nhận (tránh thất thoát); chuyển thừa thì vẫn tính là đã thu.
- Mỗi giao dịch chỉ khớp được một lượt, không thể tick hai lần cho cùng một lượt.
- Lượt thu tự động ghi nhân viên là `(tự động)`, phương thức `BANK`, kèm mã giao dịch ngân hàng để đối chiếu sau này.
- Ví điện tử (MoMo, ZaloPay, VNPay): các ví này đọc được mã VietQR nên khách vẫn quét trả tiền bình thường; phần đối soát tự động vẫn chạy qua tài khoản ngân hàng nhận tiền.

### 6.4 Vé tháng

Thẻ **Đăng ký xe** → nhập biển số, chủ xe, SĐT, loại xe → chọn số tháng → **🎫 Bán / Gia hạn vé tháng**.
Giá mặc định theo loại xe sửa trong `config.json` ở mục `monthly_ticket_fees`.

### 6.5 Ca trực

Đầu ca, nhân viên bấm **🕒 Mở ca** và nhập tiền quỹ lẻ. Cuối ca bấm **🕒 Đóng ca**, nhập số tiền đếm được — phần mềm báo khớp hay lệch bao nhiêu.

### 6.6 Tài khoản đăng nhập

Mặc định **không** bắt đăng nhập. Khi triển khai thật:

1. Mở `config.json`, đặt `"require_login": true`.
2. Mở lại app, đăng nhập `admin` / `admin`.
3. Vào **Cài đặt** → **👤 Tài khoản** → **đổi ngay mật khẩu admin** và tạo tài khoản `operator` cho nhân viên.

---

## 7. Nghiệm thu trước khi chạy thật

Chạy đủ 10 mục này rồi mới bàn giao cho nhân viên:

- [ ] Cả hai camera hiện `connected`, hình rõ, không ngược sáng.
- [ ] Chạy thử **10 xe** ban ngày: đọc đúng ít nhất 9 biển.
- [ ] Chạy thử **10 xe** ban đêm: đọc đúng ít nhất 8 biển.
- [ ] Xe vào rồi ra → lượt chuyển sang `COMPLETED`, phí tính đúng.
- [ ] Bấm đúp một lượt → thấy đủ **ảnh xe vào và ảnh xe ra**.
- [ ] Thử xe trong danh sách đen → cổng **không** mở.
- [ ] Thử ghi tay một lượt (giả lập mất vé) → lượt hiện cờ *Ghi tay*.
- [ ] Quét thử mã QR bằng app ngân hàng → đúng số tiền, đúng tên chủ tài khoản.
- [ ] Mở ca → thu 2 lượt → đóng ca → số tiền khớp.
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
| Lượt hiện `REVIEW` | Có xe ra mà không có xe vào | Dùng **Ghi thủ công** để bổ sung lượt vào, hoặc **✏ Sửa biển** nếu đọc sai |
| Máy chạy giật, chậm | Quá nhiều camera hoặc ảnh quá lớn | Giảm `preview_fps` xuống 15, **Kích thước ảnh** xuống 640 |
| Nút QR không hiện mã | Chưa cài gói `qrcode` | Chạy `pip install qrcode` (bản đóng gói đã có sẵn) |
| Đối soát tự động báo `⚠ HTTP 401` | Sai API token hoặc token đã bị xoá | Tạo lại token ở my.sepay.vn rồi dán lại, bấm **Áp dụng** |
| Tiền đã về mà không tự tick | Khách xoá nội dung `GX…`, hoặc có 2 lượt cùng số tiền | Thu tay bằng nút **💵 Thu tiền mặt**; nhắc khách giữ nguyên nội dung chuyển khoản |
| Không mở được cổng cho khách quen | Đang ở chế độ `registered_only` | Đổi sang `all`, hoặc thêm xe vào danh sách đăng ký |

Khi cần báo lỗi cho kỹ thuật, gửi kèm file `logs/app.log`.

---

## 9. Bảo trì định kỳ

| Việc | Tần suất |
| --- | --- |
| Lau ống kính camera | Hàng tuần |
| Kiểm tra ảnh chụp ban đêm còn rõ không | Hàng tuần |
| Bấm **💾 Sao lưu CSDL**, chép ra USB | Hàng tuần |
| Rà danh sách vé tháng sắp hết hạn (dòng cảnh báo cam ở thẻ Đăng ký xe) | Hàng tuần |
| Đối chiếu báo cáo doanh thu với tiền thực thu | Cuối mỗi ca |
| Đặt **Giữ dữ liệu (ngày)** để tự xoá dữ liệu cũ (ví dụ 180 ngày) | Cài một lần |

---

## 10. Tài liệu liên quan

- [README.md](../README.md) — toàn bộ tính năng và tham số nâng cao
- [packaging/README.md](../packaging/README.md) — tạo bản `.exe` mang đi cài
