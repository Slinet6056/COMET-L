"""核心数据模型定义"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class Contract(BaseModel):
    """契约模型 - 描述代码的前置条件、后置条件和异常"""

    id: str = Field(description="契约 ID")
    class_name: str = Field(description="类名")
    method_name: str = Field(description="方法名")
    method_signature: str = Field(description="方法签名")
    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    postconditions: List[str] = Field(default_factory=list, description="后置条件")
    exceptions: List[str] = Field(default_factory=list, description="异常条件")
    description: Optional[str] = Field(default=None, description="描述")
    source: str = Field(description="来源（如 javadoc、comments、tests）")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class Pattern(BaseModel):
    """缺陷模式模型 - 描述常见的代码缺陷模式"""

    id: str = Field(description="模式 ID")
    name: str = Field(description="模式名称")
    category: str = Field(description="类别（如 null_pointer、boundary、resource_leak）")
    description: str = Field(description="模式描述")
    template: str = Field(description="变异模板")
    examples: List[str] = Field(default_factory=list, description="示例")
    mutation_strategy: Optional[str] = Field(default=None, description="变异策略")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度")
    success_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="成功率（发现缺陷的比例）")
    usage_count: int = Field(default=0, ge=0, description="使用次数")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class MutationPatch(BaseModel):
    """变异补丁 - 描述如何修改代码"""

    file_path: str = Field(description="文件路径")
    line_start: int = Field(ge=1, description="起始行号")
    line_end: int = Field(ge=1, description="结束行号")
    original_code: str = Field(description="原始代码")
    mutated_code: str = Field(description="变异后代码")

    class Config:
        validate_assignment = True


class Mutant(BaseModel):
    """变异体模型 - 描述一个代码变异"""

    id: str = Field(description="变异体 ID")
    class_name: str = Field(description="类名")
    method_name: Optional[str] = Field(default=None, description="方法名")
    patch: MutationPatch = Field(description="变异补丁")
    semantic_intent: str = Field(description="语义意图（这个变异试图暴露什么问题）")
    pattern_id: Optional[str] = Field(default=None, description="关联的缺陷模式 ID")
    status: str = Field(
        default="pending",
        description="状态（pending、valid、invalid、killed、survived）"
    )
    killed_by: List[str] = Field(default_factory=list, description="被哪些测试击杀")
    survived: bool = Field(default=False, description="是否幸存")
    compile_error: Optional[str] = Field(default=None, description="编译错误信息")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    evaluated_at: Optional[datetime] = Field(default=None, description="评估时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class TestMethod(BaseModel):
    """测试方法 - 单个测试方法的信息"""

    method_name: str = Field(description="测试方法名")
    code: str = Field(description="测试方法代码")
    target_method: str = Field(description="目标被测方法")
    description: Optional[str] = Field(default=None, description="测试描述")


class TestCase(BaseModel):
    """测试用例模型 - 描述一个测试类"""

    id: str = Field(description="测试用例 ID")
    class_name: str = Field(description="测试类名")
    target_class: str = Field(description="目标被测类")
    package_name: Optional[str] = Field(default=None, description="包名")
    imports: List[str] = Field(default_factory=list, description="导入语句")
    methods: List[TestMethod] = Field(default_factory=list, description="测试方法列表")
    full_code: Optional[str] = Field(default=None, description="完整测试类代码")
    compile_success: bool = Field(default=False, description="是否编译成功")
    compile_error: Optional[str] = Field(default=None, description="编译错误信息")
    kills: List[str] = Field(default_factory=list, description="击杀的变异体 ID 列表")
    coverage_lines: List[int] = Field(default_factory=list, description="覆盖的代码行")
    coverage_branches: List[str] = Field(default_factory=list, description="覆盖的分支")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.now, description="更新时间")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class CoverageInfo(BaseModel):
    """覆盖率信息"""

    class_name: str = Field(description="类名")
    method_name: Optional[str] = Field(default=None, description="方法名")
    covered_lines: List[int] = Field(default_factory=list, description="覆盖的行")
    total_lines: int = Field(ge=0, description="总行数")
    covered_branches: int = Field(default=0, ge=0, description="覆盖的分支数")
    total_branches: int = Field(default=0, ge=0, description="总分支数")
    line_coverage: float = Field(default=0.0, ge=0.0, le=1.0, description="行覆盖率")
    branch_coverage: float = Field(default=0.0, ge=0.0, le=1.0, description="分支覆盖率")


class EvaluationResult(BaseModel):
    """评估结果 - 单次测试执行的结果"""

    test_id: str = Field(description="测试 ID")
    mutant_id: Optional[str] = Field(default=None, description="变异体 ID（如果有）")
    passed: bool = Field(description="是否通过")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(ge=0.0, description="执行时间（秒）")
    coverage: Optional[CoverageInfo] = Field(default=None, description="覆盖率信息")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class KillMatrix(BaseModel):
    """击杀矩阵 K(T,M) - 记录哪些测试击杀了哪些变异体"""

    matrix: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="键为变异体 ID，值为击杀它的测试 ID 列表"
    )

    def add_kill(self, mutant_id: str, test_id: str) -> None:
        """记录击杀"""
        if mutant_id not in self.matrix:
            self.matrix[mutant_id] = []
        if test_id not in self.matrix[mutant_id]:
            self.matrix[mutant_id].append(test_id)

    def is_killed(self, mutant_id: str) -> bool:
        """检查变异体是否被击杀"""
        return mutant_id in self.matrix and len(self.matrix[mutant_id]) > 0

    def get_killers(self, mutant_id: str) -> List[str]:
        """获取击杀特定变异体的测试列表"""
        return self.matrix.get(mutant_id, [])

    def get_survived_mutants(self, all_mutant_ids: List[str]) -> List[str]:
        """获取幸存的变异体列表"""
        return [mid for mid in all_mutant_ids if not self.is_killed(mid)]


class Metrics(BaseModel):
    """系统度量指标"""

    iteration: int = Field(ge=0, description="迭代次数")
    total_mutants: int = Field(default=0, ge=0, description="总变异体数")
    killed_mutants: int = Field(default=0, ge=0, description="被击杀的变异体数")
    survived_mutants: int = Field(default=0, ge=0, description="幸存的变异体数")
    total_tests: int = Field(default=0, ge=0, description="总测试数")
    mutation_score: float = Field(default=0.0, ge=0.0, le=1.0, description="变异分数")
    line_coverage: float = Field(default=0.0, ge=0.0, le=1.0, description="行覆盖率")
    branch_coverage: float = Field(default=0.0, ge=0.0, le=1.0, description="分支覆盖率")
    llm_calls: int = Field(default=0, ge=0, description="LLM 调用次数")
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def calculate_mutation_score(self) -> None:
        """计算变异分数"""
        if self.total_mutants > 0:
            self.mutation_score = self.killed_mutants / self.total_mutants
        else:
            self.mutation_score = 0.0
