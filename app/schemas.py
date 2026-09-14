"""请求模型与输入约束校验。

六个字段均以十进制字符串（JSON string）接收，在 ``mode="before"`` 阶段
手动转换为 :class:`decimal.Decimal`，杜绝二进制浮点误差；任何越界、
非法格式或跨字段问题都抛出带中文原因的 ValueError，最终以 HTTP 422 返回。
"""

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, field_validator, model_validator


def _parse_decimal(raw: object, name: str) -> Decimal:
    if not isinstance(raw, str):
        raise ValueError(f"{name} 必须是以字符串形式提供的十进制数")
    text = raw.strip()
    if not text:
        raise ValueError(f"{name} 不能为空，必须是十进制数字字符串")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"{name} 必须是十进制数字字符串，收到的 {raw!r} 无法解析"
        ) from None
    if not value.is_finite():
        raise ValueError(f"{name} 必须是有限的十进制数，不能为 NaN 或 Infinity")
    return value


def _check_range(value: Decimal, label: str, code: str, low: Decimal,
                 high: Decimal, low_strict: bool = False) -> Decimal:
    if low_strict:
        ok = low < value <= high
        cond = f"{low} < {code} ≤ {high}"
    else:
        ok = low <= value <= high
        cond = f"{low} ≤ {code} ≤ {high}"
    if not ok:
        raise ValueError(f"{label} 超出允许范围（必须满足 {cond}），当前为 {value}")
    return value


class ConcentrationInput(BaseModel):
    """折光补液计算入参。

    R: 折光读数；F: 折光系数；V: 槽液体积（升）；
    S: 原液质量浓度百分数；L/U: 目标浓度闭区间端点。
    """

    R: Decimal
    F: Decimal
    V: Decimal
    S: Decimal
    L: Decimal
    U: Decimal

    @field_validator("R", mode="before")
    @classmethod
    def _check_r(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "R（折光读数）")
        return _check_range(v, "R（折光读数）", "R", Decimal(0), Decimal(30))

    @field_validator("F", mode="before")
    @classmethod
    def _check_f(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "F（折光系数）")
        return _check_range(v, "F（折光系数）", "F", Decimal(0), Decimal(10), low_strict=True)

    @field_validator("V", mode="before")
    @classmethod
    def _check_v(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "V（槽液体积升）")
        return _check_range(v, "V（槽液体积升）", "V", Decimal(0), Decimal(50000),
                            low_strict=True)

    @field_validator("S", mode="before")
    @classmethod
    def _check_s(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "S（原液质量浓度百分数）")
        return _check_range(v, "S（原液浓度百分数）", "S", Decimal(0), Decimal(100),
                            low_strict=True)

    @field_validator("L", mode="before")
    @classmethod
    def _check_l(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "L（目标区间下限）")
        if v < 0:
            raise ValueError(f"L（目标区间下限）必须满足 L ≥ 0，当前为 {v}")
        return v

    @field_validator("U", mode="before")
    @classmethod
    def _check_u(cls, raw: object) -> Decimal:
        v = _parse_decimal(raw, "U（目标区间上限）")
        if v < 0:
            raise ValueError(f"U（目标区间上限）必须满足 U ≥ 0，当前为 {v}")
        return v

    @model_validator(mode="after")
    def _check_cross_fields(self) -> "ConcentrationInput":
        if self.L > self.U:
            raise ValueError(
                f"目标区间必须满足 L ≤ U，当前 L={self.L} 大于 U={self.U}"
            )
        if self.U > self.S:
            raise ValueError(
                f"目标区间必须满足 U ≤ S，当前 U={self.U} 高于原液浓度 S={self.S}"
            )
        current = self.R * self.F
        if current > self.S:
            raise ValueError(
                f"R×F 不得大于 S：当前 C=R×F={current}，高于原液浓度 S={self.S}"
            )
        return self
