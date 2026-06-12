import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal

from sqlalchemy import select, func

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, sync_engine, SyncSessionLocal
from app.models import (
    User, UserRole, Area, MaintenanceTeam, PressureStation,
    Sensor, SensorType, GasPriceTier, GasSupplier, GasInventory,
    ResidentAccount, GasStatus
)
from app.utils.security import hash_password


def seed_database():
    print("=" * 60)
    print("开始初始化数据库...")
    print("=" * 60)

    print("[1/7] 创建数据表结构...")
    Base.metadata.create_all(bind=sync_engine)
    print("      数据表结构创建完成")

    db = SyncSessionLocal()
    try:
        existing_admin = db.execute(
            User.__table__.select().where(User.username == "admin")
        ).mappings().first()
        if existing_admin:
            print("⚠️  检测到已有种子数据，跳过初始化")
            print("   如需重新初始化，请先清空数据库后再运行")
            return

        print("[2/7] 创建管理员用户...")
        admin = User(
            username="admin",
            password_hash=hash_password("Admin@123"),
            real_name="系统管理员",
            phone="13800000000",
            email="admin@gassystem.com",
            role=UserRole.ADMIN,
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.add(admin)
        db.flush()
        db.commit()
        print(f"      ✅ 管理员: admin / Admin@123 (id={admin.id}) - 已提交")

        print("[3/7] 创建区域数据...")
        areas_data = [
            {"name": "城东区", "code": "CD01", "level": 1},
            {"name": "城西区", "code": "CX01", "level": 1},
            {"name": "城南区", "code": "CN01", "level": 1},
            {"name": "城北区", "code": "CB01", "level": 1},
            {"name": "经开区", "code": "JK01", "level": 1},
        ]
        area_objs = []
        for i, a in enumerate(areas_data):
            area = Area(
                **a,
                longitude=Decimal(f"{116.3 + i * 0.02:.7f}"),
                latitude=Decimal(f"{39.9 + i * 0.01:.7f}"),
                created_at=datetime.utcnow()
            )
            db.add(area)
            area_objs.append(area)
            print(f"      创建区域 {i + 1}/{len(areas_data)}: {a['name']}")
        db.flush()
        db.commit()
        print(f"      ✅ 创建了 {len(area_objs)} 个区域 - 已提交")

        print("[4/7] 创建角色用户和维修队...")
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
        total_role_users = 0
        for i, (role, name_prefix, uname) in enumerate(roles_config):
            area_idx = i % len(area_objs)
            role_users_count = 3
            for j in range(role_users_count):
                u = User(
                    username=f"{uname}_{j + 1:02d}",
                    password_hash=hash_password("123456"),
                    real_name=f"{name_prefix}{j + 1}",
                    phone=f"138{i + 1:02d}{j + 1:04d}",
                    email=f"{uname}{j + 1}@gassystem.com",
                    role=role,
                    area_id=area_objs[area_idx].id if role != UserRole.RESIDENT else None,
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                db.add(u)
                users.append(u)
                total_role_users += 1
                print(f"      创建用户 {total_role_users}/{len(roles_config) * 3}: {u.username} ({name_prefix})")
        db.flush()

        teams = []
        for i in range(min(3, len(area_objs))):
            area = area_objs[i]
            team = MaintenanceTeam(
                name=f"{area.name}维修一队",
                area_id=area.id,
                max_capacity=15,
                longitude=area.longitude,
                latitude=area.latitude,
                contact_phone=f"400-000-{1000 + i}",
                status="active",
                current_load=0,
                created_at=datetime.utcnow()
            )
            db.add(team)
            teams.append(team)
            print(f"      创建维修队 {i + 1}/{min(3, len(area_objs))}: {team.name}")
        db.flush()

        maintenance_users = [u for u in users if u.role == UserRole.MAINTENANCE]
        for i, u in enumerate(maintenance_users):
            if teams:
                team = teams[i % len(teams)]
                u.team_id = team.id
                if i == 0:
                    team.leader_id = u.id
        db.flush()

        area_managers = [u for u in users if u.role == UserRole.AREA_MANAGER]
        for i, area in enumerate(area_objs):
            if i < len(area_managers):
                area.manager_id = area_managers[i].id
        db.flush()
        db.commit()
        print(f"      ✅ 创建了 {len(users) - 1} 个角色用户，{len(teams)} 个维修队 - 已提交")

        print("[5/7] 创建调压站和传感器...")
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
                longitude=Decimal(str(float(area.longitude) + i * 0.005)[:10]),
                latitude=Decimal(str(float(area.latitude) + i * 0.003)[:10]),
                status="normal",
                created_at=datetime.utcnow()
            )
            db.add(station)
            stations.append(station)
            print(f"      创建调压站 {i + 1}/{len(area_objs)}: {station.name}")
        db.flush()

        sensor_count = 0
        total_sensors = len(stations) * 5
        for station in stations:
            sensors_config = [
                (SensorType.PRESSURE, "入口压力传感器", "IP", Decimal("0.4"), Decimal("0.8")),
                (SensorType.PRESSURE, "出口压力传感器", "OP", Decimal("0.15"), Decimal("0.25")),
                (SensorType.FLOW, "流量传感器", "FL", Decimal("0"), Decimal("1000")),
                (SensorType.LEAK, "泄漏传感器", "LK", Decimal("0"), Decimal("0.1")),
                (SensorType.TEMPERATURE, "温度传感器", "TP", Decimal("-20"), Decimal("60")),
            ]
            for st in sensors_config:
                s = Sensor(
                    code=f"{st[2]}-{station.code}",
                    name=f"{station.name}-{st[1]}",
                    type=st[0],
                    pressure_station_id=station.id,
                    area_id=station.area_id,
                    threshold_min=st[3],
                    threshold_max=st[4],
                    leak_threshold=Decimal("0.02") if st[0] == SensorType.LEAK else None,
                    longitude=station.longitude,
                    latitude=station.latitude,
                    status="online",
                    created_at=datetime.utcnow()
                )
                db.add(s)
                sensor_count += 1
                print(f"      创建传感器 {sensor_count}/{total_sensors}: {s.name}")
        db.flush()
        db.commit()
        print(f"      ✅ 创建了 {len(stations)} 个调压站，{sensor_count} 个传感器 - 已提交")

        print("[6/7] 创建气价、供应商和库存...")
        base_date = date.today().replace(month=1, day=1)
        tiers = [
            GasPriceTier(tier=1, name="第一阶梯(0-300)", min_volume=Decimal("0"), max_volume=Decimal("300"),
                         unit_price=Decimal("2.63"), effective_date=base_date, is_active=True),
            GasPriceTier(tier=2, name="第二阶梯(300-600)", min_volume=Decimal("300"), max_volume=Decimal("600"),
                         unit_price=Decimal("2.85"), effective_date=base_date, is_active=True),
            GasPriceTier(tier=3, name="第三阶梯(600+)", min_volume=Decimal("600"), max_volume=None,
                         unit_price=Decimal("4.23"), effective_date=base_date, is_active=True),
        ]
        for i, t in enumerate(tiers):
            db.add(t)
            print(f"      创建气价 {i + 1}/{len(tiers)}: {t.name}")

        suppliers = [
            GasSupplier(name="中石油天然气有限公司", code="CNPC-001", contact_person="张经理",
                        phone="010-12345678", rating=5, is_active=True,
                        address="北京市朝阳区建国路88号", created_at=datetime.utcnow()),
            GasSupplier(name="中石化天然气分公司", code="SINOPEC-001", contact_person="李主任",
                        phone="010-87654321", rating=4, is_active=True,
                        address="北京市海淀区中关村大街1号", created_at=datetime.utcnow()),
        ]
        for i, s in enumerate(suppliers):
            db.add(s)
            print(f"      创建供应商 {i + 1}/{len(suppliers)}: {s.name}")

        inventory_points = [
            GasInventory(storage_point="一号储气库(北郊)", current_volume=Decimal("15000"),
                         min_threshold=Decimal("5000"), max_capacity=Decimal("30000"),
                         last_updated=datetime.utcnow()),
            GasInventory(storage_point="二号储气库(南郊)", current_volume=Decimal("12000"),
                         min_threshold=Decimal("4000"), max_capacity=Decimal("25000"),
                         last_updated=datetime.utcnow()),
        ]
        for i, inv in enumerate(inventory_points):
            db.add(inv)
            print(f"      创建库存点 {i + 1}/{len(inventory_points)}: {inv.storage_point}")
        db.flush()
        db.commit()
        print(f"      ✅ 创建了 {len(tiers)} 级气价，{len(suppliers)} 供应商，{len(inventory_points)} 库存点 - 已提交")

        print("[7/7] 创建居民账户...")
        resident_users = [u for u in users if u.role == UserRole.RESIDENT]
        created_accounts = 0
        first_day = date.today().replace(day=1)
        for i, ru in enumerate(resident_users):
            area = area_objs[i % len(area_objs)]
            acc = ResidentAccount(
                account_no=f"ACC{area.code}{date.today().year}{i:06d}",
                user_id=ru.id,
                area_id=area.id,
                resident_name=ru.real_name,
                phone=ru.phone,
                address=f"{area.name}示例小区{(i % 10) + 1}号楼{(i % 20) + 1:02d}0{(i % 9) + 1}",
                meter_no=f"M{area.code}{10000 + i:06d}",
                meter_reading=Decimal(str(500 + i * 23.5)),
                last_reading_date=first_day - timedelta(days=1),
                longitude=Decimal(str(float(area.longitude) + (i % 5) * 0.001)[:10]),
                latitude=Decimal(str(float(area.latitude) + (i % 5) * 0.001)[:10]),
                gas_status=GasStatus.NORMAL,
                tier_level=1,
                created_at=datetime.utcnow()
            )
            db.add(acc)
            created_accounts += 1
            print(f"      创建居民账户 {created_accounts}/{len(resident_users)}: {acc.account_no} ({acc.resident_name})")
        db.flush()
        db.commit()
        print(f"      ✅ 创建了 {created_accounts} 个居民账户 - 已提交")

        print("\n" + "=" * 60)
        print("🔍 开始验证数据...")
        print("=" * 60)

        user_count = db.execute(select(func.count()).select_from(User.__table__)).scalar()
        area_count = db.execute(select(func.count()).select_from(Area.__table__)).scalar()
        team_count = db.execute(select(func.count()).select_from(MaintenanceTeam.__table__)).scalar()
        station_count = db.execute(select(func.count()).select_from(PressureStation.__table__)).scalar()
        sensor_count = db.execute(select(func.count()).select_from(Sensor.__table__)).scalar()
        tier_count = db.execute(select(func.count()).select_from(GasPriceTier.__table__)).scalar()
        supplier_count = db.execute(select(func.count()).select_from(GasSupplier.__table__)).scalar()
        inventory_count = db.execute(select(func.count()).select_from(GasInventory.__table__)).scalar()
        account_count = db.execute(select(func.count()).select_from(ResidentAccount.__table__)).scalar()

        normal_status_count = db.execute(
            select(func.count()).select_from(ResidentAccount.__table__)
            .where(ResidentAccount.gas_status == GasStatus.NORMAL)
        ).scalar()

        print(f"\n📊 数据库验证结果:")
        print(f"  User 表:           {user_count} 条 ✅" if user_count > 0 else f"  User 表:           {user_count} 条 ❌")
        print(f"  Area 表:           {area_count} 条 ✅" if area_count > 0 else f"  Area 表:           {area_count} 条 ❌")
        print(f"  MaintenanceTeam 表: {team_count} 条 ✅" if team_count > 0 else f"  MaintenanceTeam 表: {team_count} 条 ❌")
        print(f"  PressureStation 表: {station_count} 条 ✅" if station_count > 0 else f"  PressureStation 表: {station_count} 条 ❌")
        print(f"  Sensor 表:         {sensor_count} 条 ✅" if sensor_count > 0 else f"  Sensor 表:         {sensor_count} 条 ❌")
        print(f"  GasPriceTier 表:   {tier_count} 条 ✅" if tier_count > 0 else f"  GasPriceTier 表:   {tier_count} 条 ❌")
        print(f"  GasSupplier 表:    {supplier_count} 条 ✅" if supplier_count > 0 else f"  GasSupplier 表:    {supplier_count} 条 ❌")
        print(f"  GasInventory 表:   {inventory_count} 条 ✅" if inventory_count > 0 else f"  GasInventory 表:   {inventory_count} 条 ❌")
        print(f"  ResidentAccount 表: {account_count} 条 ✅" if account_count > 0 else f"  ResidentAccount 表: {account_count} 条 ❌")
        print(f"\n🔍 gas_status 验证:")
        print(f"  gas_status = NORMAL 的居民账户: {normal_status_count} 条")
        if normal_status_count == account_count and account_count > 0:
            print(f"  ✅ 所有居民账户的 gas_status 都正确设置为 NORMAL")
        else:
            print(f"  ❌ gas_status 存在异常，期望 {account_count} 条 NORMAL，实际 {normal_status_count} 条")

        print("\n" + "=" * 60)
        print("🎉 数据库初始化全部完成！")
        print("=" * 60)
        print(f"\n📋 初始账号汇总:")
        print(f"  超级管理员:  admin       / Admin@123")
        print(f"  调度员:      dispatcher_01~03 / 123456")
        print(f"  维修员:      tech_01~03       / 123456")
        print(f"  安全员:      safety_01~03     / 123456")
        print(f"  设计师:      designer_01~03   / 123456")
        print(f"  工程师:      engineer_01~03   / 123456")
        print(f"  催收员:      collector_01~03  / 123456")
        print(f"  区域主管:    manager_01~03    / 123456")
        print(f"  居民用户:    resident_01~03   / 123456")
        print(f"\n📊 统计数据:")
        print(f"  {len(area_objs)} 区域 / {len(teams)} 维修队 / {len(stations)} 调压站")
        print(f"  {sensor_count} 传感器 / {len(tiers)} 级气价 / {created_accounts} 居民账户")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
