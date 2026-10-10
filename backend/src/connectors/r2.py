import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime

from src.config import settings


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presigned_get(key: str, expires: int = 120) -> str:
    """Посилання, яким Телеграм забере файл із приватного бакета.

    Підпис — чиста математика, мережі не треба, тож boto3 (28 МБ разом із
    botocore) у образі заради однієї функції не потрібен.
    """
    host = f"{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    scope = f"{date}/auto/s3/aws4_request"

    # Порядок параметрів алфавітний — його ж і підписуємо.
    query = urllib.parse.urlencode(
        {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{settings.R2_ACCESS_KEY_ID}/{scope}",
            "X-Amz-Date": stamp,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "host",
        },
        quote_via=urllib.parse.quote,
    )

    path = f"/{settings.R2_BUCKET}/{urllib.parse.quote(key)}"
    canonical = f"GET\n{path}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
    to_sign = (
        f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n"
        f"{hashlib.sha256(canonical.encode()).hexdigest()}"
    )
    signing_key = _sign(
        _sign(
            _sign(_sign(f"AWS4{settings.R2_SECRET_ACCESS_KEY}".encode(), date), "auto"),
            "s3",
        ),
        "aws4_request",
    )
    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{query}&X-Amz-Signature={signature}"
