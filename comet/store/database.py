"""SQLite 数据库封装"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from ..models import Mutant, TestCase, EvaluationResult

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

        # 测试用例表
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
        """保存测试用例"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO test_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            test_case.id,
            test_case.class_name,
            test_case.target_class,
            test_case.package_name,
            json.dumps(test_case.imports),
            json.dumps([m.model_dump() for m in test_case.methods]),
            test_case.full_code,
            1 if test_case.compile_success else 0,
            test_case.compile_error,
            json.dumps(test_case.kills),
            json.dumps(test_case.coverage_lines),
            json.dumps(test_case.coverage_branches),
            None,  # code_hash
            test_case.created_at.isoformat() if test_case.created_at else None,
            test_case.updated_at.isoformat() if test_case.updated_at else None,
        ))
        self.conn.commit()

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
        cursor.execute("SELECT * FROM test_cases WHERE target_class = ? AND compile_success = 1", (class_name,))
        return [self._row_to_test_case(row) for row in cursor.fetchall()]

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
        """将数据库行转换为 TestCase 对象"""
        from ..models import TestMethod
        methods_data = json.loads(row["methods"])
        methods = [TestMethod(**m) for m in methods_data]

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
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
        )

    def close(self) -> None:
        """关闭数据库连接"""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
