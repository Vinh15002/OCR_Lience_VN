# OCR Plate – ứng dụng nhận diện biển số xe máy

Ứng dụng desktop đọc video, webcam hoặc camera RTSP. Mặc định hệ thống chỉ chạy detector trong vùng giữa khung hình (ROI), phù hợp với một camera cố định hướng vào làn xe máy.

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

- `roi_width`, `roi_height`: tỷ lệ vùng xử lý ở chính giữa ảnh.
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

SQLite gồm ba bảng chính: `cameras`, `plate_events` và `vehicle_visits`. Sự kiện `IN` mở một lượt `INSIDE`; sự kiện `OUT` đóng lượt thành `COMPLETED`. Nếu chỉ có `OUT` mà không tìm thấy lần vào, lượt được đánh dấu `REVIEW` thay vì bị bỏ qua. Database cũ được tự nâng cấp khi khởi động ứng dụng.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python -m compileall app.py plate_app tests
```

## Lưu ý trước khi dùng trong doanh nghiệp

Phiên bản hiện tại vẫn dùng Ultralytics và model hiện có trong workspace. Cần rà soát giấy phép Ultralytics hoặc chuyển detector trước khi đóng gói thương mại. Không đưa mật khẩu RTSP thật vào repository; nên tách secret ra khỏi `config.json` khi triển khai chính thức.
