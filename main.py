import asyncio
import sys
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.database import init_db
from app.redis_client import RedisManager
from app.utils.logger import get_logger
from app.routers.auth_router import router as auth_router
from app.routers.sensor_router import (
    router as sensor_router,
    station_router,
    warning_router,
    work_order_router
)
from app.routers.billing_router import (
    router as resident_router,
    billing_router,
    project_router,
    purchase_router,
    report_router,
    notif_router,
    predict_router
)
from app.websocket import router as ws_router, redis_listener

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Gas Management System...")
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    try:
        await RedisManager.get_client()
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")

    redis_task = None
    try:
        loop = asyncio.get_event_loop()
        redis_task = loop.create_task(redis_listener())
        logger.info("Redis listener started")
    except Exception as e:
        logger.error(f"Failed to start Redis listener: {e}")

    yield

    if redis_task:
        redis_task.cancel()
        try:
            await redis_task
        except asyncio.CancelledError:
            pass

    await RedisManager.close()
    logger.info("Gas Management System shutdown complete")


app = FastAPI(
    title="城市燃气输配调度与安全管理系统",
    description="""
City Gas Distribution Dispatching & Safety Management System API

## 功能模块

- **用户与权限管理**: RBAC权限体系，支持多种角色
- **传感器数据采集**: 高并发处理压力、流量、泄漏传感器数据
- **管网调度**: 调压站自动调节，调控日志记录
- **泄漏预警与工单**: 多级预警、智能分配维修队、超时升级
- **居民报修**: 异常提交、智能诊断、自动派单
- **工程改造审批**: 安监/设计/工程管理多级审批、超时催办
- **气源采购**: 负荷预测、采购计划、供应商管理、到货跟踪
- **账单与收费**: 阶梯气价、欠费限气、缴费恢复
- **运行报表**: 每日报表、统计、Excel导出
- **实时通知**: WebSocket推送预警/工单/审批/账单/检修状态
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.error(f"HTTP {exc.status_code}: {exc.detail} | {request.method} {request.url}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "success": False}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error: {exc.errors()} | {request.method} {request.url}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "请求参数验证失败",
            "errors": exc.errors(),
            "success": False
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Internal error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"服务器内部错误: {str(exc)}", "success": False}
    )


app.include_router(auth_router)
app.include_router(sensor_router)
app.include_router(station_router)
app.include_router(warning_router)
app.include_router(work_order_router)
app.include_router(resident_router)
app.include_router(billing_router)
app.include_router(project_router)
app.include_router(purchase_router)
app.include_router(report_router)
app.include_router(notif_router)
app.include_router(predict_router)
app.include_router(ws_router)


@app.get("/", tags=["系统"])
async def root():
    return {
        "name": "城市燃气输配调度与安全管理系统",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health", tags=["系统"])
async def health_check():
    return {
        "status": "healthy",
        "timestamp": asyncio.get_event_loop().time()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
        workers=4 if not settings.DEBUG else 1
    )
