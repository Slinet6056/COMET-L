"""JaCoCo 覆盖率报告解析器"""

import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MethodCoverage:
    """方法级覆盖率数据"""

    class_name: str
    method_name: str
    covered_lines: List[int]
    missed_lines: List[int]
    total_lines: int
    covered_branches: int
    missed_branches: int
    total_branches: int
    line_coverage_rate: float
    branch_coverage_rate: float
    source_filename: Optional[str] = None  # 源文件名，用于聚合


@dataclass
class SourceFileCoverage:
    """源文件级覆盖率数据"""

    source_filename: str
    covered_lines: List[int]  # 唯一的已覆盖行号
    missed_lines: List[int]  # 唯一的未覆盖行号
    total_lines: int
    covered_branches: int
    total_branches: int
    line_coverage_rate: float
    branch_coverage_rate: float
    classes: List[str]  # 该源文件包含的类列表


class CoverageParser:
    """JaCoCo XML 报告解析器"""

    def __init__(self):
        """初始化解析器"""
        pass

    def parse_jacoco_xml_with_lines(self, xml_path: str) -> List[MethodCoverage]:
        """
        解析 JaCoCo XML 报告文件（包含精确的方法级行号映射）

        通过 method 元素的 line 属性和 sourcefile 的行覆盖信息，
        可以精确推断每个方法包含哪些行，以及这些行的覆盖状态

        Args:
            xml_path: JaCoCo XML 报告文件路径

        Returns:
            方法覆盖率列表（包含精确的 covered_lines 和 missed_lines）
        """
        path = Path(xml_path)
        if not path.exists():
            logger.warning(f"JaCoCo 报告文件不存在: {xml_path}")
            return []

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            method_coverages = []

            for package in root.findall(".//package"):
                # 首先收集所有 sourcefile 的行覆盖信息
                source_line_info = {}  # {source_filename: {line_nr: {covered, missed}}}

                for sourcefile in package.findall("sourcefile"):
                    source_name = sourcefile.get("name", "")
                    source_line_info[source_name] = {}

                    for line in sourcefile.findall("line"):
                        line_nr = int(line.get("nr", 0))
                        covered_instructions = int(line.get("ci", 0))
                        missed_instructions = int(line.get("mi", 0))

                        source_line_info[source_name][line_nr] = {
                            "covered": covered_instructions > 0,
                            "missed": missed_instructions > 0,
                        }

                # 然后解析每个类的方法
                for clazz in package.findall("class"):
                    class_name = clazz.get("name", "").replace("/", ".")
                    source_filename = clazz.get("sourcefilename", "")

                    # 收集该类所有方法及其起始行号
                    methods_info = []
                    for method in clazz.findall("method"):
                        method_name = method.get("name", "")
                        # 跳过构造函数和 lambda 表达式（编译器生成的内部方法）
                        if method_name == "<init>" or method_name == "<clinit>":
                            continue
                        if method_name.startswith("lambda$"):
                            continue

                        start_line = int(method.get("line", 0))
                        methods_info.append(
                            {
                                "element": method,
                                "name": method_name,
                                "start_line": start_line,
                            }
                        )

                    # 按起始行号排序
                    methods_info.sort(key=lambda x: x["start_line"])

                    # 为每个方法推断行范围并提取覆盖信息
                    for i, method_info in enumerate(methods_info):
                        method = method_info["element"]
                        method_name = method_info["name"]
                        start_line = method_info["start_line"]

                        # 推断方法结束行：下一个方法的起始行-1，或文件末尾
                        if i + 1 < len(methods_info):
                            end_line = methods_info[i + 1]["start_line"] - 1
                        else:
                            # 最后一个方法：使用源文件中的最大行号
                            if source_filename in source_line_info:
                                end_line = (
                                    max(source_line_info[source_filename].keys())
                                    if source_line_info[source_filename]
                                    else start_line
                                )
                            else:
                                end_line = start_line + 100  # 默认范围

                        # 从 sourcefile 中提取该方法范围内的行覆盖信息
                        covered_lines = []
                        missed_lines = []

                        if source_filename in source_line_info:
                            for line_nr in range(start_line, end_line + 1):
                                if line_nr in source_line_info[source_filename]:
                                    line_info = source_line_info[source_filename][
                                        line_nr
                                    ]
                                    if line_info["covered"]:
                                        covered_lines.append(line_nr)
                                    elif line_info["missed"]:
                                        missed_lines.append(line_nr)

                        # 获取覆盖率计数器（用于验证）
                        line_counter = None
                        branch_counter = None

                        for counter in method.findall("counter"):
                            counter_type = counter.get("type", "")
                            if counter_type == "LINE":
                                line_counter = counter
                            elif counter_type == "BRANCH":
                                branch_counter = counter

                        # 解析行覆盖率统计
                        if line_counter is not None:
                            missed_lines_count = int(line_counter.get("missed", 0))
                            covered_lines_count = int(line_counter.get("covered", 0))
                            total_lines = missed_lines_count + covered_lines_count
                        else:
                            # 如果没有 counter，使用实际收集的行数
                            total_lines = len(covered_lines) + len(missed_lines)
                            covered_lines_count = len(covered_lines)
                            missed_lines_count = len(missed_lines)

                        # 解析分支覆盖率
                        if branch_counter is not None:
                            missed_branches = int(branch_counter.get("missed", 0))
                            covered_branches = int(branch_counter.get("covered", 0))
                            total_branches = missed_branches + covered_branches
                        else:
                            missed_branches = 0
                            covered_branches = 0
                            total_branches = 0

                        # 计算覆盖率
                        if total_lines > 0:
                            line_coverage_rate = covered_lines_count / total_lines
                        else:
                            line_coverage_rate = 0.0

                        if total_branches > 0:
                            branch_coverage_rate = covered_branches / total_branches
                        else:
                            branch_coverage_rate = 0.0

                        method_coverage = MethodCoverage(
                            class_name=class_name,
                            method_name=method_name,
                            covered_lines=covered_lines,
                            missed_lines=missed_lines,
                            total_lines=total_lines,
                            covered_branches=covered_branches,
                            missed_branches=missed_branches,
                            total_branches=total_branches,
                            line_coverage_rate=line_coverage_rate,
                            branch_coverage_rate=branch_coverage_rate,
                            source_filename=source_filename,
                        )

                        method_coverages.append(method_coverage)

            logger.info(
                f"成功解析 {len(method_coverages)} 个方法的覆盖率信息（含精确行号）"
            )
            return method_coverages

        except ET.ParseError as e:
            logger.warning(f"解析 JaCoCo XML 失败: {e}")
            return []
        except Exception as e:
            logger.warning(f"解析覆盖率报告时出错: {e}")
            return []

    def aggregate_coverage_by_sourcefile(
        self, method_coverages: List[MethodCoverage]
    ) -> List[SourceFileCoverage]:
        """
        按源文件聚合覆盖率，避免重复计算行号

        当一个源文件包含多个类时，每个类的方法都会被单独统计，
        导致同一个源文件的行被重复计算。此方法通过按源文件分组，
        统计唯一的行号来解决这个问题。

        Args:
            method_coverages: 方法级覆盖率列表

        Returns:
            源文件级覆盖率列表
        """
        if not method_coverages:
            return []

        # 按源文件分组
        file_to_methods: Dict[str, List[MethodCoverage]] = {}
        for mc in method_coverages:
            source_file = mc.source_filename or "unknown"
            if source_file not in file_to_methods:
                file_to_methods[source_file] = []
            file_to_methods[source_file].append(mc)

        source_coverages = []

        for source_filename, methods in file_to_methods.items():
            # 收集该源文件中所有唯一的已覆盖行和未覆盖行
            all_covered_lines = set()
            all_missed_lines = set()
            all_classes = set()
            total_covered_branches = 0
            total_branches = 0

            for mc in methods:
                all_covered_lines.update(mc.covered_lines)
                all_missed_lines.update(mc.missed_lines)
                all_classes.add(mc.class_name)
                total_covered_branches += mc.covered_branches
                total_branches += mc.total_branches

            # 如果一个行既在 covered 又在 missed 中（理论上不应该发生），优先算作 covered
            all_missed_lines -= all_covered_lines

            # 排序行号
            covered_lines_sorted = sorted(list(all_covered_lines))
            missed_lines_sorted = sorted(list(all_missed_lines))

            total_lines = len(covered_lines_sorted) + len(missed_lines_sorted)

            # 计算覆盖率
            line_coverage_rate = (
                len(covered_lines_sorted) / total_lines if total_lines > 0 else 0.0
            )
            branch_coverage_rate = (
                total_covered_branches / total_branches if total_branches > 0 else 0.0
            )

            source_coverage = SourceFileCoverage(
                source_filename=source_filename,
                covered_lines=covered_lines_sorted,
                missed_lines=missed_lines_sorted,
                total_lines=total_lines,
                covered_branches=total_covered_branches,
                total_branches=total_branches,
                line_coverage_rate=line_coverage_rate,
                branch_coverage_rate=branch_coverage_rate,
                classes=sorted(list(all_classes)),
            )

            source_coverages.append(source_coverage)

            logger.debug(
                f"源文件 {source_filename}: "
                f"覆盖 {len(covered_lines_sorted)}/{total_lines} 行 ({line_coverage_rate:.1%}), "
                f"包含 {len(all_classes)} 个类: {', '.join(sorted(all_classes))}"
            )

        logger.info(f"聚合得到 {len(source_coverages)} 个源文件的覆盖率信息")
        return source_coverages

    def parse_sourcefile_coverage(self, xml_path: str) -> List[SourceFileCoverage]:
        """
        直接从 JaCoCo XML 的 sourcefile 元素解析源文件级覆盖率

        这是最准确的方式，因为 sourcefile 元素包含了精确的行覆盖信息，
        不需要从 method 元素推断行范围。

        Args:
            xml_path: JaCoCo XML 报告文件路径

        Returns:
            源文件级覆盖率列表
        """
        path = Path(xml_path)
        if not path.exists():
            logger.warning(f"JaCoCo 报告文件不存在: {xml_path}")
            return []

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            source_coverages = []

            for package in root.findall(".//package"):
                for sourcefile in package.findall("sourcefile"):
                    source_name = sourcefile.get("name", "")

                    # 从 line 元素收集行覆盖信息
                    covered_lines = []
                    missed_lines = []

                    for line in sourcefile.findall("line"):
                        line_nr = int(line.get("nr", 0))
                        covered_instructions = int(line.get("ci", 0))
                        missed_instructions = int(line.get("mi", 0))

                        if covered_instructions > 0:
                            covered_lines.append(line_nr)
                        elif missed_instructions > 0:
                            missed_lines.append(line_nr)

                    # 从 counter 元素获取统计信息
                    line_counter = sourcefile.find('counter[@type="LINE"]')
                    branch_counter = sourcefile.find('counter[@type="BRANCH"]')

                    if line_counter is not None:
                        total_lines = int(line_counter.get("covered", 0)) + int(
                            line_counter.get("missed", 0)
                        )
                        covered_count = int(line_counter.get("covered", 0))
                    else:
                        total_lines = len(covered_lines) + len(missed_lines)
                        covered_count = len(covered_lines)

                    if branch_counter is not None:
                        total_branches = int(branch_counter.get("covered", 0)) + int(
                            branch_counter.get("missed", 0)
                        )
                        covered_branches = int(branch_counter.get("covered", 0))
                    else:
                        total_branches = 0
                        covered_branches = 0

                    # 计算覆盖率
                    line_coverage_rate = (
                        covered_count / total_lines if total_lines > 0 else 0.0
                    )
                    branch_coverage_rate = (
                        covered_branches / total_branches if total_branches > 0 else 0.0
                    )

                    # 收集该源文件包含的类
                    classes = []
                    for clazz in package.findall("class"):
                        if clazz.get("sourcefilename") == source_name:
                            class_name = clazz.get("name", "").replace("/", ".")
                            classes.append(class_name)

                    source_coverage = SourceFileCoverage(
                        source_filename=source_name,
                        covered_lines=sorted(covered_lines),
                        missed_lines=sorted(missed_lines),
                        total_lines=total_lines,
                        covered_branches=covered_branches,
                        total_branches=total_branches,
                        line_coverage_rate=line_coverage_rate,
                        branch_coverage_rate=branch_coverage_rate,
                        classes=sorted(classes),
                    )

                    source_coverages.append(source_coverage)

                    logger.debug(
                        f"源文件 {source_name}: "
                        f"覆盖 {covered_count}/{total_lines} 行 ({line_coverage_rate:.1%}), "
                        f"分支 {covered_branches}/{total_branches} ({branch_coverage_rate:.1%})"
                    )

            logger.info(
                f"从 sourcefile 元素解析得到 {len(source_coverages)} 个源文件的覆盖率信息"
            )
            return source_coverages

        except ET.ParseError as e:
            logger.warning(f"解析 JaCoCo XML 失败: {e}")
            return []
        except Exception as e:
            logger.warning(f"解析覆盖率报告时出错: {e}")
            return []

    def aggregate_global_coverage(
        self, method_coverages: List[MethodCoverage]
    ) -> Dict[str, Any]:
        """
        计算全局覆盖率（基于源文件聚合，避免重复计算）

        注意：此方法基于 method_coverages 计算，由于方法行号推断可能不准确，
        建议使用 aggregate_global_coverage_from_xml 方法直接从 XML 解析。

        Args:
            method_coverages: 方法级覆盖率列表

        Returns:
            全局覆盖率字典，包含 line_coverage, branch_coverage, total_lines 等
        """
        if not method_coverages:
            return {
                "line_coverage": 0.0,
                "branch_coverage": 0.0,
                "total_lines": 0,
                "covered_lines_count": 0,
                "total_branches": 0,
                "covered_branches": 0,
            }

        # 先按源文件聚合
        source_coverages = self.aggregate_coverage_by_sourcefile(method_coverages)

        # 然后计算全局统计
        total_covered_lines = 0
        total_lines = 0
        total_covered_branches = 0
        total_branches = 0

        for sc in source_coverages:
            total_covered_lines += len(sc.covered_lines)
            total_lines += sc.total_lines
            total_covered_branches += sc.covered_branches
            total_branches += sc.total_branches

        line_coverage = total_covered_lines / total_lines if total_lines > 0 else 0.0
        branch_coverage = (
            total_covered_branches / total_branches if total_branches > 0 else 0.0
        )

        logger.info(
            f"全局覆盖率: "
            f"行覆盖率 {line_coverage:.1%} ({total_covered_lines}/{total_lines}), "
            f"分支覆盖率 {branch_coverage:.1%} ({total_covered_branches}/{total_branches})"
        )

        return {
            "line_coverage": line_coverage,
            "branch_coverage": branch_coverage,
            "total_lines": total_lines,
            "covered_lines_count": total_covered_lines,
            "total_branches": total_branches,
            "covered_branches": total_covered_branches,
        }

    def aggregate_global_coverage_from_xml(self, xml_path: str) -> Dict[str, Any]:
        """
        直接从 JaCoCo XML 文件计算全局覆盖率

        这是最准确的方式，直接使用 XML 报告中的 counter 元素，
        不需要通过方法级覆盖率推断。

        Args:
            xml_path: JaCoCo XML 报告文件路径

        Returns:
            全局覆盖率字典，包含 line_coverage, branch_coverage, total_lines 等
        """
        path = Path(xml_path)
        if not path.exists():
            logger.warning(f"JaCoCo 报告文件不存在: {xml_path}")
            return {
                "line_coverage": 0.0,
                "branch_coverage": 0.0,
                "total_lines": 0,
                "covered_lines_count": 0,
                "total_branches": 0,
                "covered_branches": 0,
            }

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # 直接从报告根元素获取全局 counter
            line_counter = root.find('counter[@type="LINE"]')
            branch_counter = root.find('counter[@type="BRANCH"]')

            if line_counter is not None:
                total_lines = int(line_counter.get("covered", 0)) + int(
                    line_counter.get("missed", 0)
                )
                covered_lines = int(line_counter.get("covered", 0))
            else:
                total_lines = 0
                covered_lines = 0

            if branch_counter is not None:
                total_branches = int(branch_counter.get("covered", 0)) + int(
                    branch_counter.get("missed", 0)
                )
                covered_branches = int(branch_counter.get("covered", 0))
            else:
                total_branches = 0
                covered_branches = 0

            line_coverage = covered_lines / total_lines if total_lines > 0 else 0.0
            branch_coverage = (
                covered_branches / total_branches if total_branches > 0 else 0.0
            )

            logger.info(
                f"全局覆盖率（从 XML）: "
                f"行覆盖率 {line_coverage:.1%} ({covered_lines}/{total_lines}), "
                f"分支覆盖率 {branch_coverage:.1%} ({covered_branches}/{total_branches})"
            )

            return {
                "line_coverage": line_coverage,
                "branch_coverage": branch_coverage,
                "total_lines": total_lines,
                "covered_lines_count": covered_lines,
                "total_branches": total_branches,
                "covered_branches": covered_branches,
            }

        except ET.ParseError as e:
            logger.warning(f"解析 JaCoCo XML 失败: {e}")
            return {
                "line_coverage": 0.0,
                "branch_coverage": 0.0,
                "total_lines": 0,
                "covered_lines_count": 0,
                "total_branches": 0,
                "covered_branches": 0,
            }
        except Exception as e:
            logger.warning(f"解析覆盖率报告时出错: {e}")
            return {
                "line_coverage": 0.0,
                "branch_coverage": 0.0,
                "total_lines": 0,
                "covered_lines_count": 0,
                "total_branches": 0,
                "covered_branches": 0,
            }
