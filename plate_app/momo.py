"""Minimal MoMo merchant API client for desktop QR collection."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class MomoError(RuntimeError):
    pass


@dataclass(frozen=True)
class MomoPayment:
    order_id: str
    request_id: str
    qr_data: str
    pay_url: str = ""


@dataclass(frozen=True)
class MomoClient:
    partner_code: str = ""
    access_key: str = ""
    secret_key: str = ""
    environment: str = "sandbox"
    timeout: float = 30.0

    @property
    def problem(self) -> str:
        missing = []
        if not self.partner_code.strip():
            missing.append("Partner Code")
        if not self.access_key.strip():
            missing.append("Access Key")
        if not self.secret_key.strip():
            missing.append("Secret Key")
        return "Thiếu " + ", ".join(missing) if missing else ""

    @property
    def base_url(self) -> str:
        if self.environment.strip().lower() == "production":
            return "https://payment.momo.vn"
        return "https://test-payment.momo.vn"

    def _signature(self, raw: str) -> str:
        return hmac.new(
            self.secret_key.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MomoError(f"MoMo HTTP {exc.code}: {detail[:160]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MomoError(f"Không kết nối được MoMo: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise MomoError("MoMo trả về dữ liệu không đọc được") from exc

    def create_payment(self, visit_id: int, amount: float, plate: str) -> MomoPayment:
        if self.problem:
            raise MomoError(self.problem)
        amount_value = int(round(amount))
        if amount_value < 1000 or amount_value > 50_000_000:
            raise MomoError("MoMo chỉ nhận giao dịch từ 1.000đ đến 50.000.000đ")
        stamp = str(int(time.time() * 1000))
        order_id = f"GX{visit_id}_{stamp}"
        request_id = f"RQ{visit_id}_{stamp}"
        order_info = f"Gui xe {visit_id} {plate}"[:50]
        extra_data = ""
        redirect_url = "https://momo.vn"
        ipn_url = "https://momo.vn"
        request_type = "captureWallet"
        raw = (
            f"accessKey={self.access_key}&amount={amount_value}&extraData={extra_data}"
            f"&ipnUrl={ipn_url}&orderId={order_id}&orderInfo={order_info}"
            f"&partnerCode={self.partner_code}&redirectUrl={redirect_url}"
            f"&requestId={request_id}&requestType={request_type}"
        )
        payload = {
            "partnerCode": self.partner_code,
            "requestId": request_id,
            "amount": amount_value,
            "orderId": order_id,
            "orderInfo": order_info,
            "redirectUrl": redirect_url,
            "ipnUrl": ipn_url,
            "requestType": request_type,
            "extraData": extra_data,
            "lang": "vi",
            "autoCapture": True,
            "signature": self._signature(raw),
        }
        response = self._post("/v2/gateway/api/create", payload)
        if int(response.get("resultCode", -1)) != 0:
            raise MomoError(str(response.get("message") or "MoMo từ chối tạo giao dịch"))
        qr_data = str(response.get("qrCodeUrl") or response.get("payUrl") or "")
        if not qr_data:
            raise MomoError("MoMo không trả về dữ liệu QR; kiểm tra quyền merchant")
        return MomoPayment(
            order_id=order_id,
            request_id=request_id,
            qr_data=qr_data,
            pay_url=str(response.get("payUrl") or ""),
        )

    def query(self, order_id: str) -> dict:
        request_id = f"QUERY{int(time.time() * 1000)}"
        raw = (
            f"accessKey={self.access_key}&orderId={order_id}"
            f"&partnerCode={self.partner_code}&requestId={request_id}"
        )
        return self._post(
            "/v2/gateway/api/query",
            {
                "partnerCode": self.partner_code,
                "requestId": request_id,
                "orderId": order_id,
                "lang": "vi",
                "signature": self._signature(raw),
            },
        )
