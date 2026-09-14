"""浓度换算与补液量计算。

全程使用 :class:`decimal.Decimal` 且中间值不做任何舍入，仅在组装响应时
按要求量化：浓度（C、T、补液后复核浓度）四位小数，补液量三位小数，
统一使用 ROUND_HALF_UP。
"""

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Literal

from pydantic import BaseModel

from app.schemas import ConcentrationInput

# 给足有效数字，保证 V 很大、T 很小时除法也不会提前截断。
getcontext().prec = 60

ZERO = Decimal("0")
Q_CONCENTRATION = Decimal("0.0001")
Q_VOLUME = Decimal("0.001")

Action = Literal["add_concentrate", "add_water", "hold"]


class ZeroDenominatorError(Exception):
    """补液公式分母为零，物理上无法配到目标中点。"""


class ConcentrationResult(BaseModel):
    action: Action
    action_label: str
    C: Decimal
    T: Decimal
    concentrate_liters: Decimal
    water_liters: Decimal
    verify_concentration: Decimal
    message: str


def _q4(value: Decimal) -> Decimal:
    return value.quantize(Q_CONCENTRATION, rounding=ROUND_HALF_UP)


def _q3(value: Decimal) -> Decimal:
    return value.quantize(Q_VOLUME, rounding=ROUND_HALF_UP)


def calculate(data: ConcentrationInput) -> ConcentrationResult:
    """根据折光读数计算当前浓度及补液 / 补水方案。

    - C = R×F；C ∈ [L, U] 时保持不动。
    - C < L：补原液 x = V×(T-C)/(S-T)，T=(L+U)/2；S-T=0 时报 422。
    - C > U：补水 y = V×(C-T)/T；T=0 时报 422。
    """

    R, F, V, S, L, U = data.R, data.F, data.V, data.S, data.L, data.U

    C = R * F
    T = (L + U) / 2

    concentrate = ZERO
    water = ZERO

    if L <= C <= U:
        action: Action = "hold"
        action_label = "保持不动"
        verify_concentration = C
        message = (
            f"当前浓度 {_q4(C)} 位于目标区间 [{_q4(L)}, {_q4(U)}] 内（含边界），"
            "无需补原液或补水，保持不动，补液量为零。"
        )
    elif C < L:
        denominator = S - T
        if denominator == ZERO:
            raise ZeroDenominatorError(
                "补原液公式分母 S-T 为零（目标中点等于原液浓度 S），"
                "无法通过补入有限体积原液达到目标，返回 422。"
            )
        # x = V×(T-C)/(S-T)
        concentrate = V * (T - C) / denominator
        action = "add_concentrate"
        action_label = "补原液"
        # 补液后复核：溶质质量守恒，体积按加和计 (V*C + x*S)/(V + x)
        verify_concentration = (V * C + concentrate * S) / (V + concentrate)
        message = (
            f"当前浓度 {_q4(C)} 低于区间下限 {_q4(L)}，"
            f"需补入原液 {_q3(concentrate)} 升，补液后复核浓度 {_q4(verify_concentration)}。"
        )
    else:  # C > U
        if T == ZERO:
            raise ZeroDenominatorError(
                "补水公式分母 T 为零（目标中点为 0），"
                "无法通过补入有限体积水达到目标，返回 422。"
            )
        # y = V×(C-T)/T
        water = V * (C - T) / T
        action = "add_water"
        action_label = "补水"
        # 补水后复核：只增加体积、溶质不变 V*C/(V + y)
        verify_concentration = V * C / (V + water)
        message = (
            f"当前浓度 {_q4(C)} 高于区间上限 {_q4(U)}，"
            f"需补入纯水 {_q3(water)} 升，补水后复核浓度 {_q4(verify_concentration)}。"
        )

    return ConcentrationResult(
        action=action,
        action_label=action_label,
        C=_q4(C),
        T=_q4(T),
        concentrate_liters=_q3(concentrate),
        water_liters=_q3(water),
        verify_concentration=_q4(verify_concentration),
        message=message,
    )
