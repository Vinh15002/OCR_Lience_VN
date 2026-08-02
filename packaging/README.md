# Đóng gói OCR_Plate thành ứng dụng Windows

Thư mục này chứa mọi thứ cần để tạo bản chạy độc lập (không cần cài Python) cho
Windows 10/11 64-bit.

Tài liệu tổng thể: [docs/README.md](../docs/README.md). Hướng dẫn này chỉ nói về build và bàn giao bản Windows.

| File | Vai trò |
| --- | --- |
| `launcher.py` | Điểm khởi động của bản đóng gói: cố định thư mục làm việc, ghi log, nạp sẵn model OCR |
| `OCR_Plate.spec` | Cấu hình PyInstaller (gom `paddle`, `paddlex`, `paddleocr`, `ultralytics`, `torch`…) |
| `build_exe.ps1` | Chạy PyInstaller rồi chép các file runtime vào cạnh file `.exe` |

## Build

Trên máy dev (máy đã chạy được `python app.py`):

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m unittest discover -s tests -v
python -m compileall -q app.py plate_app tests

# Tải sẵn cả hai model OCR vào cache để spec có thể đóng gói offline
python -c "from paddleocr import TextRecognition; [TextRecognition(model_name=n, device='cpu') for n in ('PP-OCRv6_medium_rec','PP-OCRv6_tiny_rec')]"

.\packaging\build_exe.ps1 -Clean
```

Tuỳ chọn:

- `-SkipSampleVideos` — bỏ thư mục `sample_videos` (~300 MB) khỏi gói.
- `-Clean` — xoá toàn bộ `build/` và `dist/` cũ trước khi build; chuyển bản phát hành cần giữ sang nơi khác trước khi chạy.

Build mất khoảng 10–20 phút. Kết quả nằm ở `dist\OCR_Plate\`:

```
dist\OCR_Plate\
├─ OCR_Plate.exe            <- chạy file này
├─ config.json              <- sửa được, không cần build lại
├─ license_plate_detector.pt
├─ docs\                    <- hướng dẫn vận hành/cấu hình/kỹ thuật
├─ data\                    <- CSDL + ảnh chụp sinh ra khi chạy
├─ logs\app.log             <- stdout/stderr của ứng dụng
├─ sample_videos\           (nếu không dùng -SkipSampleVideos)
└─ _internal\               <- thư viện + model OCR, đừng sửa
```

`OCR_Plate.spec` hiện chỉ cảnh báo rồi tiếp tục nếu model OCR chưa có trong cache. Trước khi bàn giao, kiểm tra hai thư mục sau tồn tại lúc build:

```powershell
$models = @(
    "$env:USERPROFILE\.paddlex\official_models\PP-OCRv6_medium_rec",
    "$env:USERPROFILE\.paddlex\official_models\PP-OCRv6_tiny_rec"
)
foreach ($model in $models) {
    if (-not (Test-Path -LiteralPath $model)) { throw "Thiếu OCR model: $model" }
}
```

### Cảnh báo cấu hình bí mật

Script build hiện ưu tiên chép `config.json` thật nếu file tồn tại. File đó có thể chứa token SePay/Casso, MoMo Secret Key và mật khẩu RTSP. Trước khi nén hoặc bàn giao, bắt buộc thay bằng cấu hình sạch:

```powershell
Copy-Item .\config.example.json .\dist\OCR_Plate\config.json -Force
```

Không đưa config production vào Git, email hoặc gói demo. Cấu hình bí mật được nhập tại máy đích sau khi đã phân quyền thư mục.

## Mang đi chạy

Chép nguyên thư mục `dist\OCR_Plate` sang máy khác rồi bấm đúp
`OCR_Plate.exe`. Máy đích **không** cần Python, không cần cài PaddleOCR, và
không cần internet cho nhận dạng nếu model đã được đóng gói: `PP-OCRv6_medium_rec` và `PP-OCRv6_tiny_rec`
đã nằm sẵn trong gói, lần chạy đầu sẽ tự chép vào `%USERPROFILE%\.paddlex`.

VietQR tạo được offline, nhưng SePay/Casso và MoMo cần Internet. Không đặt `data/events.db` trên USB, OneDrive hoặc ổ mạng đồng bộ; chạy dữ liệu trên SSD cục bộ và sao lưu ra nơi khác.

Yêu cầu máy đích: Windows 10/11 x64, CPU hỗ trợ AVX, tối thiểu 6 GB trống; khuyến nghị còn 10 GB trở lên để chứa model cache, ảnh snapshot, DB, log và báo cáo.

## Kiểm tra nhanh trên máy đích

```powershell
.\OCR_Plate.exe --self-test
```

Lệnh này nạp thử YOLO + PaddleOCR rồi ghi kết quả ra `logs\selftest.log`
(`SELFTEST PASSED` là đạt). Chạy nó trước khi demo để biết chắc máy đích đủ điều
kiện, không cần cắm camera.

Có thể kiểm tra exit code và log bằng PowerShell:

```powershell
$process = Start-Process -FilePath .\OCR_Plate.exe -ArgumentList "--self-test" -Wait -PassThru
Get-Content -Encoding UTF8 .\logs\selftest.log
if ($process.ExitCode -ne 0) { throw "Self-test thất bại" }
```

Self-test chưa kiểm tra camera, ghi DB, QR, provider ngân hàng, MoMo, PDF hoặc barrier thật. Sau đó phải chạy checklist nghiệm thu trong [Hướng dẫn lắp đặt](../docs/HUONG-DAN-CAI-DAT.md#7-nghiệm-thu-trước-khi-chạy-thật).

## Khi ứng dụng không mở lên

Bản build chạy chế độ cửa sổ (không có console), nên mọi thông báo lỗi đi vào
`logs\app.log` cạnh file `.exe`. Mở file đó trước tiên.

Nếu cần xem lỗi trực tiếp trong console, sửa `console=False` thành `console=True`
trong `OCR_Plate.spec` rồi build lại.

## Cập nhật code

Sau khi sửa `plate_app/`, build lại bằng lệnh trên. Riêng `config.json`,
`license_plate_detector.pt` và `sample_videos` là file thường nằm cạnh `.exe` —
có thể thay trực tiếp trong `dist\OCR_Plate\` mà không cần build lại.

Script build chép thư mục [`docs`](../docs/) vào cạnh EXE để nhân viên có hướng dẫn vận hành, thanh toán và xử lý sự cố.

## Sao lưu và nâng cấp máy đích

Trước khi thay phiên bản:

1. Đóng ứng dụng.
2. Sao lưu `config.json`, `data/events.db` và toàn bộ `data/snapshots`.
3. Giữ bản cũ để quay lui.
4. Chép bản mới vào thư mục mới, đưa cấu hình/dữ liệu vào theo quy trình nội bộ.
5. Chạy self-test rồi nghiệm thu camera, payment và barrier.

Nút backup trong app chỉ sao lưu DB vào `data/backups`; không gồm snapshots/config/model/log.
