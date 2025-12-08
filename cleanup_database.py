#!/usr/bin/env python3
"""清理数据库中的错误测试方法"""

import sqlite3
import sys
from pathlib import Path

def cleanup_database(db_path: str):
    """清理数据库中包含错误调用的测试方法"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("=" * 60)
    print("开始清理数据库中的错误测试方法")
    print("=" * 60)

    # 1. 查找包含错误方法调用的测试方法
    error_patterns = [
        '%findPaymentByReflection%',
        '%getPaymentByReflection%',
        '%service.getAmount()%',  # PaymentService没有这个方法
    ]

    print("\n1. 查找包含错误方法调用的测试方法...")
    for pattern in error_patterns:
        cursor.execute('''
            SELECT test_case_id, method_name, target_method
            FROM test_methods
            WHERE code LIKE ?
        ''', (pattern,))

        results = cursor.fetchall()
        if results:
            print(f"\n   模式: {pattern}")
            print(f"   找到 {len(results)} 个错误方法:")
            for row in results:
                print(f"     - {row[0]}.{row[1]} (目标: {row[2]})")

    # 2. 删除这些错误方法
    print("\n2. 删除错误的测试方法...")
    total_deleted = 0
    for pattern in error_patterns:
        cursor.execute('''
            DELETE FROM test_methods
            WHERE code LIKE ?
        ''', (pattern,))
        deleted = cursor.rowcount
        if deleted > 0:
            print(f"   删除了 {deleted} 个包含 {pattern} 的方法")
            total_deleted += deleted

    conn.commit()
    print(f"\n   总共删除了 {total_deleted} 个错误方法")

    # 3. 查找空的测试用例（没有任何测试方法）
    print("\n3. 查找空的测试用例...")
    cursor.execute('''
        SELECT tc.id, tc.target_class, tc.class_name
        FROM test_cases tc
        LEFT JOIN test_methods tm ON tc.id = tm.test_case_id
        GROUP BY tc.id
        HAVING COUNT(tm.method_name) = 0
    ''')

    empty_cases = cursor.fetchall()
    if empty_cases:
        print(f"   找到 {len(empty_cases)} 个空测试用例:")
        for row in empty_cases:
            print(f"     - {row[0]}: {row[1]} -> {row[2]}")

        # 删除空测试用例
        print("\n   删除空测试用例...")
        for case_id, _, _ in empty_cases:
            cursor.execute('DELETE FROM test_cases WHERE id = ?', (case_id,))
        conn.commit()
        print(f"   删除了 {len(empty_cases)} 个空测试用例")
    else:
        print("   没有找到空测试用例")

    # 4. 统计剩余的测试用例和方法
    print("\n4. 统计剩余的测试数据...")
    cursor.execute('SELECT COUNT(*) FROM test_cases')
    test_cases_count = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM test_methods')
    test_methods_count = cursor.fetchone()[0]

    print(f"   剩余测试用例: {test_cases_count}")
    print(f"   剩余测试方法: {test_methods_count}")

    # 5. 按类统计
    print("\n5. 按类统计测试方法数量:")
    cursor.execute('''
        SELECT tc.target_class, COUNT(DISTINCT tc.id) as cases, COUNT(tm.method_name) as methods
        FROM test_cases tc
        LEFT JOIN test_methods tm ON tc.id = tm.test_case_id
        GROUP BY tc.target_class
        ORDER BY tc.target_class
    ''')

    for row in cursor.fetchall():
        print(f"   {row[0]}: {row[1]} 用例, {row[2]} 方法")

    conn.close()

    print("\n" + "=" * 60)
    print("数据库清理完成")
    print("=" * 60)

if __name__ == "__main__":
    db_path = "cache/comet.db"

    if not Path(db_path).exists():
        print(f"错误：数据库文件不存在: {db_path}")
        sys.exit(1)

    # 备份数据库
    import shutil
    from datetime import datetime

    backup_path = f"cache/comet.db.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"备份数据库到: {backup_path}")
    shutil.copy2(db_path, backup_path)

    cleanup_database(db_path)
