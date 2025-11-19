"""SQLite 数据库封装"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..models import Mutant, TestCase, TestMethod, EvaluationResult

logger = logging.getLogger(__name__)


class Database:
    """数据库管理类 - 使用 SQLite 存储测试用例、变异体和执行结果"""

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
        self._create_tables()

    def _create_tables(self) -> None:
        """创建数据表"""
        cursor = self.conn.cursor()

        # 变异体表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mutants (
                id TEXT PRIMARY KEY,
                class_name TEXT NOT NULL,
                method_name TEXT,
                patch TEXT NOT NULL,
                semantic_intent TEXT NOT NULL,
                pattern_id TEXT,
                status TEXT DEFAULT 'pending',
                killed_by TEXT,
                survived INTEGER DEFAULT 0,
                compile_error TEXT,
                code_hash TEXT,
                created_at TEXT,
                evaluated_at TEXT
            )
        """)

        # 测试方法表（支持方法级别的版本控制）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_methods (
                test_case_id TEXT NOT NULL,
                method_name TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                code TEXT NOT NULL,
                target_method TEXT NOT NULL,
                description TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (test_case_id, method_name, version)
            )
        """)

        # 测试用例表（测试类级别的元数据）
        cursor.execute("""
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
                version INTEGER NOT NULL DEFAULT 1,
                code_hash TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        # 评估结果表
        cursor.execute("""
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
        """)

        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mutants_status ON mutants(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_mutants_hash ON mutants(code_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tests_hash ON test_cases(code_hash)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_test_methods_case ON test_methods(test_case_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_test_methods_name ON test_methods(test_case_id, method_name)
        """)

        self.conn.commit()

    def save_mutant(self, mutant: Mutant) -> None:
        """保存变异体"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO mutants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mutant.id,
            mutant.class_name,
            mutant.method_name,
            mutant.patch.model_dump_json(),
            mutant.semantic_intent,
            mutant.pattern_id,
            mutant.status,
            json.dumps(mutant.killed_by),
            1 if mutant.survived else 0,
            mutant.compile_error,
            None,  # code_hash
            mutant.created_at.isoformat() if mutant.created_at else None,
            mutant.evaluated_at.isoformat() if mutant.evaluated_at else None,
        ))
        self.conn.commit()

    def get_mutant(self, mutant_id: str) -> Optional[Mutant]:
        """获取变异体"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM mutants WHERE id = ?", (mutant_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_mutant(row)

    def get_all_mutants(self, status: Optional[str] = None) -> List[Mutant]:
        """获取所有变异体"""
        cursor = self.conn.cursor()
        if status:
            cursor.execute("SELECT * FROM mutants WHERE status = ?", (status,))
        else:
            cursor.execute("SELECT * FROM mutants")
        return [self._row_to_mutant(row) for row in cursor.fetchall()]

    def get_pending_mutants(self) -> List[Mutant]:
        """获取待评估的变异体"""
        return self.get_all_mutants(status="pending")

    def get_valid_mutants(self) -> List[Mutant]:
        """获取有效的变异体（已通过静态检查）"""
        return self.get_all_mutants(status="valid")

    def get_mutants_by_class(self, class_name: str) -> List[Mutant]:
        """获取指定类的所有变异体"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM mutants WHERE class_name = ?", (class_name,))
        return [self._row_to_mutant(row) for row in cursor.fetchall()]

    def save_test_case(self, test_case: TestCase) -> None:
        """
        保存测试用例（支持方法级别的版本控制）

        对于每个测试方法：
        - 如果是新方法，版本号为1
        - 如果是现有方法，比较代码是否变化，如果变化则版本号+1
        """
        cursor = self.conn.cursor()

        # 检查测试用例是否存在
        cursor.execute("SELECT id FROM test_cases WHERE id = ?", (test_case.id,))
        case_exists = cursor.fetchone() is not None

        if case_exists:
            logger.debug(f"测试用例 {test_case.id} 已存在，将更新测试方法")
        else:
            logger.debug(f"创建新测试用例: {test_case.id}")

        # 处理每个测试方法
        for method in test_case.methods:
            # 获取该方法的当前版本（如果存在）
            cursor.execute("""
                SELECT version, code FROM test_methods
                WHERE test_case_id = ? AND method_name = ?
                ORDER BY version DESC LIMIT 1
            """, (test_case.id, method.method_name))

            existing = cursor.fetchone()

            if existing:
                current_version = existing[0]
                existing_code = existing[1]

                # 检查代码是否有变化
                if existing_code.strip() != method.code.strip():
                    # 代码有变化，增加版本号
                    method.version = current_version + 1
                    method.updated_at = datetime.now()
                    logger.debug(f"  方法 {method.method_name}: v{current_version} -> v{method.version} (代码已更新)")
                else:
                    # 代码没变化，保持版本号
                    method.version = current_version
                    logger.debug(f"  方法 {method.method_name}: v{current_version} (无变化)")
                    continue  # 跳过保存，避免重复
            else:
                # 新方法，版本号为1
                method.version = 1
                method.created_at = datetime.now()
                method.updated_at = datetime.now()
                logger.debug(f"  新方法 {method.method_name}: v1")

            # 保存方法到数据库
            cursor.execute("""
                INSERT OR REPLACE INTO test_methods
                (test_case_id, method_name, version, code, target_method, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                test_case.id,
                method.method_name,
                method.version,
                method.code,
                method.target_method,
                method.description,
                method.created_at.isoformat() if method.created_at else None,
                method.updated_at.isoformat() if method.updated_at else None,
            ))

        # 更新测试用例主表（不再需要版本号，因为版本控制在方法级别）
        test_case.updated_at = datetime.now()
        cursor.execute("""
            INSERT OR REPLACE INTO test_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            test_case.id,
            test_case.class_name,
            test_case.target_class,
            test_case.package_name,
            json.dumps(test_case.imports),
            json.dumps([m.model_dump(mode='json') for m in test_case.methods]),
            test_case.full_code,
            1 if test_case.compile_success else 0,
            test_case.compile_error,
            json.dumps(test_case.kills),
            json.dumps(test_case.coverage_lines),
            json.dumps(test_case.coverage_branches),
            test_case.version,
            None,  # code_hash
            test_case.created_at.isoformat() if test_case.created_at else None,
            test_case.updated_at.isoformat() if test_case.updated_at else None,
        ))
        self.conn.commit()

        logger.info(f"已保存测试用例 {test_case.id}，包含 {len(test_case.methods)} 个测试方法")

    def get_test_case(self, test_id: str) -> Optional[TestCase]:
        """获取测试用例"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM test_cases WHERE id = ?", (test_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_test_case(row)

    def get_all_test_cases(self) -> List[TestCase]:
        """获取所有测试用例"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM test_cases WHERE compile_success = 1")
        return [self._row_to_test_case(row) for row in cursor.fetchall()]

    def get_all_tests(self) -> List[TestCase]:
        """获取所有测试用例（别名）"""
        return self.get_all_test_cases()

    def get_tests_by_target_class(self, class_name: str) -> List[TestCase]:
        """获取指定目标类的所有测试"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM test_cases
            WHERE target_class = ? AND compile_success = 1
            ORDER BY updated_at DESC
        """, (class_name,))
        results = [self._row_to_test_case(row) for row in cursor.fetchall()]
        if results:
            logger.debug(f"查询到 {len(results)} 个测试用例: {results[0].id}")
        return results

    def save_evaluation_result(self, result: EvaluationResult) -> None:
        """保存评估结果"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO evaluation_results (test_id, mutant_id, passed, error_message, execution_time, coverage, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            result.test_id,
            result.mutant_id,
            1 if result.passed else 0,
            result.error_message,
            result.execution_time,
            result.coverage.model_dump_json() if result.coverage else None,
            result.timestamp.isoformat(),
        ))
        self.conn.commit()

    def _row_to_mutant(self, row: sqlite3.Row) -> Mutant:
        """将数据库行转换为 Mutant 对象"""
        from ..models import MutationPatch
        return Mutant(
            id=row["id"],
            class_name=row["class_name"],
            method_name=row["method_name"],
            patch=MutationPatch.model_validate_json(row["patch"]),
            semantic_intent=row["semantic_intent"],
            pattern_id=row["pattern_id"],
            status=row["status"],
            killed_by=json.loads(row["killed_by"]) if row["killed_by"] else [],
            survived=bool(row["survived"]),
            compile_error=row["compile_error"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            evaluated_at=datetime.fromisoformat(row["evaluated_at"]) if row["evaluated_at"] else None,
        )

    def _row_to_test_case(self, row: sqlite3.Row) -> TestCase:
        """将数据库行转换为 TestCase 对象，从 test_methods 表加载最新版本的方法"""
        test_case_id = row["id"]

        # 从 test_methods 表中加载该测试用例的所有最新版本的方法
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT tm.* FROM test_methods tm
            INNER JOIN (
                SELECT test_case_id, method_name, MAX(version) as max_version
                FROM test_methods
                WHERE test_case_id = ?
                GROUP BY test_case_id, method_name
            ) latest
            ON tm.test_case_id = latest.test_case_id
            AND tm.method_name = latest.method_name
            AND tm.version = latest.max_version
        """, (test_case_id,))

        method_rows = cursor.fetchall()
        methods = []

        for method_row in method_rows:
            method = TestMethod(
                method_name=method_row["method_name"],
                code=method_row["code"],
                target_method=method_row["target_method"],
                description=method_row["description"],
                version=method_row["version"],
                created_at=datetime.fromisoformat(method_row["created_at"]) if method_row["created_at"] else datetime.now(),
                updated_at=datetime.fromisoformat(method_row["updated_at"]) if method_row["updated_at"] else datetime.now(),
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
            coverage_lines=json.loads(row["coverage_lines"]) if row["coverage_lines"] else [],
            coverage_branches=json.loads(row["coverage_branches"]) if row["coverage_branches"] else [],
            version=row["version"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
        )

    def get_test_method_versions(self, test_case_id: str, method_name: str) -> List[TestMethod]:
        """获取测试方法的所有历史版本"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM test_methods
            WHERE test_case_id = ? AND method_name = ?
            ORDER BY version DESC
        """, (test_case_id, method_name))

        methods = []
        for row in cursor.fetchall():
            method = TestMethod(
                method_name=row["method_name"],
                code=row["code"],
                target_method=row["target_method"],
                description=row["description"],
                version=row["version"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
            )
            methods.append(method)

        return methods

    def get_all_test_methods(self, test_case_id: str) -> Dict[str, List[TestMethod]]:
        """获取测试用例的所有方法及其历史版本"""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM test_methods
            WHERE test_case_id = ?
            ORDER BY method_name, version DESC
        """, (test_case_id,))

        methods_by_name: Dict[str, List[TestMethod]] = {}
        for row in cursor.fetchall():
            method_name = row["method_name"]
            method = TestMethod(
                method_name=method_name,
                code=row["code"],
                target_method=row["target_method"],
                description=row["description"],
                version=row["version"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
                updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
            )
            if method_name not in methods_by_name:
                methods_by_name[method_name] = []
            methods_by_name[method_name].append(method)

        return methods_by_name

    def close(self) -> None:
        """关闭数据库连接"""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
