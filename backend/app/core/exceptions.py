"""FastAPI 统一异常处理。"""
from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """前端可稳定解析的统一错误响应。"""

    code: str = Field(description="稳定错误码，优先使用业务错误码。")
    message: str = Field(description="可展示或可记录的错误摘要。")
    detail: Any | None = Field(default=None, description="兼容 FastAPI detail 的错误详情。")
    request_id: str = Field(description="请求追踪 ID；未接入网关时为空字符串。")


API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "请求参数或业务状态不合法"},
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "未认证或认证失效"},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse, "description": "无权限访问"},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "资源不存在"},
    422: {"model": ErrorResponse, "description": "请求体验证失败"},
}

_STATUS_CODE_TO_ERROR_CODE = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    422: "VALIDATION_ERROR",
}
_BUSINESS_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_:.\\-]*$")


class AppError(Exception):
    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500):
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(message, "NOT_FOUND", 404)


class ForbiddenError(AppError):
    def __init__(self, message: str = "无权限访问"):
        super().__init__(message, "FORBIDDEN", 403)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "未认证"):
        super().__init__(message, "UNAUTHORIZED", 401)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                code=exc.code,
                message=exc.message,
                detail=exc.message,
                request_id=_request_id(_request),
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = _error_code(exc.status_code, exc.detail)
        message = _error_message(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                code=code,
                message=message,
                detail=exc.detail,
                request_id=_request_id(request),
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                code="VALIDATION_ERROR",
                message="请求参数校验失败",
                detail=exc.errors(),
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_error_payload(
                code="VALIDATION_ERROR",
                message=str(exc),
                detail=str(exc),
                request_id=_request_id(_request),
            ),
        )


def _error_payload(*, code: str, message: str, detail: Any, request_id: str) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "detail": detail,
        "request_id": request_id,
    }


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id", "")


def _error_code(status_code: int, detail: Any) -> str:
    if isinstance(detail, str) and _BUSINESS_CODE_PATTERN.match(detail):
        return detail
    return _STATUS_CODE_TO_ERROR_CODE.get(status_code, "INTERNAL_ERROR")


def _error_message(status_code: int, detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    return _STATUS_CODE_TO_ERROR_CODE.get(status_code, "INTERNAL_ERROR")
