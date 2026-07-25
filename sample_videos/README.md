# Sample videos

## `vietnam_motorbike_intersection.mp4`

- Source: https://www.pexels.com/video/busy-vietnamese-street-scene-with-motorbikes-29746894/
- Purpose: default test video for the application.
- Downloaded resolution: 1920x1080, approximately 35 seconds.
- License information: https://www.pexels.com/license/

## `vietnam_motorbike_test.mp4`

- Source: https://pixabay.com/videos/traffic-motorcycle-traffic-jam-city-129209/
- Purpose: short secondary test video.
- Downloaded resolution: 1920x1080, approximately 6 seconds.
- License information: https://pixabay.com/service/license-summary/

## `vietnam_dense_day.mp4`

- Source: https://www.pexels.com/video/motorcycle-riders-dominating-the-traffic-in-a-road-in-vietnam-3125979/
- Scenario: dense daytime motorcycle traffic.
- Resolution: 2560x1440, approximately 54 seconds.
- License information: https://www.pexels.com/license/

## `vietnam_hanoi_intersection.mp4`

- Source: https://www.pexels.com/video/busy-city-intersection-with-motorbike-traffic-31343455/
- Scenario: portrait view of a busy Hanoi intersection.
- Resolution: 1080x1920, approximately 10 seconds.
- License information: https://www.pexels.com/license/

## `vietnam_night_traffic_1080p.mp4`

- Source: https://www.pexels.com/video/nighttime-urban-motorcycle-traffic-in-city-29185677/
- Scenario: nighttime motorcycle traffic in Ho Chi Minh City.
- Resolution: 1920x1080. The local test copy is trimmed to 30 seconds.
- License information: https://www.pexels.com/license/

## `VideoCar1.mp4`

- Source: unrecorded (added directly via commit `adb4b3e`; original source URL unknown).
- Scenario: Vietnamese CCTV, elevated fixed-angle view over a motorbike lane, plates legible (e.g. `51G-270.93`, `61-E1 749.54`).
- Resolution: 1280x720, approximately 15 seconds. Has a burned-in timestamp overlay in the corner.
- Closest match so far to the intended dedicated gate-camera angle; prefer this over the wide dashcam-style clips above when testing ROI/ OCR accuracy.

## `VideoCar.mp4`

- Source: unrecorded (added directly via commit `0b3c5f8`; original source URL unknown).
- **Not representative of this project's goal**: this is UK highway dashcam footage of cars (van, Mercedes, Nissan Qashqai) with British-format plates (e.g. `EY61 NBG`), not Vietnamese motorbikes. The detector/OCR pipeline here targets Vietnamese motorbike plates, so this clip will not produce meaningful detections.
- Resolution: 1280x720, approximately 38 seconds.
- Kept for now but should not be used to validate or benchmark the recognition pipeline; consider removing if it's not needed for another purpose.

These stock videos are useful for verifying video input and the recognition pipeline. Their camera distance is not representative of a dedicated gate camera, so they should not be used to set the final OCR accuracy target.
