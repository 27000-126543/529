import os
from celery import Celery
from celery.schedules import crontab
from datetime import datetime, date, timedelta
from app.config import settings

celery_app = Celery(
    "gas_management",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.notification_tasks",
        "app.tasks.billing_tasks",
        "app.tasks.report_tasks"
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
)

celery_app.conf.beat_schedule = {
    "generate-daily-report-at-2am": {
        "task": "app.tasks.report_tasks.generate_daily_reports",
        "schedule": crontab(hour=2, minute=0),
    },
    "check-overdue-bills-at-6am": {
        "task": "app.tasks.billing_tasks.check_overdue_bills",
        "schedule": crontab(hour=6, minute=0),
    },
    "generate-monthly-bills": {
        "task": "app.tasks.billing_tasks.generate_monthly_bills",
        "schedule": crontab(day_of_month=1, hour=3, minute=0),
    },
    "check-overdue-work-orders": {
        "task": "app.tasks.notification_tasks.check_overdue_work_orders",
        "schedule": crontab(minute="*/15"),
    },
    "check-approval-reminders": {
        "task": "app.tasks.notification_tasks.check_approval_reminders",
        "schedule": crontab(minute="*/30"),
    },
    "predict-next-day-demand": {
        "task": "app.tasks.report_tasks.predict_daily_demand",
        "schedule": crontab(hour=20, minute=0),
    },
    "generate-gas-purchase-plan": {
        "task": "app.tasks.report_tasks.generate_monthly_purchase_plan",
        "schedule": crontab(day_of_month=25, hour=4, minute=0),
    },
    "auto-adjust-pressure-peak-hours": {
        "task": "app.tasks.notification_tasks.auto_adjust_pressure_stations",
        "schedule": crontab(minute="*/10"),
    },
}
