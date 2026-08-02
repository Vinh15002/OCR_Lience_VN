# Thanh toán và đối soát

OCR Plate có ba luồng thanh toán độc lập: VietQR chuyển khoản ngân hàng, đọc giao dịch qua SePay/Casso, và MoMo merchant. Tài liệu này mô tả đúng cách ứng dụng hiện xác nhận tiền.

## 1. Phân biệt ba cơ chế

| Cơ chế | Tạo mã | Ai xác nhận tiền? | Cần Internet |
| --- | --- | --- | --- |
| VietQR | Ứng dụng tạo payload NAPAS/EMVCo tại máy. | Không tự xác nhận. | Không để tạo mã; app ngân hàng của khách vẫn cần mạng. |
| SePay/Casso | Dùng cùng mã VietQR. | OCR Plate định kỳ đọc giao dịch đến từ API. | Có. |
| MoMo merchant | Gọi API MoMo để tạo một giao dịch riêng. | OCR Plate truy vấn trạng thái đơn hàng MoMo. | Có. |

Quét mã, mở app ngân hàng hoặc hiện màn hình xác nhận trên điện thoại **chưa phải là đã thu**. Trạng thái chỉ đổi sang `PAID` khi:

- nhân viên bấm **Đã thu tiền mặt**; hoặc
- SePay/Casso trả về một giao dịch ngân hàng khớp; hoặc
- MoMo trả về giao dịch thành công và số tiền đủ.

## 2. Cấu hình VietQR

Vào **Cài đặt → Thu tiền QR (VietQR)**:

1. Chọn ngân hàng từ dropdown 20 ngân hàng.
2. Nhập số tài khoản không có dấu cách hoặc dấu chấm.
3. Nhập tên chủ tài khoản.
4. Bấm **Lưu & kiểm tra**.
5. Thử quét một mã thật và đối chiếu ngân hàng, tài khoản, tên hiển thị và số tiền.

Danh sách hiện có: VietinBank, Vietcombank, BIDV, Agribank, MBBank, Techcombank, ACB, VPBank, TPBank, Sacombank, HDBank, VIB, SHB, Eximbank, MSB, OCB, LPBank, PVcomBank, ABBANK và SeABank.

Thông báo **Tài khoản hợp lệ** chỉ xác nhận định dạng BIN và số tài khoản tại máy. Ứng dụng không gọi ngân hàng để chứng minh tài khoản tồn tại hoặc tên chủ tài khoản đúng.

### Dữ liệu trong mã

Mỗi QR của một lượt có:

- BIN ngân hàng và số tài khoản nhận;
- tiền tệ VND;
- đúng số phí của lượt;
- nội dung `GX<visit_id> <plate>`, tối đa 25 ký tự.

Ví dụ minh họa: lượt 42, biển `59A112345` tạo nội dung `GX42 59A112345`. Không dùng cố định `GX1`; ID phải là ID hiện tại hiển thị trong mã.

Tên chủ tài khoản chỉ được ứng dụng hiển thị dưới QR, không nằm trong payload. Vì vậy bước quét thử trên app ngân hàng là bắt buộc khi nghiệm thu.

## 3. Khi nào dialog QR tự mở?

Dialog tự mở khi có sự kiện `OUT` thỏa cả bốn điều kiện:

1. sự kiện ghép được với một lượt vào;
2. lượt đã chuyển thành `COMPLETED`;
3. trạng thái thanh toán là `UNPAID`;
4. phí lớn hơn 0.

Ứng dụng chỉ giữ một dialog VietQR cho mỗi lượt. Mở dialog sẽ lưu mốc `requested_at`; mốc này là điều kiện để giao dịch ngân hàng được phép đối soát.

Trong dialog, nút **Đã thu tiền mặt** ghi phương thức `CASH`. Nút này không phải nút xác nhận chuyển khoản thủ công.

## 4. SePay

### 4.1 Trường API token nhận gì?

Trường **API token** chỉ nhận chuỗi Bearer API Token dùng để đọc giao dịch. Không nhập:

- tên đăng nhập SePay;
- mật khẩu SePay;
- thông tin đăng nhập Internet Banking;
- `client_id:client_secret` của một sản phẩm SePay khác.

Nếu bạn chỉ có username/password xác nhận API, bộ thông tin đó không tương thích trực tiếp với client hiện tại. Hãy tạo/cấp một API Token có quyền đọc giao dịch trong trang quản trị SePay, hoặc cần phát triển thêm luồng đổi credential lấy access token trước khi dùng.

Tài liệu chính thức: [SePay – tạo API token](https://developer.sepay.vn/vi/sepay-api/v2/tao-api-token) và [API danh sách giao dịch](https://developer.sepay.vn/vi/sepay-api/v2/giao-dich/danh-sach).

### 4.2 Cấu hình trong ứng dụng

1. Tài khoản ngân hàng phải được liên kết và có giao dịch trên SePay.
2. Vào **Cài đặt → Thu tiền QR → Tự động xác nhận đã nhận tiền**.
3. Chọn dịch vụ `sepay`.
4. Nhập API Token, không thêm chữ `Bearer`.
5. Đặt chu kỳ; giao diện cho phép tối thiểu 5 giây, mặc định 20 giây.
6. Bấm **Áp dụng**.
7. Chờ trạng thái `✓ Đang theo dõi`.

Client tự thêm header `Authorization: Bearer <token>` và lọc theo `bank_account` đã khai báo.

> Tương thích hiện tại: code đang gọi endpoint SePay v1 `https://my.sepay.vn/userapi/transactions/list`. SePay ghi rõ v1 không còn được phát triển cho tích hợp mới; cần lập kế hoạch chuyển client sang v2. Không thay URL trong `config.json` vì URL đang cố định trong code.

## 5. Casso

1. Liên kết tài khoản ngân hàng trên Casso và tạo API Key có quyền đọc giao dịch.
2. Chọn dịch vụ `casso`.
3. Dán API Key vào **API token**, không thêm tiền tố.
4. Bấm **Áp dụng** và theo dõi trạng thái.

Client gọi `https://oauth.casso.vn/v2/transactions` với header `Authorization: Apikey <token>`. Tài liệu chính thức: [Casso – API lấy giao dịch](https://developer.casso.vn/casso-api/api/lay-giao-dich).

Phiên bản hiện tại chưa truyền bộ lọc số tài khoản vào API Casso; việc ghép vẫn dựa trên nội dung, số tiền và mốc thời gian. Nếu tài khoản Casso chứa nhiều tài khoản ngân hàng, cần nghiệm thu kỹ để tránh dữ liệu ngoài phạm vi.

## 6. Quy tắc ghép giao dịch ngân hàng

Một giao dịch chỉ có thể xác nhận một lượt khi:

- lượt đang `COMPLETED`, `UNPAID` và có phí lớn hơn 0;
- dialog QR của lượt đã từng được mở;
- thời gian giao dịch từ provider đọc được và không sớm hơn lúc mở QR;
- giao dịch là tiền vào, chưa từng được xử lý;
- số tiền và nội dung thỏa một trong hai nhánh dưới đây.

### Có mã `GX<id>` trong nội dung

- ID phải đúng một lượt đang chờ.
- Số tiền nhận phải bằng hoặc lớn hơn số phải thu.
- Nếu ID sai/đã thu, ứng dụng không bỏ mã GX rồi đoán sang lượt khác bằng số tiền.

### Không có mã GX

- Số tiền phải bằng chính xác số phải thu.
- Trong tất cả lượt đang chờ chỉ được có đúng một lượt nợ số tiền đó.
- Nếu hai lượt cùng nợ 5.000đ, giao dịch 5.000đ không có GX bị bỏ qua vì mơ hồ.

### Các giao dịch không được tự xác nhận

- tiền chuyển đến trước lúc mở QR;
- giao dịch thiếu hoặc có timestamp không đọc được;
- tiền ra hoặc số tiền bằng 0;
- chuyển thiếu;
- không có GX và số tiền thừa;
- không có GX và có nhiều lượt cùng số tiền;
- giao dịch có ID đã xử lý.

Khi khớp, lượt được ghi `payment_method=BANK`, người thu `(tự động)`, mã tham chiếu và audit `PAYMENT_BANK`; dialog đang mở báo nhận đủ rồi tự đóng.

## 7. MoMo merchant

### 7.1 Lấy Partner Code, Access Key và Secret Key

Ba giá trị này thuộc tài khoản **MoMo for Business/merchant**, không phải tài khoản ví MoMo cá nhân. Đăng ký và hoàn thành quy trình merchant với MoMo; sau đó lấy credential theo môi trường Testing/Production. Tham khảo [MoMo Developers – thiết lập credential](https://developers.momo.vn/v3/docs/payment/api/other/postman/) và [MoMo for Business](https://business.momo.vn/).

- `Partner Code`: định danh merchant.
- `Access Key`: khóa truy cập dùng khi tạo chữ ký.
- `Secret Key`: bí mật HMAC; không chia sẻ và không gửi trong ảnh chụp lỗi.

### 7.2 Cấu hình

1. Vào **Cài đặt → Ví điện tử MoMo**.
2. Chọn `sandbox` để thử nghiệm trước.
3. Nhập Partner Code, Access Key, Secret Key của cùng một môi trường.
4. Bấm **Lưu MoMo**.
5. Chọn một lượt chưa thanh toán, bấm **Thu MoMo** và chạy giao dịch thử.
6. Chỉ chuyển sang `production` khi bộ test nghiệp vụ đã đạt và credential production đã được cấp.

Thông báo sẵn sàng trên giao diện chỉ kiểm tra ba trường không rỗng; nó không gọi MoMo để xác thực trước. Giao dịch thử mới xác nhận credential và quyền merchant thực sự dùng được.

### 7.3 Luồng kỹ thuật hiện tại

1. Ứng dụng ký HMAC-SHA256 bằng Secret Key.
2. Gọi `/v2/gateway/api/create` với loại `captureWallet`.
3. Hiện `qrCodeUrl` hoặc `payUrl` dưới dạng QR.
4. Gọi `/v2/gateway/api/query` mỗi 5 giây; lỗi kết nối thử lại sau 10 giây.
5. Chỉ `resultCode=0` và `amount` phản hồi bằng hoặc lớn hơn phí mới ghi `PAID/MOMO`.

Giới hạn số tiền được code chấp nhận là 1.000đ đến 50.000.000đ. Ứng dụng dùng polling query; `ipnUrl` và `redirectUrl` hiện là URL placeholder của MoMo, không có webhook server tại chốt.

## 8. Kiểm thử nghiệm thu thanh toán

### VietQR + SePay/Casso

- [ ] QR hiện đúng ngân hàng, tài khoản, tên và số tiền.
- [ ] Chỉ quét nhưng chưa chuyển: lượt vẫn **Chưa thanh toán**.
- [ ] Chuyển đúng tiền, giữ `GX...`: app tự xác nhận sau chu kỳ poll.
- [ ] Chuyển thiếu: không xác nhận.
- [ ] Sửa sai GX: không xác nhận nhầm lượt khác.
- [ ] Hai lượt cùng tiền, bỏ GX: không xác nhận vì mơ hồ.
- [ ] Mất mạng: nhận dạng vẫn chạy, dòng feed báo lỗi.
- [ ] Khi mạng phục hồi, feed chạy lại mà không dùng lại một giao dịch cũ.

### MoMo

- [ ] Sandbox tạo được QR.
- [ ] Đóng dialog trước khi trả tiền không làm lượt thành đã thu.
- [ ] Giao dịch thành công đúng tiền chuyển sang **Đã thu**.
- [ ] Giao dịch hủy/chờ không chuyển trạng thái.
- [ ] Mất mạng hiển thị lỗi và thử lại, không làm app nhận dạng bị treo.

## 9. Khi SePay đã có giao dịch nhưng app không đổi trạng thái

Kiểm tra theo thứ tự:

1. Lượt còn là **Chưa thanh toán**, đã hoàn tất và phí lớn hơn 0 chưa?
2. Dialog QR của chính lượt đó đã được mở **trước** thời điểm giao dịch chưa?
3. Dịch vụ có hiện `✓ Đang theo dõi` hay đang báo HTTP 401/mất mạng?
4. Số tài khoản trong Cài đặt có đúng tài khoản đang được SePay đồng bộ không?
5. Nội dung có đúng `GX<id>` của lượt đó không?
6. Số tiền có thiếu không?
7. Provider có trả timestamp hợp lệ không?
8. Token có phải API Token đọc giao dịch, không phải username/password không?
9. Xem thẻ **Báo cáo → Chi tiết & đối soát → Nhật ký hệ thống** để tìm `PAYMENT_BANK`.

Nếu cần gửi log cho kỹ thuật, che toàn bộ token, số tài khoản, Secret Key và mật khẩu RTSP. Bản EXE ghi lỗi vào `logs/app.log`.

## 10. Bảo mật và quyết toán

- API token, Partner Code, Access Key và Secret Key được lưu dạng văn bản rõ trong `config.json`; chỉ ô nhập trên UI được che.
- Không đưa `config.json` thật vào bản build giao khách, email, chat hoặc Git.
- Token chỉ nên có quyền đọc giao dịch cần thiết; xoay vòng ngay khi nghi ngờ lộ.
- QR không cho phép phần mềm rút/chuyển tiền, nhưng token có thể làm lộ lịch sử giao dịch.
- Bản hiện tại phân loại `BANK` và `MOMO` sai vào tiền mặt khi tính tiền phải có cuối ca. Phải đối chiếu thêm sao kê/provider cho đến khi logic được sửa.
- Không có chức năng hoàn tiền hoặc chuyển một lượt `PAID` về `UNPAID` trên giao diện; hoàn tiền phải theo quy trình bên ngoài và được ghi nhận riêng.

