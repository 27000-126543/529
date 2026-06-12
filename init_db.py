import asyncio
import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import AsyncSessionLocal, init_db
from app.models import (
    User, UserRole, Area, MaintenanceTeam, PressureStation,
    Sensor, SensorType, GasPriceTier, GasSupplier, GasInventory,
    ResidentAccount
)
from app.utils.security import hash_password


async def seed_database():
    await init_db()
    async with AsyncSessionLocal() as db:
        from sqlalchemy.future import select

        existing_admin = await db.execute(select(User).where(User.username == "admin"))
        if existing_admin.scalar_one_or_none():
            print("数据库已初始化，跳过种子数据")
            return

        admin = User(
            username="admin",
            password_hash=hash_password("Admin@123"),
            real_name="系统管理员",
            phone="13800000000",
            email="admin@gassystem.com",
            role=UserRole.ADMIN,
            is_active=True
        )
        db.add(admin)

        areas = [
            {"name": "城东区", "code": "CD01", "level": 1},
            {"name": "城西区", "code": "CX01", "level": 1},
            {"name": "城南区", "code": "CN01", "level": 1},
            {"name": "城北区", "code": "CB01", "level": 1},
            {"name": "经开区", "code": "JK01", "level": 1},
        ]
        area_objs = []
        for a in areas:
            area = Area(
                **a,
                longitude=Decimal(f"{116.3 + i * 0.02}"),
                latitude=Decimal(f"{39.9 + i * 0.01}")
            )
            db.add(area)
            area_objs.append(area)
        await db.flush()

        roles_config = [
            (UserRole.DISPATCHER, "调度员", "dispatcher"),
            (UserRole.MAINTENANCE, "维修员", "tech"),
            (UserRole.SAFETY_INSPECTOR, "安全员", "safety"),
            (UserRole.DESIGNER, "设计师", "designer"),
            (UserRole.ENGINEER, "工程师", "engineer"),
            (UserRole.COLLECTOR, "催收员", "collector"),
            (UserRole.AREA_MANAGER, "区域主管", "manager"),
            (UserRole.RESIDENT, "居民用户", "resident"),
        ]
        users = [admin]
        for i, (role, name_prefix, uname) in enumerate(roles_config):
            for j in range(min(3, 5)):
                user_area = area_objs[i % len(area_objs)] if role != UserRole.RESIDENT else None
                u = User(
                    username=f"{uname}_{j + 1:02d}",
                    password_hash=hash_password("123456"),
                    real_name=f"{name_prefix}{j + 1}",
                    phone=f"1380000{i + 1:02d}{j + 1:02d}",
                    email=f"{uname}{j + 1}@gassystem.com",
                    role=role,
                    area_id=user_area.id if user_area else None,
                    is_active=True
                )
                db.add(u)
                users.append(u)
        await db.flush()

        teams = []
        for i, area in enumerate(area_objs[:3]):
            team = MaintenanceTeam(
                name=f"{area.name}维修一队",
                area_id=area.id,
                max_capacity=15,
                longitude=area.longitude,
                latitude=area.latitude,
                contact_phone=f"400-000-{1000 + i}"
            )
            db.add(team)
            teams.append(team)
        await db.flush()

        maintenance_users = [u for u in users if u.role == UserRole.MAINTENANCE]
        for i, u in enumerate(maintenance_users):
            team = teams[i % len(teams)]
            u.team_id = team.id
            if i == 0:
                team.leader_id = u.id

        stations = []
        for i, area in enumerate(area_objs):
            station = PressureStation(
                name=f"{area.name}调压站{chr(65 + i)}",
                code=f"PS{area.code}{i:03d}",
                area_id=area.id,
                inlet_pressure_min=Decimal("0.4"),
                inlet_pressure_max=Decimal("0.8"),
                outlet_pressure_set=Decimal("0.2"),
                outlet_pressure_min=Decimal("0.15"),
                outlet_pressure_max=Decimal("0.25"),
                capacity=Decimal("5000"),
                longitude=Decimal(str(float(area.longitude) + i * 0.005)),
                latitude=Decimal(str(float(area.latitude) + i * 0.003))
            )
            db.add(station)
            stations.append(station)
        await db.flush()

        for station in stations:
            sensors_config = [
                (SensorType.PRESSURE, "入口压力传感器", "IP", 0.4, 0.8),
                (SensorType.PRESSURE, "出口压力传感器", "OP", 0.15, 0.25),
                (SensorType.FLOW, "流量传感器", "FL", 0, 1000),
                (SensorType.LEAK, "泄漏传感器", "LK", 0, 0.1),
                (SensorType.TEMPERATURE, "温度传感器", "TP", -20, 60),
            ]
            for st in sensors_config:
                s = Sensor(
                    code=f"{st[2]}-{station.code}",
                    name=f"{station.name}-{st[1]}",
                    type=st[0],
                    pressure_station_id=station.id,
                    area_id=station.area_id,
                    threshold_min=Decimal(str(st[3])),
                    threshold_max=Decimal(str(st[4])),
                    leak_threshold=Decimal("0.02") if st[0] == SensorType.LEAK else None,
                    longitude=station.longitude,
                    latitude=station.latitude
                )
                db.add(s)

        tiers = [
            GasPriceTier(tier=1, name="第一阶梯", min_volume=Decimal("0"), max_volume=Decimal("300"),
                         unit_price=Decimal("2.63"), effective_date=date.today().replace(month=1, day=1)),
            GasPriceTier(tier=2, name="第二阶梯", min_volume=Decimal("300"), max_volume=Decimal("600"),
                         unit_price=Decimal("2.85"), effective_date=date.today().replace(month=1, day=1)),
            GasPriceTier(tier=3, name="第三阶梯", min_volume=Decimal("600"), max_volume=None,
                         unit_price=Decimal("4.23"), effective_date=date.today().replace(month=1, day=1)),
        ]
        for t in tiers:
            db.add(t)

        suppliers = [
            GasSupplier(name="中石油天然气有限公司", code="CNPC-001", contact_person="张经理",
                        phone="010-12345678", rating=5),
            GasSupplier(name="中石化天然气分公司", code="SINOPEC-001", contact_person="李主任",
                        phone="010-87654321", rating=4),
        ]
        for s in suppliers:
            db.add(s)

        inventory_points = [
            GasInventory(storage_point="一号储气库", current_volume=Decimal("15000"),
                         min_threshold=Decimal("5000"), max_capacity=Decimal("30000")),
            GasInventory(storage_point="二号储气库", current_volume=Decimal("12000"),
                         min_threshold=Decimal("4000"), max_capacity=Decimal("25000")),
        ]
        for inv in inventory_points:
            db.add(inv)

        resident_users = [u for u in users if u.role == UserRole.RESIDENT]
        for i, ru in enumerate(resident_users):
            area = area_objs[i % len(area_objs)]
            acc = ResidentAccount(
                account_no=f"ACC{area.code}{date.today().year}{i:06d}",
                user_id=ru.id,
                area_id=area.id,
                resident_name=ru.real_name,
                phone=ru.phone,
                address=f"{area.name}示例小区{i + 1}号楼{i + 1:02d}0{i + 1}",
                meter_no=f"M{area.code}{10000 + i:06d}",
                meter_reading=Decimal(str(500 + i * 23.5)),
                last_reading_date=date.today().replace(day=1) - timedelta(days=1),
                longitude=area.longitude,
                latitude=area.latitude
            )
            db.add(acc)

        await db.commit()
        print(f"数据库初始化完成！")
        print(f"管理员账号: admin / Admin@123")
        print(f"测试账号: dispatcher_01 ~ manager_03 / 123456")
        print(f"已创建: {len(area_objs)}个区域, {len(users)}个用户, {len(teams)}个维修队")
        print(f"已创建: {len(stations)}个调压站, {len(stations) * 5}个传感器")


if __name__ == "__main__":
    asyncio.run(seed_database())
