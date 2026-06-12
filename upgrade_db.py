import sys
import os

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import sync_engine


def upgrade_database():
    print("=" * 60)
    print("开始升级数据库...")
    print("=" * 60)

    print("\n[1/2] 创建日报表部分唯一索引...")
    with sync_engine.connect() as conn:
        result = conn.execute(text("""
            SELECT indexname 
            FROM pg_indexes 
            WHERE tablename = 'daily_reports' 
              AND indexname = 'ux_daily_reports_date_global'
        """))
        existing = result.scalar_one_or_none()
        
        if existing:
            print("      索引 ux_daily_reports_date_global 已存在，跳过")
        else:
            conn.execute(text("""
                CREATE UNIQUE INDEX ux_daily_reports_date_global 
                ON daily_reports (report_date) 
                WHERE area_id IS NULL
            """))
            conn.commit()
            print("      ✅ 索引 ux_daily_reports_date_global 创建成功")

    print("\n[2/2] 检查并清理重复的全局日报记录...")
    with sync_engine.connect() as conn:
        result = conn.execute(text("""
            SELECT report_date, COUNT(*) as cnt
            FROM daily_reports
            WHERE area_id IS NULL
            GROUP BY report_date
            HAVING COUNT(*) > 1
        """))
        duplicates = result.all()
        
        if duplicates:
            print(f"      发现 {len(duplicates)} 天存在重复的全局日报记录，开始清理...")
            for row in duplicates:
                report_date = row[0]
                cnt = row[1]
                delete_result = conn.execute(text("""
                    DELETE FROM daily_reports
                    WHERE area_id IS NULL
                      AND report_date = :report_date
                      AND id NOT IN (
                          SELECT MIN(id)
                          FROM daily_reports
                          WHERE area_id IS NULL
                            AND report_date = :report_date
                      )
                """), {"report_date": report_date})
                conn.commit()
                deleted = delete_result.rowcount
                print(f"      日期 {report_date}: 删除 {deleted} 条重复记录（保留 id 最小的）")
            print("      ✅ 重复记录清理完成")
        else:
            print("      没有发现重复的全局日报记录")

    print("\n" + "=" * 60)
    print("🎉 数据库升级完成！")
    print("=" * 60)


if __name__ == "__main__":
    upgrade_database()
