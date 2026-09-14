"""进程内单元测试：计算逻辑、舍入、约束与错误原因。"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ConcentrationInput
from app.service import ZeroDenominatorError, calculate

client = TestClient(app)


def make_input(**overrides):
    base = dict(R="5", F="1", V="1000", S="50", L="8", U="12")
    base.update(overrides)
    return ConcentrationInput(**base)


# ---------- 低浓度：补原液，数值可独立复算 ----------

def test_low_concentration_add_concentrate():
    # C=5×1=5，T=(8+12)/2=10，x=1000×(10-5)/(50-10)=125
    result = calculate(make_input())
    assert result.action == "add_concentrate"
    assert result.C == Decimal("5.0000")
    assert result.T == Decimal("10.0000")
    assert result.concentrate_liters == Decimal("125.000")
    assert result.water_liters == Decimal("0.000")
    # 复核浓度由未舍入的补液量代回质量守恒：(V*C + x*S)/(V + x) = T
    assert result.verify_concentration == Decimal("10.0000")


def test_low_concentration_http():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="5", F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "add_concentrate"
    assert body["concentrate_liters"] == "125.000"
    assert body["verify_concentration"] == "10.0000"


# ---------- 高浓度：补水，数值可独立复算 ----------

def test_high_concentration_add_water():
    # C=15×1=15，T=10，y=1000×(15-10)/10=500
    result = calculate(make_input(R="15"))
    assert result.action == "add_water"
    assert result.C == Decimal("15.0000")
    assert result.water_liters == Decimal("500.000")
    assert result.concentrate_liters == Decimal("0.000")
    # V*C/(V+y) = 1000*15/1500 = 10
    assert result.verify_concentration == Decimal("10.0000")


def test_high_concentration_http():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="15", F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "add_water"
    assert body["water_liters"] == "500.000"
    assert body["verify_concentration"] == "10.0000"


# ---------- 恰好压线：明确无需调整 ----------

@pytest.mark.parametrize("reading", ["8", "12"])  # C 恰等于 L，恰等于 U
def test_on_boundary_hold(reading):
    result = calculate(make_input(R=reading))
    assert result.action == "hold"
    assert result.concentrate_liters == Decimal("0.000")
    assert result.water_liters == Decimal("0.000")
    assert result.verify_concentration == Decimal(f"{Decimal(reading) * 1:.4f}")
    assert "保持不动" in result.message or "无需" in result.message


def test_inside_interval_hold_http():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="10", F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "hold"
    assert body["concentrate_liters"] == "0.000"
    assert body["water_liters"] == "0.000"
    assert "无需" in body["message"]


# ---------- 分母为零：422 ----------

def test_water_zero_target_returns_422():
    # 目标区间 [0,0]，T=0 而 C>0，补水公式分母为零
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="1", F="1", V="1000", S="10", L="0", U="0"),
    )
    assert resp.status_code == 422
    assert "分母" in resp.json()["detail"]


def test_concentrate_target_equals_s_returns_422():
    # L=U=S，T=S，C<S，补原液公式分母 S-T=0
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="1", F="1", V="1000", S="20", L="20", U="20"),
    )
    assert resp.status_code == 422
    assert "分母" in resp.json()["detail"]


def test_service_raises_zero_denominator():
    with pytest.raises(ZeroDenominatorError):
        calculate(make_input(L="0", U="0"))


# ---------- ROUND_HALF_UP 与定长格式 ----------

def test_half_up_rounding_concentration():
    # C=0.00015，四位小数 HALF_UP -> 0.0002
    result = calculate(make_input(R="0.00015", F="1", S="1", L="0", U="1"))
    assert result.C == Decimal("0.0002")


def test_half_up_volume_three_places():
    # C=3.333333...? 构造需要三位 HALF_UP 的补液量：
    # V=100, C=1, S=4, T=1.5 -> x=100*(0.5)/(2.5)=20 整除；改 V=100,S=8,T=3,C=1
    # x=100*2/5=40 整除。用 V=100,C=1,S=6,T=2 -> 100/4=25。
    # 取 V=10, C=1, S=3, L=1.2, U=1.8（T=1.5，C<L）
    # x=10×(1.5-1)/(3-1.5)=3.3333... -> 3.333
    result = calculate(make_input(R="1", F="1", V="10", S="3", L="1.2", U="1.8"))
    assert result.T == Decimal("1.5000")
    assert result.concentrate_liters == Decimal("3.333")
    # 复核浓度必须用未舍入补液量计算，恰好回到 T
    assert result.verify_concentration == Decimal("1.5000")


def test_decimal_string_output_padding():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="5", F="1", V="1000", S="50", L="8", U="12"),
    )
    body = resp.json()
    # 尾随零必须保留：浓度四位、体积三位
    for key in ("C", "T", "verify_concentration"):
        assert len(body[key].split(".")[1]) == 4
    for key in ("concentrate_liters", "water_liters"):
        assert len(body[key].split(".")[1]) == 3


# ---------- 约束错误：必须指出原因 ----------

@pytest.mark.parametrize(
    "payload,片段",
    [
        (dict(R="31"), "R"),
        (dict(F="0"), "F"),
        (dict(F="10.1"), "F"),
        (dict(V="0"), "V"),
        (dict(V="50001"), "V"),
        (dict(S="0"), "S"),
        (dict(S="101"), "S"),
        (dict(L="-0.1"), "L"),
        (dict(U="-1"), "U"),
        (dict(R="-0.001"), "R"),
    ],
)
def test_range_violations_report_reason(payload, 片段):
    base = dict(R="5", F="1", V="1000", S="50", L="8", U="12")
    base.update(payload)
    resp = client.post("/api/concentration/adjust", json=base)
    assert resp.status_code == 422
    reasons = " ".join(e["reason"] for e in resp.json()["detail"])
    assert 片段 in reasons


def test_rf_greater_than_s_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="20", F="2", V="1000", S="30", L="5", U="10"),
    )
    assert resp.status_code == 422
    reasons = " ".join(e["reason"] for e in resp.json()["detail"])
    assert "R×F" in reasons and "S" in reasons


def test_l_greater_than_u_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="5", F="1", V="1000", S="50", L="12", U="8"),
    )
    assert resp.status_code == 422
    assert "L ≤ U" in " ".join(e["reason"] for e in resp.json()["detail"])


def test_u_greater_than_s_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="5", F="1", V="1000", S="10", L="8", U="12"),
    )
    assert resp.status_code == 422
    assert "U ≤ S" in " ".join(e["reason"] for e in resp.json()["detail"])


def test_malformed_decimal_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="abc", F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 422
    reasons = " ".join(e["reason"] for e in resp.json()["detail"])
    assert "十进制" in reasons and "R" in reasons


def test_non_string_number_rejected():
    # 要求十进制字符串，裸 JSON 数字不应接受
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R=5, F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 422


def test_missing_field_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 422
    fields = {e["field"] for e in resp.json()["detail"]}
    assert "R" in fields


def test_nan_rejected():
    resp = client.post(
        "/api/concentration/adjust",
        json=dict(R="NaN", F="1", V="1000", S="50", L="8", U="12"),
    )
    assert resp.status_code == 422
    assert "有限" in " ".join(e["reason"] for e in resp.json()["detail"])


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}
