"""FastAPI 统一异常处理。"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "code": "VALIDATION_ERROR"},
        )
