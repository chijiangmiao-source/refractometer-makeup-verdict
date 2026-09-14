"""FastAPI 入口：折光浓度换算与补液决策。"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas import ConcentrationInput
from app.service import ConcentrationResult, ZeroDenominatorError, calculate

app = FastAPI(
    title="磨削槽液浓度换算 API",
    description="根据折光读数计算真实浓度，并决策补原液、补水或保持不动。",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """把字段错误整理成 field + reason，明确指出出错字段与中文原因。"""
    # Pydantic 内置错误类型 → 中文原因
    builtin_reasons = {
        "missing": "缺少必填字段",
        "model_type": "请求体必须是 JSON 对象",
        "json_invalid": "请求体不是合法的 JSON",
    }
    detail = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ()) if part != "body"]
        err_type = err.get("type", "")
        if err_type in builtin_reasons:
            reason = builtin_reasons[err_type]
        else:
            reason = err.get("msg", "输入无效")
            # 去掉 Pydantic 自带的 "Value error, " 前缀，保留我们给出的中文原因
            if reason.startswith("Value error, "):
                reason = reason[len("Value error, "):]
        detail.append(
            {
                "field": ".".join(loc) if loc else "请求体（跨字段约束）",
                "reason": reason,
            }
        )
    return JSONResponse(status_code=422, content={"detail": detail})


@app.post("/api/concentration/adjust", response_model=ConcentrationResult)
def adjust_concentration(payload: ConcentrationInput) -> ConcentrationResult:
    try:
        return calculate(payload)
    except ZeroDenominatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
