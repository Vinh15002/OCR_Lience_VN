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

## Chạy nhanh

```powershell
python app.py
```

Trong ứng dụng:

1. Chọn **Add video** để thử với file video; hoặc nhập `0` rồi chọn **Add camera/RTSP** để dùng webcam đầu tiên.
   Có thể chọn nhanh một file trong danh sách **Sample** rồi nhấn **Use sample**; app chỉ bật một sample tại một thời điểm để tránh quá tải CPU.
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
- `detection_interval_seconds`: khoảng nghỉ giữa hai lần detect trên cùng camera; mặc định 0,5 giây và luôn xử lý frame mới nhất.
- `ocr_recognition_model`: mặc định dùng PP-OCRv6 tiny để CPU xử lý nhanh hơn. Biển xe máy được tự tách thành hai dòng trước khi OCR.
- `min_votes`: số lần OCR giống nhau trước khi tạo sự kiện.
- `vote_window_seconds`: khoảng thời gian gom phiếu OCR.
- `duplicate_cooldown_seconds`: không ghi lại cùng biển trên cùng camera trong khoảng này.
- `recognized_cache_seconds`: thời gian tối đa giữ kết quả OCR đã xác nhận của một xe đang trong ROI.
- `track_max_missed_frames`: số frame detector được phép hụt trước khi coi xe đã rời ROI.
- `max_plates_per_frame`: số biển tối đa xử lý trong một frame.

Nếu camera bị giật, tăng `frame_skip` từ 3 lên 5. Nếu xe chạy nhanh và thường không đủ hai phiếu OCR, giảm `frame_skip` hoặc tạm đặt `min_votes` bằng 1 để khảo sát, nhưng không nên dùng cấu hình một phiếu khi điều khiển barrier.

## Dữ liệu đầu ra

- `data/events.db`: cơ sở dữ liệu SQLite.
- `data/snapshots/`: ảnh bằng chứng khi một biển được xác nhận.
- CSV được tạo khi chọn **Export CSV**.

## Kiểm tra

```powershell
python -m unittest discover -s tests -v
python -m compileall app.py plate_app tests
```

## Lưu ý trước khi dùng trong doanh nghiệp

Phiên bản hiện tại vẫn dùng Ultralytics và model hiện có trong workspace. Cần rà soát giấy phép Ultralytics hoặc chuyển detector trước khi đóng gói thương mại. Không đưa mật khẩu RTSP thật vào repository; nên tách secret ra khỏi `config.json` khi triển khai chính thức.
