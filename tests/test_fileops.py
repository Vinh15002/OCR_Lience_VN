import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from plate_app import fileops


class OpenExportedFileTests(unittest.TestCase):
    def test_windows_uses_the_file_association(self):
        with tempfile.TemporaryDirectory() as directory:
            exported = Path(directory) / "bao_cao.pdf"
            exported.write_bytes(b"%PDF-test")
            with (
                mock.patch.object(fileops.sys, "platform", "win32"),
                mock.patch.object(fileops.os, "startfile", create=True) as startfile,
            ):
                fileops.open_with_default_app(exported)
            startfile.assert_called_once_with(str(exported.resolve()))

    def test_missing_export_is_not_launched(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pdf"
            with self.assertRaises(FileNotFoundError):
                fileops.open_with_default_app(missing)


class ReportExportDispatchTests(unittest.TestCase):
    def test_pdf_option_uses_pdf_exporter_and_extension(self):
        from plate_app import analytics
        from plate_app.ui import PlateApp

        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "bao_cao.csv"
            expected = selected.with_suffix(".pdf")
            span = analytics.DateRange(date(2026, 8, 1), date(2026, 8, 2))
            fake_app = SimpleNamespace(
                _report_range=mock.Mock(return_value=span),
                event_store=object(),
                app_config=SimpleNamespace(parking_capacity=25),
                _show_export_complete=mock.Mock(),
            )
            with (
                mock.patch("plate_app.ui.filedialog.asksaveasfilename", return_value=str(selected)),
                mock.patch("plate_app.ui.analytics.export_report_pdf", return_value=expected) as exporter,
            ):
                PlateApp._export_report(fake_app, "pdf")

            exporter.assert_called_once_with(fake_app.event_store, span, expected, capacity=25)
            fake_app._show_export_complete.assert_called_once_with(
                expected, "Đã xuất báo cáo PDF thành công."
            )


if __name__ == "__main__":
    unittest.main()
