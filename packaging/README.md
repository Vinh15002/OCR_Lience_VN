# Đóng gói OCR_Plate thành ứng dụng Windows

Thư mục này chứa mọi thứ cần để tạo bản chạy độc lập (không cần cài Python) cho
Windows 10/11 64-bit.

| File | Vai trò |
| --- | --- |
| `launcher.py` | Điểm khởi động của bản đóng gói: cố định thư mục làm việc, ghi log, nạp sẵn model OCR |
| `OCR_Plate.spec` | Cấu hình PyInstaller (gom `paddle`, `paddlex`, `paddleocr`, `ultralytics`, `torch`…) |
| `build_exe.ps1` | Chạy PyInstaller rồi chép các file runtime vào cạnh file `.exe` |

## Build

Trên máy dev (máy đã chạy được `python app.py`):

```powershell
pip install pyinstaller
.\packaging\build_exe.ps1 -Clean
```

Tuỳ chọn:

- `-SkipSampleVideos` — bỏ thư mục `sample_videos` (~300 MB) khỏi gói.
- `-Clean` — xoá `build/` và `dist/` cũ trước khi build.

Build mất khoảng 10–20 phút. Kết quả nằm ở `dist\OCR_Plate\`:

```
dist\OCR_Plate\
├─ OCR_Plate.exe            <- chạy file này
├─ config.json              <- sửa được, không cần build lại
├─ license_plate_detector.pt
├─ data\                    <- CSDL + ảnh chụp sinh ra khi chạy
├─ logs\app.log             <- stdout/stderr của ứng dụng
├─ sample_videos\           (nếu không dùng -SkipSampleVideos)
└─ _internal\               <- thư viện + model OCR, đừng sửa
```

## Mang đi chạy

Chép nguyên thư mục `dist\OCR_Plate` sang máy khác (USB, ổ mạng…) rồi bấm đúp
`OCR_Plate.exe`. Máy đích **không** cần Python, không cần cài PaddleOCR, và
không cần internet: model nhận dạng `PP-OCRv6_medium_rec` và `PP-OCRv6_tiny_rec`
đã nằm sẵn trong gói, lần chạy đầu sẽ tự chép vào `%USERPROFILE%\.paddlex`.

Yêu cầu máy đích: Windows 10/11 x64, CPU hỗ trợ AVX (Paddle yêu cầu), khoảng
4 GB trống.

## Kiểm tra nhanh trên máy đích

```powershell
.\OCR_Plate.exe --self-test
```

Lệnh này nạp thử YOLO + PaddleOCR rồi ghi kết quả ra `logs\selftest.log`
(`SELFTEST PASSED` là đạt). Chạy nó trước khi demo để biết chắc máy đích đủ điều
kiện, không cần cắm camera.

## Khi ứng dụng không mở lên

Bản build chạy chế độ cửa sổ (không có console), nên mọi thông báo lỗi đi vào
`logs\app.log` cạnh file `.exe`. Mở file đó trước tiên.

Nếu cần xem lỗi trực tiếp trong console, sửa `console=False` thành `console=True`
trong `OCR_Plate.spec` rồi build lại.

## Cập nhật code

Sau khi sửa `plate_app/`, build lại bằng lệnh trên. Riêng `config.json`,
`license_plate_detector.pt` và `sample_videos` là file thường nằm cạnh `.exe` —
có thể thay trực tiếp trong `dist\OCR_Plate\` mà không cần build lại.
