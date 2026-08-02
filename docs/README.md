# Bộ tài liệu OCR Plate

Đây là mục lục chính của ứng dụng nhận diện biển số và quản lý bãi xe OCR Plate. Tài liệu được viết theo hành vi hiện có trong mã nguồn; các nhãn tiếng Việt là nhãn đang hiển thị trên giao diện.

## Đọc tài liệu nào trước?

| Vai trò / nhu cầu | Tài liệu |
| --- | --- |
| Nhân viên giữ xe, thu ngân | [Hướng dẫn vận hành](HUONG-DAN-VAN-HANH.md) |
| Kỹ thuật lắp camera, cài máy mới | [Hướng dẫn lắp đặt & cấu hình](HUONG-DAN-CAI-DAT.md) |
| Cấu hình VietQR, SePay, Casso hoặc MoMo | [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md) |
| Cần biết ý nghĩa từng khóa trong `config.json` | [Tham chiếu cấu hình](THAM-CHIEU-CAU-HINH.md) |
| Lập trình viên cần sửa hoặc tích hợp hệ thống | [Kiến trúc kỹ thuật](KIEN-TRUC-KY-THUAT.md) |
| Build và triển khai bản Windows `.exe` | [Hướng dẫn đóng gói](../packaging/README.md) |
| Cần tìm nguyên nhân lỗi nhanh | [Xử lý sự cố](XU-LY-SU-CO.md) |
| Muốn xem nhanh tính năng và cách chạy source | [README dự án](../README.md) |

## Ứng dụng làm được gì?

- Nhận video từ webcam, file hoặc RTSP; nhiều nguồn có thể chạy đồng thời và được gán chiều `IN`/`OUT`.
- Phát hiện biển bằng YOLO, đọc ký tự bằng PaddleOCR, sửa các nhầm lẫn chữ/số thường gặp và chỉ ghi nhận sau khi đủ phiếu đồng thuận.
- Ghép sự kiện xe vào và xe ra thành một lượt gửi xe; lưu ảnh bằng chứng, thời gian, phí, loại xe và trạng thái thanh toán trong SQLite.
- Chạy theo chế độ bãi tính phí hoặc chỉ cho xe trong whitelist; hỗ trợ blacklist, vé tháng và ba loại xe.
- Điều khiển barrier mô phỏng, TCP hoặc cổng serial; có nút mở và đóng thủ công.
- Thu tiền mặt, VietQR chuyển khoản ngân hàng hoặc MoMo merchant. SePay/Casso có thể đọc giao dịch đến và tự xác nhận khi khớp.
- Quản lý ca trực, tài khoản `admin`/`operator`, audit log, sao lưu, lưu trữ và dọn dữ liệu cũ.
- Hiển thị báo cáo doanh thu, lưu lượng, thời gian gửi, công suất và đối soát; xuất CSV hoặc PDF có biểu đồ rồi mở file ngay từ hộp thoại hoàn tất.

## Luồng nghiệp vụ cốt lõi

```text
Camera IN → nhận dạng → quyết định ALLOW/GUEST/DENY → tạo lượt INSIDE
Camera OUT → nhận dạng → ghép lượt → tính phí → COMPLETED
                                             ├─ EXEMPT: không thu
                                             └─ UNPAID: mở dialog VietQR
                                                  ├─ tiền mặt → PAID ngay
                                                  ├─ SePay/Casso báo có → PAID
                                                  └─ MoMo xác nhận → PAID
```

Quét mã QR **không** có nghĩa là tiền đã về. Chỉ nút xác nhận tiền mặt hoặc kết quả xác nhận từ ngân hàng/MoMo mới đổi lượt thành **Đã thu**.

## Thuật ngữ và trạng thái

| Hiển thị | Giá trị nội bộ | Ý nghĩa |
| --- | --- | --- |
| Xe đang trong bãi | `INSIDE` | Có sự kiện IN, chưa có OUT tương ứng. |
| Hoàn tất | `COMPLETED` | Đã ghép được IN và OUT. |
| Cần kiểm tra | `REVIEW` hoặc `review_flag` | Thiếu lượt vào, OCR yếu, lượt quá ngắn hoặc có thao tác ghi tay. |
| Đã thu | `PAID` | Một phương thức thanh toán đã xác nhận thành công. |
| Chưa thanh toán | `UNPAID` | Lượt hoàn tất có phí nhưng chưa xác nhận tiền. |
| Miễn | `EXEMPT` | Xe đăng ký hợp lệ hoặc phí của lượt bằng 0. |
| Cho phép | `ALLOW` | Xe whitelist còn hiệu lực. |
| Khách | `GUEST` | Xe không đăng ký nhưng được vào ở chế độ `all`. |
| Từ chối | `DENY` | Xe blacklist hoặc không đủ quyền ở chế độ `registered_only`. |

## Nguyên tắc an toàn dữ liệu

- Không đưa `config.json` thật lên Git hoặc gửi cho người không có quyền: file này có thể chứa mật khẩu RTSP, API token và MoMo Secret Key dưới dạng văn bản rõ.
- Nút **Sao lưu CSDL** chỉ sao lưu `events.db`; muốn phục hồi đầy đủ bằng chứng cần sao lưu thêm `data/snapshots` và `config.json`.
- Bản đóng gói là dạng thư mục. Phải chép nguyên thư mục `dist/OCR_Plate`, không chỉ chép riêng `OCR_Plate.exe`.
- Đổi ngay mật khẩu mặc định `admin/admin` và bật `require_login` trước khi chạy thật.
- Nghiệm thu camera, biểu phí, nội dung QR và barrier trên môi trường thử trước khi nối barrier vật lý.

## Phạm vi hiện tại

OCR Plate là ứng dụng desktop cục bộ, một cơ sở dữ liệu SQLite và một ca trực đang mở cho toàn bộ máy. Ứng dụng chưa phải hệ thống cloud nhiều chi nhánh, không đồng bộ nhiều máy chốt và không cung cấp webhook server công khai. Các giới hạn kỹ thuật đã biết được ghi tại [Kiến trúc kỹ thuật](KIEN-TRUC-KY-THUAT.md#13-giới-hạn-và-điểm-cần-lưu-ý).

