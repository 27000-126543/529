#!/bin/bash

echo "============================================="
echo "城市燃气输配调度与安全管理系统 - 启动脚本"
echo "============================================="

ACTION=${1:-all}

case $ACTION in
  install)
    echo "正在安装依赖..."
    python3 -m pip install -r requirements.txt
    echo "依赖安装完成"
    ;;

  init-db)
    echo "正在初始化数据库..."
    python3 init_db.py
    echo "数据库初始化完成"
    ;;

  server)
    echo "正在启动 FastAPI 服务..."
    python3 main.py
    ;;

  worker)
    echo "正在启动 Celery Worker..."
    celery -A app.celery_app worker --loglevel=info --pool=prefork --concurrency=4
    ;;

  beat)
    echo "正在启动 Celery Beat..."
    celery -A app.celery_app beat --loglevel=info
    ;;

  all)
    echo "正在启动完整服务..."
    echo "步骤1: 检查依赖..."
    python3 -c "import fastapi, sqlalchemy, redis, celery" 2>/dev/null || pip install -r requirements.txt

    echo "步骤2: 初始化数据库..."
    python3 init_db.py

    echo "步骤3: 启动所有服务..."
    echo "请在不同终端分别执行以下命令："
    echo "  API服务:   python3 main.py"
    echo "  Celery Worker: celery -A app.celery_app worker --loglevel=info"
    echo "  Celery Beat:   celery -A app.celery_app beat --loglevel=info"
    echo ""
    echo "API文档地址:"
    echo "  Swagger: http://localhost:8000/docs"
    echo "  ReDoc:   http://localhost:8000/redoc"
    ;;

  *)
    echo "用法: $0 {install|init-db|server|worker|beat|all}"
    echo ""
    echo "示例:"
    echo "  $0 install     # 安装依赖"
    echo "  $0 init-db     # 初始化数据库"
    echo "  $0 server      # 启动API服务"
    echo "  $0 worker      # 启动Celery Worker"
    echo "  $0 beat        # 启动Celery Beat定时任务"
    echo "  $0 all         # 完整启动引导"
    ;;
esac
