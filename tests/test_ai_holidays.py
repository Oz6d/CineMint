"""中国法定节假日自动同步的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_ai_holidays.py
使用临时数据目录，不会访问真实节假日接口。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-ai-holidays-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ai_holidays import HolidayError, sync_holidays  # noqa: E402
from app.db import get_setting, init_db, set_setting  # noqa: E402
from app.main import app  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


def payload_for(year: int) -> dict:
    return {
        "year": year,
        "days": [
            {"name": "国庆节", "date": f"{year}-10-01", "isOffDay": True},
            {"name": "国庆节", "date": f"{year}-10-02", "isOffDay": True},
            {"name": "调休", "date": f"{year}-10-10", "isOffDay": False},
        ],
    }


def fake_get(url: str, **kwargs):
    if "2026.json" in url:
        return FakeResponse(payload_for(2026))
    if "2027.json" in url:
        return FakeResponse({}, status_code=404)
    return FakeResponse({}, status_code=404)


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    init_db()
    set_setting("ai_cn_holidays", "2025-01-01")
    with patch("app.core.ai_holidays.httpx.get", side_effect=fake_get):
        result = sync_holidays([2026])
    holidays = str(get_setting("ai_cn_holidays", ""))
    expect("sync years", result["years"], [2026])
    expect("sync source", result["source"], "NateScarlet/holiday-cn")
    expect("sync updated", bool(result["updated_at"]), True)
    expect("off day included", "2026-10-01" in holidays, True)
    expect("off day two included", "2026-10-02" in holidays, True)
    expect("work day excluded", "2026-10-10" in holidays, False)
    expect("existing year kept", "2025-01-01" in holidays, True)
    expect("updated setting", get_setting("ai_cn_holidays_updated_at", ""), result["updated_at"])

    try:
        with patch("app.core.ai_holidays.httpx.get", side_effect=fake_get):
            sync_holidays([2027])
        not_published = None
    except HolidayError as exc:
        not_published = str(exc)
    expect("unpublished year error", "尚未发布" in (not_published or ""), True)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)
        with patch("app.core.ai_holidays.httpx.get", side_effect=fake_get):
            response = client.post("/api/settings/ai-holidays/sync", json={"years": [2026]})
        expect("sync api status", response.status_code, 200)
        expect("sync api holiday", "2026-10-01" in response.json().get("holidays", ""), True)
        expect(
            "sync api updated",
            bool(response.json().get("updated_at")),
            True,
        )

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
