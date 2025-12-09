"""知识库持久化存储"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..models import Contract, Pattern

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """知识库存储 - 持久化 Patterns 和 Contracts"""

    def __init__(self, db_path: str = "knowledge.db"):
        """
        初始化知识库存储

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

        # 契约表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id TEXT PRIMARY KEY,
                class_name TEXT NOT NULL,
                method_name TEXT NOT NULL,
                method_signature TEXT NOT NULL,
                preconditions TEXT,
                postconditions TEXT,
                exceptions TEXT,
                description TEXT,
                source TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                created_at TEXT
            )
        """
        )

        # 模式表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS patterns (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                template TEXT NOT NULL,
                examples TEXT,
                mutation_strategy TEXT,
                confidence REAL DEFAULT 1.0,
                success_rate REAL DEFAULT 0.0,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        """
        )

        # 创建索引
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contracts_class ON contracts(class_name)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_contracts_method ON contracts(method_name)
        """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_patterns_category ON patterns(category)
        """
        )

        self.conn.commit()

    def save_contract(self, contract: Contract) -> None:
        """保存契约"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                contract.id,
                contract.class_name,
                contract.method_name,
                contract.method_signature,
                json.dumps(contract.preconditions),
                json.dumps(contract.postconditions),
                json.dumps(contract.exceptions),
                contract.description,
                contract.source,
                contract.confidence,
                contract.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_contract(self, contract_id: str) -> Optional[Contract]:
        """获取契约"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_contract(row)

    def get_contracts_by_class(self, class_name: str) -> List[Contract]:
        """获取类的所有契约"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM contracts WHERE class_name = ?", (class_name,))
        return [self._row_to_contract(row) for row in cursor.fetchall()]

    def get_contracts_by_method(
        self, class_name: str, method_name: str
    ) -> List[Contract]:
        """获取方法的契约"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM contracts WHERE class_name = ? AND method_name = ?",
            (class_name, method_name),
        )
        return [self._row_to_contract(row) for row in cursor.fetchall()]

    def get_all_contracts(self) -> List[Contract]:
        """获取所有契约"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM contracts")
        return [self._row_to_contract(row) for row in cursor.fetchall()]

    def save_pattern(self, pattern: Pattern) -> None:
        """保存模式"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO patterns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                pattern.id,
                pattern.name,
                pattern.category,
                pattern.description,
                pattern.template,
                json.dumps(pattern.examples),
                pattern.mutation_strategy,
                pattern.confidence,
                pattern.success_rate,
                pattern.usage_count,
                pattern.created_at.isoformat(),
                pattern.updated_at.isoformat(),
            ),
        )
        self.conn.commit()

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        """获取模式"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM patterns WHERE id = ?", (pattern_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_pattern(row)

    def get_patterns_by_category(self, category: str) -> List[Pattern]:
        """获取特定类别的模式"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM patterns WHERE category = ?", (category,))
        return [self._row_to_pattern(row) for row in cursor.fetchall()]

    def get_all_patterns(self) -> List[Pattern]:
        """获取所有模式"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM patterns ORDER BY success_rate DESC, usage_count DESC"
        )
        return [self._row_to_pattern(row) for row in cursor.fetchall()]

    def update_pattern_stats(self, pattern_id: str, success: bool) -> None:
        """更新模式统计信息"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE patterns
            SET usage_count = usage_count + 1,
                success_rate = (success_rate * usage_count + ?) / (usage_count + 1),
                updated_at = ?
            WHERE id = ?
        """,
            (1.0 if success else 0.0, datetime.now().isoformat(), pattern_id),
        )
        self.conn.commit()

    def _row_to_contract(self, row: sqlite3.Row) -> Contract:
        """将数据库行转换为 Contract 对象"""
        return Contract(
            id=row["id"],
            class_name=row["class_name"],
            method_name=row["method_name"],
            method_signature=row["method_signature"],
            preconditions=(
                json.loads(row["preconditions"]) if row["preconditions"] else []
            ),
            postconditions=(
                json.loads(row["postconditions"]) if row["postconditions"] else []
            ),
            exceptions=json.loads(row["exceptions"]) if row["exceptions"] else [],
            description=row["description"],
            source=row["source"],
            confidence=row["confidence"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_pattern(self, row: sqlite3.Row) -> Pattern:
        """将数据库行转换为 Pattern 对象"""
        return Pattern(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            description=row["description"],
            template=row["template"],
            examples=json.loads(row["examples"]) if row["examples"] else [],
            mutation_strategy=row["mutation_strategy"],
            confidence=row["confidence"],
            success_rate=row["success_rate"],
            usage_count=row["usage_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def close(self) -> None:
        """关闭数据库连接"""
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
