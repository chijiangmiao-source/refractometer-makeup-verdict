"""黑盒验收测试：对已部署的 HTTP 服务发起真实请求。

仅当环境变量 BASE_URL 指向运行中的服务时执行（docker compose 的 verify
服务会设置 BASE_URL=http://api:8000）；否则跳过，便于本地直接跑全套。
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL")

pytestmark = pytest.mark.skipif(not BASE_URL, reason="未设置 BASE_URL，跳过在线验收")

PAYLOAD_LOW = dict(R="5", F="1", V="1000", S="50", L="8", U="12")
PAYLOAD_HIGH = dict(R="15", F="1", V="1000", S="50", L="8", U="12")
PAYLOAD_BOUNDARY = dict(R="8", F="1", V="1000", S="50", L="8", U="12")


def _post(client: httpx.Client, payload):
    return client.post(f"{BASE_URL}/api/concentration/adjust", json=payload)


def test_health(client: httpx.Client):
    assert client.get(f"{BASE_URL}/healthz").status_code == 200


def test_acceptance_low_concentration_needs_concentrate(client: httpx.Client):
    # 手算：C=5，T=10，原液 x = 1000×(10-5)/(50-10) = 125 升
    body = _post(client, PAYLOAD_LOW).json()
    assert body["action"] == "add_concentrate"
    assert body["C"] == "5.0000"
    assert body["T"] == "10.0000"
    assert body["concentrate_liters"] == "125.000"
    assert body["verify_concentration"] == "10.0000"


def test_acceptance_high_concentration_needs_water(client: httpx.Client):
    # 手算：C=15，T=10，补水 y = 1000×(15-10)/10 = 500 升
    body = _post(client, PAYLOAD_HIGH).json()
    assert body["action"] == "add_water"
    assert body["C"] == "15.0000"
    assert body["water_liters"] == "500.000"
    assert body["verify_concentration"] == "10.0000"


def test_acceptance_on_boundary_needs_nothing(client: httpx.Client):
    # C=8 恰等于下限 L=8，必须明确无需调整
    body = _post(client, PAYLOAD_BOUNDARY).json()
    assert body["action"] == "hold"
    assert body["concentrate_liters"] == "0.000"
    assert body["water_liters"] == "0.000"
    assert ("无需" in body["message"]) or ("保持不动" in body["message"])


def test_acceptance_zero_denominator_422(client: httpx.Client):
    resp = _post(client, dict(R="1", F="1", V="1000", S="10", L="0", U="0"))
    assert resp.status_code == 422
    assert "分母" in resp.json()["detail"]


@pytest.fixture(scope="session")
def client():
    with httpx.Client(timeout=10) as c:
        yield c
