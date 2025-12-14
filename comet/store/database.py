"""SQLite 数据库封装"""

import sqlite3
import json
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..models import Mutant, TestCase, TestMethod, EvaluationResult

logger = logging.getLogger(__name__)


class Database:
    """数据库管理类 - 使用 SQLite 存储测试用例、变异体和执行结果

    线程安全：使用 RLock 保护所有数据库操作
    """

    def __init__(self, db_path: str = "comet.db"):
        """
        初始化数据库

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()  # 使用可重入锁保证线程安全
        self._create_tables()

    def _create_tables(self) -> None:
        """创建数据表"""
        cursor = self.conn.cursor()

        # 变异体表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS mutants (
                id TEXT PRIMARY KEY,
                class_name TEXT NOT NULL,
                method_name TEXT,
                patch TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                killed_by TEXT,
                survived INTEGER DEFAULT 0,
                compile_error TEXT,
                code_hash TEXT,
                created_at TEXT,
                evaluated_at TEXT
            )
        """
        )

        # 测试方法表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS test_methods (
                test_case_id TEXT NOT NULL,
                method_name TEXT NOT NULL,
                code TEXT NOT NULL,
                target_method TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (test_case_id, method_name)
            )
        """
        )

        # 测试用例表（测试类级别的元数据）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS test_cases (
                id TEXT PRIMARY KEY,
                class_name TEXT NOT NULL,
                target_class TEXT NOT NULL,
                package_name TEXT,
                imports TEXT,
                methods TEXT NOT NULL,
                full_code TEXT,
                compile_success INTEGER DEFAULT 0,
                compile_error TEXT,
                kills TEXT,
                coverage_lines TEXT,
                coverage_branches TEXT,
                code_hash TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """
        )

        # 评估结果表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id TEXT NOT NULL,
                mutant_id TEXT,
                passed INTEGER NOT NULL,
                error_message TEXT,
                execution_time REAL,
                coverage TEXT,
                timestamp TEXT
            )
        """
        )

        # 方法覆盖率表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS method_coverage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                iteration INTEGER NOT NULL,
                class_name TEXT NOT NULL,
                method_name TEXT NOT NULL,
                covered_lines TEXT NOT NULL,
                missed_lines TEXT NOT NULL,
                total_lines INTEGER,
                covered_branches INTEGER,
                total_branches INTEGER,
                line_coverage REAL,
                branch_coverage REAL,
                timestamp TEXT
            )
        """
        )

        # 类到文件映射表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS class_file_mapping (
                class_name TEXT PRIMARY KEY,
                simple_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                package_name TEXT,
                is_public INTEGER DEFAULT 0,
                is_interface INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """
        )

        # 创建索引
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mutants_status ON mutants(status)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_mutants_hash ON mutants(code_hash)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tests_hash ON test_cases(code_hash)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_test_methods_case ON test_methods(test_case_id)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_test_methods_name ON test_methods(test_case_id, method_name)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_coverage_class_method ON method_coverage(class_name, method_name)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_coverage_iteration ON method_coverage(iteration)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_class_mapping_file ON class_file_mapping(file_path)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_class_mapping_simple ON class_file_mapping(simple_name)
        """
        )

        self.conn.commit()

    def save_mutant(self, mutant: Mutant) -> None:
        """保存变异体（线程安全）"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO mutants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    mutant.id,
                    mutant.class_name,
                    mutant.method_name,
                    mutant.patch.model_dump_json(),
                    mutant.status,
                    json.dumps(mutant.killed_by),
                    1 if mutant.survived else 0,
                    mutant.compile_error,
                    None,  # code_hash
                    mutant.created_at.isoformat() if mutant.created_at else None,
                    mutant.evaluated_at.isoformat() if mutant.evaluated_at else None,
                ),
            )
            self.conn.commit()

    def get_mutant(self, mutant_id: str) -> Optional[Mutant]:
        """获取变异体"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM mutants WHERE id = ?", (mutant_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_mutant(row)

    def get_all_mutants(self, status: Optional[str] = None) -> List[Mutant]:
        """获取所有变异体"""
        with self._lock:
            cursor = self.conn.cursor()
            if status:
                cursor.execute("SELECT * FROM mutants WHERE status = ?", (status,))
            else:
                cursor.execute("SELECT * FROM mutants")
            rows = cursor.fetchall()
            return [self._row_to_mutant(row) for row in rows]

    def get_pending_mutants(self) -> List[Mutant]:
        """获取待评估的变异体"""
        return self.get_all_mutants(status="pending")

    def get_valid_mutants(self) -> List[Mutant]:
        """获取有效的变异体（已通过静态检查）"""
        return self.get_all_mutants(status="valid")

    def get_all_evaluated_mutants(self) -> List[Mutant]:
        """
        获取所有已评估的变异体（包括 valid 和 outdated 状态）
        用于计算全局变异分数
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT * FROM mutants
                WHERE status IN ('valid', 'outdated')
                AND evaluated_at IS NOT NULL
            """
            )
            rows = cursor.fetchall()
            return [self._row_to_mutant(row) for row in rows]

    def get_mutants_by_class(self, class_name: str) -> List[Mutant]:
        """获取指定类的所有变异体"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM mutants WHERE class_name = ?", (class_name,))
            rows = cursor.fetchall()
            return [self._row_to_mutant(row) for row in rows]

    def get_mutants_by_method(
        self, class_name: str, method_name: str, status: Optional[str] = "valid"
    ) -> List[Mutant]:
        """
        获取指定方法的变异体

        Args:
            class_name: 类名
            method_name: 方法名
            status: 状态过滤（如 'valid', 'pending', None 表示所有状态）

        Returns:
            变异体列表
        """
        with self._lock:
            cursor = self.conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM mutants WHERE class_name = ? AND method_name = ? AND status = ?",
                    (class_name, method_name, status),
                )
            else:
                cursor.execute(
                    "SELECT * FROM mutants WHERE class_name = ? AND method_name = ?",
                    (class_name, method_name),
                )
            rows = cursor.fetchall()
            return [self._row_to_mutant(row) for row in rows]

    def mark_mutants_outdated(self, class_name: str, method_name: str) -> int:
        """
        将指定方法的有效变异体标记为 outdated

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            被标记的变异体数量
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE mutants
                SET status = 'outdated'
                WHERE class_name = ? AND method_name = ? AND status = 'valid'
            """,
                (class_name, method_name),
            )
            self.conn.commit()
            updated_count = cursor.rowcount
            logger.info(
                f"已将 {class_name}.{method_name} 的 {updated_count} 个变异体标记为 outdated"
            )
            return updated_count

    def get_method_mutant_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有方法的变异体统计信息

        Returns:
            字典，键为 "ClassName.methodName"，值为包含统计信息的字典：
            {
                "class_name": str,
                "method_name": str,
                "total": int,  # 总变异体数（仅 valid 状态）
                "killed": int,  # 已击杀数量
                "survived": int,  # 幸存数量
                "killrate": float  # 杀死率（0.0-1.0）
            }
        """
        with self._lock:
            cursor = self.conn.cursor()
            # 查询所有有效变异体的统计信息
            cursor.execute(
                """
                SELECT
                    class_name,
                    method_name,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'killed' THEN 1 ELSE 0 END) as killed,
                    SUM(CASE WHEN survived = 1 THEN 1 ELSE 0 END) as survived
                FROM mutants
                WHERE status IN ('valid', 'killed') AND method_name IS NOT NULL
                GROUP BY class_name, method_name
            """
            )

            rows = cursor.fetchall()
            stats = {}

            for row in rows:
                class_name = row["class_name"]
                method_name = row["method_name"]
                total = row["total"]
                killed = row["killed"]
                survived = row["survived"]

                # 计算杀死率
                killrate = killed / total if total > 0 else 0.0

                key = f"{class_name}.{method_name}"
                stats[key] = {
                    "class_name": class_name,
                    "method_name": method_name,
                    "total": total,
                    "killed": killed,
                    "survived": survived,
                    "killrate": killrate,
                }

            return stats

    def save_test_case(self, test_case: TestCase) -> None:
        """
        保存测试用例

        每次保存会覆盖已有的测试方法，只保留最新版本

        注意：此方法会删除数据库中存在但不在 test_case.methods 列表中的旧方法，
        这样可以处理方法名变更的情况（LLM 返回的新方法名可能与旧方法名不同）
        """
        cursor = self.conn.cursor()

        # 检查测试用例是否存在
        cursor.execute("SELECT id FROM test_cases WHERE id = ?", (test_case.id,))
        case_exists = cursor.fetchone() is not None

        if case_exists:
            logger.debug(f"测试用例 {test_case.id} 已存在，将更新测试方法")

            # 获取数据库中已有的方法名列表
            cursor.execute(
                "SELECT method_name FROM test_methods WHERE test_case_id = ?",
                (test_case.id,),
            )
            existing_method_names = {row[0] for row in cursor.fetchall()}

            # 获取当前要保存的方法名列表
            current_method_names = {method.method_name for method in test_case.methods}

            # 找出需要删除的方法（在数据库中存在但不在当前列表中）
            methods_to_delete = existing_method_names - current_method_names

            if methods_to_delete:
                logger.info(f"删除已不存在的旧测试方法: {methods_to_delete}")
                for method_name in methods_to_delete:
                    cursor.execute(
                        "DELETE FROM test_methods WHERE test_case_id = ? AND method_name = ?",
                        (test_case.id, method_name),
                    )
        else:
            logger.debug(f"创建新测试用例: {test_case.id}")

        # 处理每个测试方法
        for method in test_case.methods:
            method.updated_at = datetime.now()

            # 保存方法到数据库，使用 COALESCE 保留原有的 created_at
            cursor.execute(
                """
                INSERT OR REPLACE INTO test_methods
                (test_case_id, method_name, code, target_method, created_at, updated_at)
                VALUES (?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM test_methods WHERE test_case_id = ? AND method_name = ?), ?),
                        ?)
            """,
                (
                    test_case.id,
                    method.method_name,
                    method.code,
                    method.target_method,
                    test_case.id,
                    method.method_name,
                    method.created_at.isoformat() if method.created_at else None,
                    method.updated_at.isoformat() if method.updated_at else None,
                ),
            )

        # 更新测试用例主表
        test_case.updated_at = datetime.now()
        cursor.execute(
            """
            INSERT OR REPLACE INTO test_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                test_case.id,
                test_case.class_name,
                test_case.target_class,
                test_case.package_name,
                json.dumps(test_case.imports),
                json.dumps([m.model_dump(mode="json") for m in test_case.methods]),
                test_case.full_code,
                1 if test_case.compile_success else 0,
                test_case.compile_error,
                json.dumps(test_case.kills),
                json.dumps(test_case.coverage_lines),
                json.dumps(test_case.coverage_branches),
                None,  # code_hash
                test_case.created_at.isoformat() if test_case.created_at else None,
                test_case.updated_at.isoformat() if test_case.updated_at else None,
            ),
        )
        self.conn.commit()

        logger.info(
            f"已保存测试用例 {test_case.id}，包含 {len(test_case.methods)} 个测试方法"
        )

    def get_test_case(self, test_id: str) -> Optional[TestCase]:
        """获取测试用例"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM test_cases WHERE id = ?", (test_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_test_case(row)

    def get_all_test_cases(self) -> List[TestCase]:
        """获取所有测试用例"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM test_cases WHERE compile_success = 1")
            rows = cursor.fetchall()  # 先取出所有行
            return [self._row_to_test_case(row) for row in rows]

    def get_all_tests(self) -> List[TestCase]:
        """获取所有测试用例（别名）"""
        return self.get_all_test_cases()

    def get_tests_by_target_class(self, class_name: str) -> List[TestCase]:
        """获取指定目标类的所有测试"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT * FROM test_cases
                WHERE target_class = ? AND compile_success = 1
                ORDER BY updated_at DESC
            """,
                (class_name,),
            )
            rows = cursor.fetchall()  # 先取出所有行
            results = [self._row_to_test_case(row) for row in rows]
            if results:
                logger.debug(f"查询到 {len(results)} 个测试用例: {results[0].id}")
            return results

    def get_tests_by_target_method(
        self, class_name: str, method_name: str
    ) -> List[TestCase]:
        """
        获取针对指定方法的测试用例

        通过检查测试用例中的测试方法的 target_method 字段来判断

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            测试用例列表，按更新时间倒序排列
        """
        with self._lock:
            cursor = self.conn.cursor()
            # 先获取所有编译成功的目标类的测试用例
            cursor.execute(
                """
                SELECT * FROM test_cases
                WHERE target_class = ? AND compile_success = 1
                ORDER BY updated_at DESC
            """,
                (class_name,),
            )

            rows = cursor.fetchall()  # 先取出所有行
            results = []
            for row in rows:
                test_case = self._row_to_test_case(row)
                # 检查这个测试用例是否包含针对指定方法的测试方法
                has_target_method = any(
                    tm.target_method == method_name for tm in test_case.methods
                )
                if has_target_method:
                    results.append(test_case)

            if results:
                logger.debug(
                    f"查询到 {len(results)} 个针对 {class_name}.{method_name} 的测试用例"
                )
            return results

    def delete_test_case(self, test_id: str) -> None:
        """删除测试用例及其所有测试方法"""
        with self._lock:
            cursor = self.conn.cursor()
            try:
                # 删除测试方法
                cursor.execute(
                    "DELETE FROM test_methods WHERE test_case_id = ?", (test_id,)
                )
                # 删除测试用例
                cursor.execute("DELETE FROM test_cases WHERE id = ?", (test_id,))
                self.conn.commit()
                logger.info(f"已删除测试用例: {test_id}")
            except Exception as e:
                logger.error(f"删除测试用例失败: {e}")
                self.conn.rollback()
                raise

    def delete_test_method(self, test_case_id: str, method_name: str) -> bool:
        """
        删除指定测试用例中的指定测试方法，并更新相关变异体的击杀信息

        Args:
            test_case_id: 测试用例 ID
            method_name: 方法名

        Returns:
            是否成功删除（True 表示有记录被删除）
        """
        with self._lock:
            cursor = self.conn.cursor()
            try:
                # 先更新变异体的击杀信息
                updated_mutants = self.batch_update_mutant_kill_info(
                    test_case_id, method_name
                )
                if updated_mutants > 0:
                    logger.info(
                        f"删除测试方法前，已更新 {updated_mutants} 个变异体的击杀信息"
                    )

                # 删除该方法
                cursor.execute(
                    """
                    DELETE FROM test_methods
                    WHERE test_case_id = ? AND method_name = ?
                """,
                    (test_case_id, method_name),
                )
                deleted_count = cursor.rowcount
                self.conn.commit()

                if deleted_count > 0:
                    logger.debug(f"已删除测试方法: {test_case_id}.{method_name}")
                    return True
                return False
            except Exception as e:
                logger.error(f"删除测试方法失败: {e}")
                self.conn.rollback()
                raise

    def delete_mutant(self, mutant_id: str) -> None:
        """删除变异体"""
        with self._lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute("DELETE FROM mutants WHERE id = ?", (mutant_id,))
                self.conn.commit()
                logger.info(f"已删除变异体: {mutant_id}")
            except Exception as e:
                logger.error(f"删除变异体失败: {e}")
                self.conn.rollback()
                raise

    def update_mutant_kill_info(
        self, mutant_id: str, test_method_to_remove: str
    ) -> bool:
        """
        从变异体的 killed_by 列表中移除指定测试方法

        Args:
            mutant_id: 变异体 ID
            test_method_to_remove: 要移除的测试方法名（格式：TestClassName.testMethodName）

        Returns:
            是否成功更新（True 表示有变更）
        """
        with self._lock:
            cursor = self.conn.cursor()
            try:
                # 获取当前的 killed_by 列表
                cursor.execute(
                    "SELECT killed_by, status FROM mutants WHERE id = ?", (mutant_id,)
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"变异体不存在: {mutant_id}")
                    return False

                killed_by_json = row["killed_by"]
                current_status = row["status"]

                if not killed_by_json:
                    return False

                killed_by = json.loads(killed_by_json)

                # 移除指定的测试方法
                if test_method_to_remove in killed_by:
                    killed_by.remove(test_method_to_remove)

                    # 更新数据库
                    new_killed_by_json = json.dumps(killed_by)

                    # 如果 killed_by 为空且状态是 killed，需要更新状态为 survived
                    new_status = current_status
                    if not killed_by and current_status == "killed":
                        new_status = "valid"
                        logger.info(
                            f"变异体 {mutant_id} 的所有击杀测试都被移除，状态从 killed 变为 valid"
                        )

                    cursor.execute(
                        """
                        UPDATE mutants
                        SET killed_by = ?, status = ?, survived = ?
                        WHERE id = ?
                    """,
                        (
                            new_killed_by_json,
                            new_status,
                            1 if not killed_by else 0,
                            mutant_id,
                        ),
                    )
                    self.conn.commit()
                    logger.debug(
                        f"已从变异体 {mutant_id} 的 killed_by 中移除: {test_method_to_remove}"
                    )
                    return True

                return False
            except Exception as e:
                logger.error(f"更新变异体击杀信息失败: {e}")
                self.conn.rollback()
                raise

    def batch_update_mutant_kill_info(self, test_case_id: str, method_name: str) -> int:
        """
        批量更新所有被指定测试方法击杀的变异体

        Args:
            test_case_id: 测试用例 ID
            method_name: 测试方法名

        Returns:
            更新的变异体数量
        """
        with self._lock:
            cursor = self.conn.cursor()
            try:
                # 构造测试方法的完整标识（TestClassName.testMethodName）
                # 先获取测试用例的类名
                cursor.execute(
                    "SELECT class_name FROM test_cases WHERE id = ?", (test_case_id,)
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"测试用例不存在: {test_case_id}")
                    return 0

                test_class_name = row["class_name"]
                test_method_full = f"{test_class_name}.{method_name}"

                # 查询所有在 killed_by 中包含该测试方法的变异体
                cursor.execute("SELECT id, killed_by, status FROM mutants")
                rows = cursor.fetchall()

                updated_count = 0
                for row in rows:
                    mutant_id = row["id"]
                    killed_by_json = row["killed_by"]
                    current_status = row["status"]

                    if not killed_by_json:
                        continue

                    killed_by = json.loads(killed_by_json)

                    # 检查是否包含要删除的测试方法
                    if test_method_full in killed_by:
                        killed_by.remove(test_method_full)
                        new_killed_by_json = json.dumps(killed_by)

                        # 如果 killed_by 为空且状态是 killed，更新状态为 valid
                        new_status = current_status
                        if not killed_by and current_status == "killed":
                            new_status = "valid"
                            logger.info(
                                f"变异体 {mutant_id} 的所有击杀测试都被移除，状态从 killed 变为 valid"
                            )

                        cursor.execute(
                            """
                            UPDATE mutants
                            SET killed_by = ?, status = ?, survived = ?
                            WHERE id = ?
                        """,
                            (
                                new_killed_by_json,
                                new_status,
                                1 if not killed_by else 0,
                                mutant_id,
                            ),
                        )
                        updated_count += 1

                self.conn.commit()
                if updated_count > 0:
                    logger.info(
                        f"已更新 {updated_count} 个变异体的击杀信息，移除测试方法: {test_method_full}"
                    )
                return updated_count
            except Exception as e:
                logger.error(f"批量更新变异体击杀信息失败: {e}")
                self.conn.rollback()
                raise

    def save_evaluation_result(self, result: EvaluationResult) -> None:
        """保存评估结果"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO evaluation_results (test_id, mutant_id, passed, error_message, execution_time, coverage, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    result.test_id,
                    result.mutant_id,
                    1 if result.passed else 0,
                    result.error_message,
                    result.execution_time,
                    result.coverage.model_dump_json() if result.coverage else None,
                    result.timestamp.isoformat(),
                ),
            )
            self.conn.commit()

    def _row_to_mutant(self, row: sqlite3.Row) -> Mutant:
        """将数据库行转换为 Mutant 对象"""
        from ..models import MutationPatch

        return Mutant(
            id=row["id"],
            class_name=row["class_name"],
            method_name=row["method_name"],
            patch=MutationPatch.model_validate_json(row["patch"]),
            status=row["status"],
            killed_by=json.loads(row["killed_by"]) if row["killed_by"] else [],
            survived=bool(row["survived"]),
            compile_error=row["compile_error"],
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else datetime.now()
            ),
            evaluated_at=(
                datetime.fromisoformat(row["evaluated_at"])
                if row["evaluated_at"]
                else None
            ),
        )

    def _row_to_test_case(self, row: sqlite3.Row) -> TestCase:
        """将数据库行转换为 TestCase 对象，从 test_methods 表加载方法

        注意：此方法假设调用者已经获取了锁，因为它会创建新的 cursor
        """
        test_case_id = row["id"]

        # 从 test_methods 表中加载该测试用例的所有方法
        # 注意：这里创建新的 cursor，所以调用者必须已经获取了锁
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM test_methods
            WHERE test_case_id = ?
        """,
            (test_case_id,),
        )

        method_rows = cursor.fetchall()
        methods = []

        for method_row in method_rows:
            method = TestMethod(
                method_name=method_row["method_name"],
                code=method_row["code"],
                target_method=method_row["target_method"],
                created_at=(
                    datetime.fromisoformat(method_row["created_at"])
                    if method_row["created_at"]
                    else datetime.now()
                ),
                updated_at=(
                    datetime.fromisoformat(method_row["updated_at"])
                    if method_row["updated_at"]
                    else datetime.now()
                ),
            )
            methods.append(method)

        return TestCase(
            id=row["id"],
            class_name=row["class_name"],
            target_class=row["target_class"],
            package_name=row["package_name"],
            imports=json.loads(row["imports"]) if row["imports"] else [],
            methods=methods,
            full_code=row["full_code"],
            compile_success=bool(row["compile_success"]),
            compile_error=row["compile_error"],
            kills=json.loads(row["kills"]) if row["kills"] else [],
            coverage_lines=(
                json.loads(row["coverage_lines"]) if row["coverage_lines"] else []
            ),
            coverage_branches=(
                json.loads(row["coverage_branches"]) if row["coverage_branches"] else []
            ),
            created_at=(
                datetime.fromisoformat(row["created_at"])
                if row["created_at"]
                else datetime.now()
            ),
            updated_at=(
                datetime.fromisoformat(row["updated_at"])
                if row["updated_at"]
                else datetime.now()
            ),
        )

    def save_method_coverage(self, coverage, iteration: int) -> None:
        """
        保存方法覆盖率数据

        Args:
            coverage: MethodCoverage 对象（来自 coverage_parser）
            iteration: 迭代次数
        """
        from datetime import datetime

        # 提取简单类名（去掉包名）
        simple_class_name = (
            coverage.class_name.split(".")[-1]
            if "." in coverage.class_name
            else coverage.class_name
        )

        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO method_coverage
                (iteration, class_name, method_name, covered_lines, missed_lines,
                 total_lines, covered_branches, total_branches, line_coverage, branch_coverage, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    iteration,
                    simple_class_name,  # 使用简单类名
                    coverage.method_name,
                    json.dumps(coverage.covered_lines),
                    json.dumps(coverage.missed_lines),
                    coverage.total_lines,
                    coverage.covered_branches,
                    coverage.total_branches,
                    coverage.line_coverage_rate,
                    coverage.branch_coverage_rate,
                    datetime.now().isoformat(),
                ),
            )
            self.conn.commit()

    def get_method_coverage(self, class_name: str, method_name: str):
        """
        获取方法的最新覆盖率

        Args:
            class_name: 类名
            method_name: 方法名

        Returns:
            MethodCoverage 对象或 None
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT * FROM method_coverage
                WHERE class_name = ? AND method_name = ?
                ORDER BY iteration DESC, id DESC
                LIMIT 1
            """,
                (class_name, method_name),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_method_coverage(row)

    def get_latest_coverage_for_class(self, class_name: str) -> List:
        """
        获取类的最新覆盖率（所有方法）

        Args:
            class_name: 类名

        Returns:
            MethodCoverage 对象列表
        """
        with self._lock:
            cursor = self.conn.cursor()
            # 获取最新迭代号
            cursor.execute(
                """
                SELECT MAX(iteration) FROM method_coverage WHERE class_name = ?
            """,
                (class_name,),
            )
            result = cursor.fetchone()
            max_iteration = result[0] if result and result[0] is not None else 0

            # 获取该迭代的所有方法覆盖率
            cursor.execute(
                """
                SELECT * FROM method_coverage
                WHERE class_name = ? AND iteration = ?
                ORDER BY method_name
            """,
                (class_name, max_iteration),
            )
            rows = cursor.fetchall()
            return [self._row_to_method_coverage(row) for row in rows]

    def get_low_coverage_methods(self, threshold: float = 0.8) -> List:
        """
        获取低覆盖率的方法

        Args:
            threshold: 覆盖率阈值（默认 0.8，即 80%）

        Returns:
            MethodCoverage 对象列表，按覆盖率从低到高排序
        """
        with self._lock:
            cursor = self.conn.cursor()
            # 获取最新迭代号
            cursor.execute("SELECT MAX(iteration) FROM method_coverage")
            result = cursor.fetchone()
            max_iteration = result[0] if result and result[0] is not None else 0

            # 获取低于阈值的方法
            cursor.execute(
                """
                SELECT * FROM method_coverage
                WHERE iteration = ? AND line_coverage < ?
                ORDER BY line_coverage ASC
            """,
                (max_iteration, threshold),
            )
            rows = cursor.fetchall()
            return [self._row_to_method_coverage(row) for row in rows]

    def get_all_method_coverage(self, iteration: Optional[int] = None) -> List:
        """
        获取所有方法的覆盖率

        Args:
            iteration: 迭代次数（如果为 None 则获取最新迭代）

        Returns:
            MethodCoverage 对象列表
        """
        with self._lock:
            cursor = self.conn.cursor()

            if iteration is None:
                # 获取最新迭代号
                cursor.execute("SELECT MAX(iteration) FROM method_coverage")
                result = cursor.fetchone()
                iteration = result[0] if result and result[0] is not None else 0

            cursor.execute(
                """
                SELECT * FROM method_coverage
                WHERE iteration = ?
                ORDER BY class_name, method_name
            """,
                (iteration,),
            )
            rows = cursor.fetchall()
            return [self._row_to_method_coverage(row) for row in rows]

    def _row_to_method_coverage(self, row: sqlite3.Row):
        """将数据库行转换为 MethodCoverage 对象"""
        from ..executor.coverage_parser import MethodCoverage

        return MethodCoverage(
            class_name=row["class_name"],
            method_name=row["method_name"],
            covered_lines=(
                json.loads(row["covered_lines"]) if row["covered_lines"] else []
            ),
            missed_lines=json.loads(row["missed_lines"]) if row["missed_lines"] else [],
            total_lines=row["total_lines"],
            covered_branches=row["covered_branches"],
            missed_branches=row["total_branches"] - row["covered_branches"],
            total_branches=row["total_branches"],
            line_coverage_rate=row["line_coverage"],
            branch_coverage_rate=row["branch_coverage"],
        )

    def save_class_mapping(
        self,
        class_name: str,
        simple_name: str,
        file_path: str,
        package_name: Optional[str] = None,
        is_public: bool = False,
        is_interface: bool = False,
    ) -> None:
        """
        保存类到文件的映射

        Args:
            class_name: 完整类名
            simple_name: 简单类名
            file_path: 源文件路径
            package_name: 包名
            is_public: 是否为 public 类
            is_interface: 是否为接口
        """
        with self._lock:
            cursor = self.conn.cursor()
            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT OR REPLACE INTO class_file_mapping
                (class_name, simple_name, file_path, package_name, is_public, is_interface, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM class_file_mapping WHERE class_name = ?), ?),
                        ?)
            """,
                (
                    class_name,
                    simple_name,
                    file_path,
                    package_name,
                    1 if is_public else 0,
                    1 if is_interface else 0,
                    class_name,
                    now,
                    now,
                ),
            )

            self.conn.commit()
            logger.debug(f"保存类映射: {class_name} -> {file_path}")

    def get_class_file_path(self, class_name: str) -> Optional[str]:
        """
        根据类名获取源文件路径

        Args:
            class_name: 类名（完整类名或简单类名）

        Returns:
            文件路径，如果找不到则返回 None
        """
        with self._lock:
            cursor = self.conn.cursor()

            # 先尝试完整类名
            cursor.execute(
                """
                SELECT file_path FROM class_file_mapping WHERE class_name = ?
            """,
                (class_name,),
            )
            result = cursor.fetchone()

            if result:
                return result["file_path"]

            # 再尝试简单类名
            cursor.execute(
                """
                SELECT file_path FROM class_file_mapping WHERE simple_name = ?
            """,
                (class_name,),
            )
            result = cursor.fetchone()

            if result:
                return result["file_path"]

            return None

    def get_classes_in_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        获取指定文件中包含的所有类

        Args:
            file_path: 文件路径

        Returns:
            类信息列表
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT * FROM class_file_mapping WHERE file_path = ?
            """,
                (file_path,),
            )

            results = cursor.fetchall()
            return [dict(row) for row in results]

    def get_all_class_mappings(self) -> List[Dict[str, Any]]:
        """
        获取所有类到文件的映射

        Returns:
            所有类映射信息列表
        """
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM class_file_mapping ORDER BY class_name")

            results = cursor.fetchall()
            return [dict(row) for row in results]

    def clear_class_mappings(self) -> None:
        """清空类映射表"""
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM class_file_mapping")
            self.conn.commit()
            logger.info("已清空类映射表")

    def close(self) -> None:
        """关闭数据库连接"""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
