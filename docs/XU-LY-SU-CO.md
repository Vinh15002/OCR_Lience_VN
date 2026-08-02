# Xử lý sự cố OCR Plate

Làm theo nhóm lỗi tương ứng và kiểm tra từng bước. Không gửi `config.json` nguyên bản cho bên hỗ trợ vì file có thể chứa mật khẩu camera và khóa thanh toán.

## 1. Thu thập thông tin trước khi xử lý

Ghi lại:

- thời điểm lỗi và thao tác ngay trước lỗi;
- đang chạy source hay bản EXE;
- camera nào, chiều IN/OUT, biển số và ID lượt liên quan;
- ảnh chụp màn hình nhưng che số tài khoản/token/Secret Key;
- `logs/app.log` đối với bản EXE;
- `logs/selftest.log` nếu lỗi AI/bản đóng gói.

Không sửa trực tiếp `events.db` khi ứng dụng đang mở. Sao lưu DB và snapshots trước mọi thao tác phục hồi.

## 2. Ứng dụng không mở

### Bản EXE

1. Xác nhận đã chép **nguyên thư mục** `OCR_Plate`, gồm `_internal`, model và config.
2. Mở `logs/app.log`.
3. Chạy self-test trong PowerShell:

```powershell
Set-Location C:\duong-dan\OCR_Plate
.\OCR_Plate.exe --self-test
Get-Content -Encoding UTF8 .\logs\selftest.log
```

4. Nếu thiếu `license_plate_detector.pt`, chép đúng model cạnh EXE.
5. Nếu báo thiếu DLL/Paddle, build lại từ môi trường sạch và kiểm tra model OCR đã được đóng gói.

Windows SmartScreen có thể cảnh báo vì EXE chưa ký số. Xác minh gói đến từ nguồn tin cậy; không tắt Defender toàn hệ thống để chạy thử.

### Chạy source

```powershell
python --version
python -m pip install -r requirements.txt
python -m compileall -q app.py plate_app
python app.py
```

Chạy từ console để thấy traceback. Project được nghiệm thu với Python 3.10; môi trường quá mới/cũ có thể không tương thích Paddle/Torch.

## 3. Lỗi `config.json`

Triệu chứng thường gặp: JSON decode error, camera direction invalid hoặc app đóng ngay.

Kiểm tra cú pháp mà không in secret:

```powershell
python -m json.tool .\config.json > $null
```

Kiểm tra tải bằng code:

```powershell
python -c "from pathlib import Path; from plate_app.config import load_config; c=load_config(Path('config.json')); print('OK cameras=', len(c.cameras))"
```

Lỗi thường gặp:

- dấu phẩy thừa ở phần tử cuối;
- dùng `True/False` thay vì JSON `true/false`;
- thêm comment `//`;
- `direction` không phải `IN`/`OUT`;
- trường camera bị viết sai tên;
- đường dẫn Windows dùng `\` đơn. Trong JSON cần `\\` hoặc dùng `/`.

Nếu cần tạo lại, giữ file lỗi làm bản riêng rồi sao chép `config.example.json`; không ghi đè khi chưa lưu credential cần thiết ở nơi an toàn.

## 4. Camera không có hình

| Trạng thái | Ý nghĩa / xử lý |
| --- | --- |
| `waiting Ns` | Camera có delay; chờ hết thời gian hoặc đặt delay 0. |
| `cannot open` | Sai URI, camera bận, codec/quyền hoặc mạng không tới được. |
| `reconnecting` | Nguồn live bị ngắt; app đang thử lại. |
| `finished` | File đã hết và `loop_video=false`. |
| `MẤT KẾT NỐI` | Không có frame quá `camera_alert_seconds`. |

Kiểm tra:

1. Webcam: thử index `0`, `1`; đóng Teams/Zoom/Camera app đang giữ thiết bị.
2. RTSP: mở đúng URL trong VLC trên cùng máy.
3. Ping IP camera và kiểm tra firewall/VLAN.
4. Kiểm tra username/password, stream path và camera có bật RTSP.
5. Giảm độ phân giải/bitrate nếu nhiều camera làm nghẽn mạng hoặc CPU.
6. Với file mẫu, bật **Lặp video** nếu cần chạy liên tục.

Khi thêm URI, chiều/delay/loop được lấy từ nhóm điều khiển phía trên danh sách nguồn. Sau khi thêm, chọn dòng rồi dùng nhóm điều khiển dưới bảng để sửa nguồn đã có.

## 5. Có hình nhưng không nhận dạng

1. Xem biển có nằm trong ROI và hộp detector có xuất hiện không.
2. Đảm bảo biển đủ lớn, nét, ít chói và góc nghiêng hợp lý.
3. Thử `detection_imgsz=1280` nếu biển quá nhỏ; theo dõi tải máy.
4. Giảm `detection_interval_seconds` nếu xe qua nhanh.
5. Kiểm tra `model_path` tồn tại và đúng model detector biển số.
6. Thử model OCR medium nếu tiny đọc yếu.
7. Giữ `min_votes=2` khi nối barrier; chỉ đặt 1 tạm thời để khảo sát.

Nếu OCR đọc được nhưng không tạo sự kiện, có thể chưa đủ phiếu trong `vote_window_seconds`, đang trong cooldown hoặc chuỗi không đạt kiểm tra dạng biển Việt Nam.

## 6. Nhận dạng sai hoặc ghi trùng

- Căn ROI hẹp quanh vị trí xe dừng, nhưng vẫn bao trọn biển.
- Tăng ánh sáng tán xạ; tránh IR/đèn chiếu thẳng gây cháy biển.
- Tăng `min_votes` hoặc `detection_imgsz` khi đọc sai.
- Tăng `duplicate_cooldown_seconds` nếu cùng xe tạo nhiều event sau khi đứng lâu.
- Nếu hai xe cùng khung, tăng `max_plates_per_frame` chỉ khi nghiệp vụ và phần cứng đủ tải.
- Sửa lượt bằng **Bãi xe → Sửa biển**, không chỉnh DB bằng tay.

## 7. Barrier

### Simulator chạy, barrier thật không chạy

1. Xác nhận `gate_backend` là `tcp` hoặc `serial`, không phải `simulated`.
2. TCP: kiểm tra IP, port, firewall và chuỗi lệnh thiết bị.
3. Serial: kiểm tra đúng `COMx`, baudrate, driver và không có phần mềm khác giữ cổng.
4. Kiểm tra bộ điều khiển cần newline, pulse hay protocol nhị phân; app hiện gửi chuỗi UTF-8 cộng `\n`.
5. Test nút mở/đóng thủ công trong khu vực an toàn.

UI hiện chưa hiển thị `last_error` phần cứng; simulator vẫn đổi trạng thái dù thiết bị thật offline. Cần xem log/thiết bị và không xem simulator là bằng chứng relay đã chạy.

### Cổng mở nhưng không tự hạ

`gate_open_seconds` chỉ kết thúc trạng thái mô phỏng. Nó không tự gửi `CLOSE` đến TCP/serial khi hết giờ. Dùng controller pulse/tự nhả hoặc nút **Đóng barrier**; không dựa vào timer phần mềm cho an toàn cơ khí.

### Cổng OUT mở trước khi thu

Đây là hành vi hiện tại: event `GUEST/ALLOW` gọi mở cổng trước khi dialog thanh toán xuất hiện. Nếu quy trình yêu cầu thu trước mở, chưa được dùng auto-open OUT nếu chưa sửa logic.

## 8. QR không hiện hoặc quét sai

- Nếu source báo thiếu `qrcode`, chạy `python -m pip install qrcode`; package đã nằm trong `requirements.txt` và bản EXE chuẩn phải có sẵn.
- Kiểm tra đã chọn ngân hàng, số tài khoản chỉ có chữ/số và BIN đủ 6 số.
- `Lưu & kiểm tra` không xác minh tài khoản thật; quét nghiệm thu bằng app ngân hàng.
- Đảm bảo số tài khoản không có khoảng trắng/dấu chấm.
- Nếu tên chủ tài khoản khác, sửa thông tin trong Cài đặt; tên hiển thị thực tế cuối cùng do app ngân hàng tra từ tài khoản.

## 9. Quét QR nhưng đã hiện Đã thu

Theo logic hiện tại, chỉ quét QR không thể tự đánh dấu. Kiểm tra:

1. Có ai bấm **Đã thu tiền mặt** không?
2. Có giao dịch ngân hàng cũ/mới đã khớp không? Giao dịch phải sau mốc mở QR.
3. Audit của lượt có `PAYMENT_CASH`, `PAYMENT_BANK` hay `PAYMENT_MOMO`?
4. `payment_reference`, `paid_at` và `collected_by` trong DB là gì? Chỉ kỹ thuật đọc bản sao DB, không sửa trực tiếp bản đang chạy.

Nếu không tìm thấy nguyên nhân, sao lưu DB/log và ghi lại ID lượt để tái hiện trên môi trường test.

## 10. SePay/Casso có giao dịch nhưng app không xác nhận

Kiểm tra:

- provider/token và dòng trạng thái feed;
- token là API Token/API Key, không phải username/password;
- tài khoản trong app trùng tài khoản provider;
- QR đã mở trước giao dịch;
- timestamp provider hợp lệ;
- nội dung có đúng `GX<id>`;
- tiền không thiếu;
- nếu mất GX, chỉ có một lượt đang nợ đúng số tiền;
- transaction chưa bị ghi là đã xử lý.

HTTP 401 thường là token sai/hết quyền. Lỗi kết nối không dừng nhận dạng; app sẽ thử lại ở chu kỳ sau.

SePay client hiện gọi API v1 legacy. Nếu provider ngừng hỗ trợ hoặc thay response, cần nâng `bankfeed.py` sang API v2; đổi token không sửa được lỗi schema/endpoint.

## 11. MoMo không tạo hoặc không xác nhận QR

- Cả ba credential phải thuộc cùng merchant và cùng môi trường.
- Thử `sandbox` trước; credential production không dùng cho test URL và ngược lại.
- Số tiền phải trong 1.000–50.000.000đ.
- **Lưu MoMo** chỉ kiểm tra trường không rỗng, không xác thực credential.
- Xem nội dung lỗi HTTP trong dialog/log nhưng che secret khi chia sẻ.
- App xác nhận bằng query polling; không cần nhận IPN tại máy chốt.

## 12. Sai số đóng ca hoặc báo cáo

Giới hạn đã biết:

- `BANK` và `MOMO` đang bị tính vào tiền mặt phải có; đối chiếu thêm provider/sao kê.
- Bảng **Thu theo nhân viên** bỏ sót audit BANK/MOMO.
- KPI tổng doanh thu có cộng vé tháng, biểu đồ doanh thu ngày không cộng vé tháng.
- Bảng ca/audit trên UI không hoàn toàn lọc theo khoảng ngày.

Khi quyết toán, giữ CSV/PDF, sao kê ngân hàng/MoMo và bản backup DB cùng kỳ. Không tự sửa con số DB để làm khớp.

## 13. Không xuất hoặc không mở được CSV/PDF

1. Chọn thư mục người dùng có quyền ghi, không phải thư mục hệ thống.
2. Kiểm tra file không đang bị Excel/Reader khóa.
3. Đảm bảo đủ dung lượng ổ đĩa.
4. Source: cài đủ `reportlab`; package đã nằm trong requirements.
5. Nếu xuất thành công nhưng **Mở file** lỗi, cài/chọn ứng dụng mặc định cho `.csv`/`.pdf`, hoặc mở trực tiếp từ đường dẫn hiển thị.

## 14. Database và phục hồi

### Sao lưu đầy đủ

Trong lúc app đang chạy, nút **Sao lưu CSDL** tạo bản DB nhất quán. Để lưu đầy đủ, sau khi dừng app hãy sao chép thêm:

```text
config.json
data/events.db hoặc data/backups/events_*.db
data/snapshots/
license_plate_detector.pt
```

### Phục hồi trên máy thử

1. Đóng ứng dụng trên máy đích.
2. Đổi tên thư mục `data` hiện tại để giữ bản quay lui.
3. Tạo `data` mới và sao chép file backup thành `data/events.db`.
4. Sao chép đúng thư mục snapshots tương ứng nếu có.
5. Khởi động bằng cấu hình đã kiểm tra và đối chiếu vài lượt/ảnh.
6. Chỉ sau khi nghiệm thu mới áp dụng quy trình tương tự trên máy thật.

Không chỉ chép file DB đè lên DB đang mở. Không dùng file backup của một thời điểm với snapshots đã bị retention xóa mà kỳ vọng đủ ảnh.

## 15. Hiệu năng

Khi CPU cao hoặc preview giật:

- giảm `preview_fps` từ 20 xuống 15;
- tăng `detection_interval_seconds`;
- giảm `detection_imgsz`;
- dùng OCR tiny nếu độ chính xác chấp nhận được;
- giảm số camera hoặc độ phân giải stream phụ;
- bật `max_plates_per_frame=1` cho làn đơn.

Theo dõi chỉ số `Detect .../s`, trạng thái camera và độ trễ thực tế. Không đánh đổi `min_votes` xuống 1 trên barrier thật chỉ để tăng cảm giác nhanh.

## 16. Khi nào cần chuyển cho kỹ thuật

Chuyển cấp khi:

- self-test fail sau khi build lại;
- DB báo corrupt/locked lặp lại;
- barrier thật không phản hồi dù kết nối/command đã xác minh;
- provider thay endpoint/response;
- một giao dịch xác nhận nhầm lượt;
- app crash hoặc mất dữ liệu.

Gói thông tin gồm bản sao DB, log, thời điểm/ID lượt và cấu hình đã che secret. Không gửi ảnh biển số ra ngoài phạm vi người có quyền xử lý dữ liệu.
