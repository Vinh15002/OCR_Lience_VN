# Hướng dẫn vận hành OCR Plate

Tài liệu này dành cho nhân viên tại chốt và quản lý ca. Mục tiêu là vận hành một ca từ lúc mở ứng dụng đến lúc đối soát, đóng ca và bàn giao.

## 1. Giao diện chính

Ứng dụng có năm thẻ:

| Thẻ | Công việc chính |
| --- | --- |
| **Giám sát** | Xem camera, khung nhận dạng, barrier mô phỏng và sự kiện mới nhất. |
| **Bãi xe** | Xem xe đang trong bãi, lượt đã hoàn tất, thu tiền, ghi tay và đối soát ảnh. |
| **Đăng ký xe** | Quản lý whitelist/blacklist, loại xe, chủ xe và bán/gia hạn vé tháng. |
| **Báo cáo** | Xem KPI, biểu đồ, ca trực, audit; xuất CSV/PDF. |
| **Cài đặt** | Khai báo nguồn video, nhận dạng, phí, QR, MoMo và dữ liệu. |

Thanh trên cùng có các nút **Bắt đầu**, **Dừng**, **Mở barrier**, **Đóng barrier** và **Mở/Đóng ca**. Thanh trạng thái dưới cùng cho biết ứng dụng đang chạy hay dừng, số camera, số sự kiện, tốc độ nhận dạng và trạng thái cổng.

Các bảng đều có thanh cuộn dọc/ngang. Vùng camera, Bãi xe và Cài đặt có thanh chia kéo được; trên màn hình nhỏ có thể cuộn phần nội dung để tới các trường phía dưới.

## 2. Checklist đầu ca

1. Mở `OCR_Plate.exe` hoặc chạy `python app.py`.
2. Nếu màn hình đăng nhập xuất hiện, đăng nhập bằng tài khoản cá nhân; không dùng chung tài khoản `admin` cho cả ca.
3. Bấm **Mở ca**, nhập đúng quỹ tiền mặt đầu ca rồi xác nhận.
4. Mở thẻ **Cài đặt**, kiểm tra mỗi nguồn có đúng chiều `IN` hoặc `OUT`.
5. Bấm **Bắt đầu**.
6. Sang **Giám sát**, xác nhận:
   - tất cả camera cần dùng có hình;
   - khung ROI nằm tại vị trí xe dừng;
   - trạng thái không báo mất kết nối;
   - barrier mô phỏng phản hồi khi bấm mở/đóng thủ công.
7. Nếu thu chuyển khoản, kiểm tra dòng trạng thái đối soát trong **Cài đặt → Thu tiền QR** phải là đang theo dõi, không phải `Tắt` hoặc cảnh báo token.
8. Chạy một lượt thử IN/OUT trước khi đón xe thật.

## 3. Luồng xe vào

Khi camera `IN` nhận dạng đủ độ tin cậy:

1. Ứng dụng ghi một sự kiện và ảnh snapshot.
2. Hệ thống kiểm tra đăng ký xe và chính sách cổng.
3. Nếu được phép, barrier mở và một lượt `INSIDE` xuất hiện trong **Xe đang trong bãi**.
4. Nếu bị từ chối, barrier giữ đóng, bảng/simulator báo đỏ và audit ghi lý do.

| Kết quả | Khi nào xảy ra | Barrier |
| --- | --- | --- |
| `ALLOW` | Xe `ALLOW` đang hoạt động và còn hiệu lực. | Mở. |
| `GUEST` | Xe lạ trong chế độ `all`. | Mở. |
| `DENY` | Xe blacklist; hoặc xe lạ/hết hạn trong `registered_only`. | Giữ đóng. |

Nếu camera đọc lại cùng biển khi xe vẫn đang trong bãi, ứng dụng gắn sự kiện vào lượt đang mở và không tạo lượt thứ hai.

## 4. Luồng xe ra và dialog thu tiền

Khi camera `OUT` nhận dạng một biển đang có lượt `INSIDE`:

1. Ứng dụng ghép ảnh vào/ra, tính thời gian và phí.
2. Lượt chuyển thành `COMPLETED`.
3. Xe đăng ký hợp lệ hoặc lượt có phí 0 chuyển thành **Miễn**.
4. Lượt có phí chuyển thành **Chưa thanh toán** và dialog VietQR tự mở.

Dialog hiển thị biển số, số tiền, QR và nội dung chuyển khoản dạng `GX<id lượt> <biển số>`. Trong dialog chỉ có nút xác nhận **Đã thu tiền mặt**; quét QR không tự đánh dấu đã thu.

> Hành vi hiện tại: quyết định mở barrier được xử lý ngay khi nhận sự kiện OUT, trước phần thu tiền. Thanh toán chưa khóa barrier. Nếu quy trình cơ sở yêu cầu “thu đủ mới mở”, phải dùng barrier thủ công hoặc sửa logic trước khi triển khai thật.

### OUT không có IN

Nếu không tìm thấy lượt vào cùng biển, hệ thống tạo lượt `REVIEW` với cảnh báo **Không có lượt vào**. Nhân viên cần mở đối soát, kiểm tra ảnh và xử lý theo quy định của bãi; hệ thống không tự suy đoán một lượt khác.

## 5. Thu tiền

Chỉ thu trên lượt đã hoàn tất, có phí và đang **Chưa thanh toán**. Ứng dụng chặn thu lại lượt đã **Đã thu** hoặc lượt **Miễn**.

### 5.1 Tiền mặt

Có hai cách:

- Chọn một hoặc nhiều lượt trong bảng rồi bấm **Thu tiền mặt**.
- Trong dialog QR tự mở khi OUT, nhận tiền rồi bấm **Đã thu tiền mặt**.

Ngay khi bấm, lượt chuyển thành **Đã thu**, phương thức được lưu là `CASH`, người thu và ca trực hiện tại được ghi lại. Không bấm nút này khi khách mới chỉ quét QR hoặc đang mở màn hình chuyển khoản.

### 5.2 Chuyển khoản VietQR

1. Giữ dialog QR đang mở.
2. Yêu cầu khách kiểm tra đúng tên chủ tài khoản và số tiền.
3. Khách chuyển khoản và giữ nguyên nội dung `GX...`.
4. Chờ dòng trạng thái đổi thành **Đã nhận đủ tiền** và dialog tự đóng.
5. Kiểm tra bảng đã hiện **Đã thu** trước khi kết thúc giao dịch.

Tự xác nhận chỉ hoạt động khi đã cấu hình SePay/Casso và có Internet. Nếu dịch vụ đối soát đang tắt, QR vẫn quét được nhưng ứng dụng không biết tiền đã về; nhân viên phải kiểm tra báo có theo quy trình nội bộ, không dùng nút tiền mặt để giả lập một giao dịch ngân hàng.

### 5.3 MoMo

1. Chọn đúng một lượt chưa thanh toán.
2. Bấm **Thu MoMo**.
3. Chờ ứng dụng tạo QR giao dịch merchant.
4. Khách quét bằng MoMo và hoàn tất thanh toán.
5. Ứng dụng truy vấn trạng thái định kỳ; chỉ khi MoMo trả về thành công và đủ tiền mới chuyển lượt thành **Đã thu**.

Nếu hiện thông báo thiếu Partner Code, Access Key hoặc Secret Key, báo quản trị viên; nhân viên không tự nhập thông tin tài khoản MoMo cá nhân.

### 5.4 Ý nghĩa trạng thái thanh toán

| Trạng thái | Xử lý |
| --- | --- |
| **Chưa thanh toán** | Chưa có xác nhận tiền; tiếp tục thu hoặc xử lý theo quy định. |
| **Đã thu** | Đã ghi nhận một lần; không thu lại. |
| **Miễn** | Xe đăng ký hợp lệ hoặc phí bằng 0; không thu. |

Chi tiết điều kiện ghép giao dịch nằm tại [Thanh toán & đối soát](THANH-TOAN-VA-DOI-SOAT.md).

## 6. Đối soát ảnh và sửa dữ liệu

Chọn một lượt trong bảng **Lượt gửi xe & thu phí**, sau đó:

- Bấm **Đối soát** hoặc bấm đúp: xem ảnh xe vào và xe ra cạnh nhau, thời gian, camera, biển OCR và độ tin cậy.
- Bấm **Đúng xe, bỏ cảnh báo** chỉ sau khi đã so ảnh và chắc chắn cùng xe.
- Bấm **Sửa biển** khi OCR lưu sai ký tự. Thao tác cập nhật biển của lượt và sự kiện liên quan, đồng thời ghi audit.
- Bấm **Đổi loại xe** để tính lại phí theo loại xe mới. Lượt đã thu tiền không được tính lại phí.

Các cờ cảnh báo:

| Cờ hiển thị | Nguyên nhân |
| --- | --- |
| Đọc biển yếu | Một trong hai đầu có độ tin cậy dưới 60%. |
| Gửi quá ngắn | Thời gian giữa IN và OUT dưới 30 giây. |
| Ghi tay | Lượt có sự kiện vào được nhập thủ công. |
| Không có lượt vào | Có OUT nhưng không tìm thấy IN đang mở. |

## 7. Ghi xe thủ công

Dùng phần **Ghi thủ công** trong thẻ **Bãi xe** khi camera không đọc được, mất vé hoặc xe đã được xử lý ngoài hệ thống:

1. Nhập biển số, chọn loại xe.
2. Bấm **Ghi xe VÀO** hoặc **Ghi xe RA** đúng chiều thực tế.
3. Kiểm tra lượt vừa tạo và mở đối soát nếu có cảnh báo.

Ghi tay không có ảnh và được đánh dấu để kiểm tra. Sự kiện ghi tay không tự gọi lệnh mở barrier; nếu cần cho xe qua, dùng nút mở thủ công riêng.

## 8. Mở và đóng barrier thủ công

- **Mở barrier**: gửi lệnh mở đến mô phỏng và backend phần cứng đã cấu hình; ghi audit `GATE_MANUAL`.
- **Đóng barrier**: gửi lệnh đóng; ghi audit `GATE_MANUAL_CLOSE`.

Hai nút này bỏ qua whitelist, trạng thái thanh toán và không tự tạo lượt IN/OUT. Trước khi bấm phải nhìn làn xe trực tiếp, kiểm tra vùng an toàn và ghi xe thủ công nếu camera đã bỏ sót.

Nếu barrier mô phỏng đổi trạng thái nhưng cổng thật không chạy, dừng sử dụng điều khiển tự động và báo kỹ thuật; xem [Xử lý sự cố](XU-LY-SU-CO.md#7-barrier).

## 9. Đăng ký xe, blacklist và vé tháng

### Đăng ký hoặc cập nhật xe

1. Mở thẻ **Đăng ký xe**.
2. Nhập biển số, chủ xe, số điện thoại và loại xe.
3. Chọn:
   - `ALLOW`: whitelist, được phép qua khi còn hiệu lực và được miễn phí lượt;
   - `DENY`: blacklist, luôn bị từ chối.
4. Bấm **Lưu / Cập nhật**.

Chọn một dòng trong danh sách để nạp lại thông tin lên biểu mẫu. Nút **Xóa** xóa đăng ký đã chọn; cần kiểm tra biển trước khi thao tác.

### Bán hoặc gia hạn vé tháng

1. Nhập/chọn biển và đúng loại xe.
2. Chọn số tháng; ứng dụng đề xuất giá theo cấu hình.
3. Kiểm tra và điều chỉnh số tiền nếu quy định cho phép.
4. Bấm **Bán / Gia hạn vé tháng**.

Gia hạn sớm cộng tiếp từ hạn hiện tại; vé mới tính từ ngày hiện tại. Lịch sử bán vé và người bán được lưu. Doanh thu vé tháng được gắn vào ca đang mở và hiện được giả định là tiền mặt khi đối soát ca.

## 10. Báo cáo và xuất file

Trong thẻ **Báo cáo**:

1. Chọn nhanh **Hôm nay**, **7 ngày**, **30 ngày**, **Tháng này**; hoặc nhập `dd/mm/yyyy`.
2. Bấm **Cập nhật**.
3. Xem ba nhóm:
   - **Doanh thu**: đã thu/chưa thu, doanh thu ngày, hình thức và người thu;
   - **Lưu lượng & thời gian**: theo giờ, theo thứ, nhóm thời gian gửi;
   - **Chi tiết & đối soát**: top biển, lý do từ chối, ca trực và audit.
4. Mở menu **Xuất báo cáo**, chọn **CSV** hoặc **PDF**.
5. Chọn nơi lưu. Khi hoàn tất, dialog hiển thị đường dẫn; bấm **Mở file** để mở bằng ứng dụng mặc định.

PDF có biểu đồ doanh thu theo ngày, lưu lượng theo giờ, hình thức thanh toán và thời gian gửi xe cùng các bảng chi tiết. Hai nút xuất CSV trong **Cài đặt** là dữ liệu thô của sự kiện/lượt, khác với báo cáo tổng hợp theo khoảng ngày.

## 11. Đóng ca và bàn giao

1. Xử lý các lượt tiền mặt chưa cập nhật và kiểm tra danh sách **Chưa thanh toán**.
2. Bấm **Đóng ca**.
3. Đối chiếu quỹ đầu ca, tiền mặt vé lượt, vé tháng và phần chuyển khoản hiển thị.
4. Đếm tiền mặt thực tế, nhập vào **Tiền mặt đếm được** và thêm ghi chú nếu lệch.
5. Bấm **Đóng ca**, đọc kết quả khớp/lệch.
6. Xuất báo cáo ngày hoặc ca theo quy trình cơ sở.
7. Nếu có quyền admin, thực hiện sao lưu CSDL.
8. Bấm **Dừng** trước khi tắt máy hoặc đóng ứng dụng.

> Giới hạn phiên bản hiện tại: bộ tính ca chỉ nhận phương thức đúng bằng `QR` là không tiền mặt; giao dịch tự động `BANK` và `MOMO` hiện bị gộp vào tiền mặt phải có. Khi có hai phương thức này, phải đối chiếu thêm báo cáo giao dịch và không dùng số “Tiền mặt phải có” làm số quyết toán cuối cùng cho tới khi logic được sửa.

## 12. Quyền người dùng

| Chức năng | `operator` | `admin` |
| --- | --- | --- |
| Camera, thu tiền, ghi tay, đối soát, ca trực | Có | Có |
| Mở/đóng barrier thủ công | Có | Có |
| Sửa cấu hình trên giao diện | Có | Có |
| Quản lý tài khoản | Không | Có |
| Sao lưu CSDL | Không | Có |
| Dọn dữ liệu cũ / xóa dữ liệu nhận dạng | Không | Có |

Nếu `require_login=false`, ứng dụng không yêu cầu đăng nhập và các thao tác bảo trì xem phiên hiện tại như có quyền admin. Chế độ này chỉ phù hợp khi thử nghiệm.

## 13. Checklist cuối ca

- [ ] Không còn giao dịch tiền mặt đã nhận nhưng vẫn hiện **Chưa thanh toán**.
- [ ] Các lượt cảnh báo quan trọng đã được đối soát ảnh.
- [ ] Barrier đã đóng và làn xe an toàn.
- [ ] Ca đã đóng, số lệch đã được ghi chú.
- [ ] Báo cáo đã xuất và mở thử được.
- [ ] CSDL và ảnh snapshot đã được sao lưu theo chính sách cơ sở.
- [ ] Ứng dụng đã dừng nhận dạng trước khi tắt máy.

