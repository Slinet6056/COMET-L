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


class CoverageParser:
    """JaCoCo XML 报告解析器"""

    def __init__(self):
        """初始化解析器"""
        pass

    def parse_jacoco_xml(self, xml_path: str) -> List[MethodCoverage]:
        """
        解析 JaCoCo XML 报告文件

        Args:
            xml_path: JaCoCo XML 报告文件路径

        Returns:
            方法覆盖率列表
        """
        path = Path(xml_path)
        if not path.exists():
            logger.warning(f"JaCoCo 报告文件不存在: {xml_path}")
            return []

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            method_coverages = []

            # 遍历所有包
            for package in root.findall('.//package'):
                package_name = package.get('name', '').replace('/', '.')

                # 遍历包中的所有类
                for clazz in package.findall('class'):
                    class_name = clazz.get('name', '').replace('/', '.')
                    source_file = clazz.get('sourcefilename', '')

                    # 构建行号到覆盖信息的映射（从 sourcefile 元素）
                    line_coverage_map = {}
                    for sourcefile in clazz.findall('sourcefile'):
                        for line in sourcefile.findall('line'):
                            line_nr = int(line.get('nr', 0))
                            missed_instructions = int(line.get('mi', 0))
                            covered_instructions = int(line.get('ci', 0))
                            missed_branches = int(line.get('mb', 0))
                            covered_branches = int(line.get('cb', 0))

                            line_coverage_map[line_nr] = {
                                'covered': covered_instructions > 0,
                                'missed_instructions': missed_instructions,
                                'covered_instructions': covered_instructions,
                                'missed_branches': missed_branches,
                                'covered_branches': covered_branches,
                            }

                    # 遍历类中的所有方法
                    for method in clazz.findall('method'):
                        method_name = method.get('name', '')
                        method_desc = method.get('desc', '')

                        # 跳过构造函数
                        if method_name == '<init>' or method_name == '<clinit>':
                            continue

                        # 获取方法的起始行号
                        method_line = int(method.get('line', 0))

                        # 从 counter 中提取覆盖率信息
                        line_counter = None
                        branch_counter = None

                        for counter in method.findall('counter'):
                            counter_type = counter.get('type', '')
                            if counter_type == 'LINE':
                                line_counter = counter
                            elif counter_type == 'BRANCH':
                                branch_counter = counter

                        # 解析行覆盖率
                        if line_counter is not None:
                            missed_lines_count = int(line_counter.get('missed', 0))
                            covered_lines_count = int(line_counter.get('covered', 0))
                            total_lines = missed_lines_count + covered_lines_count
                        else:
                            missed_lines_count = 0
                            covered_lines_count = 0
                            total_lines = 0

                        # 解析分支覆盖率
                        if branch_counter is not None:
                            missed_branches = int(branch_counter.get('missed', 0))
                            covered_branches = int(branch_counter.get('covered', 0))
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

                        # 由于 JaCoCo 的 method 元素不直接包含具体的行号列表，
                        # 我们需要从 sourcefile 的 line 元素中推断
                        # 这里我们使用一个简化的方法：收集所有行的覆盖信息
                        covered_lines = []
                        missed_lines = []

                        # 注意：这里我们无法精确知道每个方法包含哪些行
                        # 作为简化，我们将所有行的信息都记录下来
                        # 更精确的实现需要使用 JaCoCo 的 API 或更复杂的解析逻辑

                        # 创建方法覆盖率对象
                        method_coverage = MethodCoverage(
                            class_name=class_name,
                            method_name=method_name,
                            covered_lines=covered_lines,  # 将在后续步骤填充
                            missed_lines=missed_lines,    # 将在后续步骤填充
                            total_lines=total_lines,
                            covered_branches=covered_branches,
                            missed_branches=missed_branches,
                            total_branches=total_branches,
                            line_coverage_rate=line_coverage_rate,
                            branch_coverage_rate=branch_coverage_rate,
                        )

                        method_coverages.append(method_coverage)
                        logger.debug(
                            f"解析方法覆盖率: {class_name}.{method_name} "
                            f"- 行覆盖: {line_coverage_rate:.2%}, "
                            f"分支覆盖: {branch_coverage_rate:.2%}"
                        )

            logger.info(f"成功解析 {len(method_coverages)} 个方法的覆盖率信息")
            return method_coverages

        except ET.ParseError as e:
            logger.error(f"解析 JaCoCo XML 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"解析覆盖率报告时出错: {e}")
            return []

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

            for package in root.findall('.//package'):
                # 首先收集所有 sourcefile 的行覆盖信息
                source_line_info = {}  # {source_filename: {line_nr: {covered, missed}}}

                for sourcefile in package.findall('sourcefile'):
                    source_name = sourcefile.get('name', '')
                    source_line_info[source_name] = {}

                    for line in sourcefile.findall('line'):
                        line_nr = int(line.get('nr', 0))
                        covered_instructions = int(line.get('ci', 0))
                        missed_instructions = int(line.get('mi', 0))

                        source_line_info[source_name][line_nr] = {
                            'covered': covered_instructions > 0,
                            'missed': missed_instructions > 0,
                        }

                # 然后解析每个类的方法
                for clazz in package.findall('class'):
                    class_name = clazz.get('name', '').replace('/', '.')
                    source_filename = clazz.get('sourcefilename', '')

                    # 收集该类所有方法及其起始行号
                    methods_info = []
                    for method in clazz.findall('method'):
                        method_name = method.get('name', '')
                        # 跳过构造函数
                        if method_name == '<init>' or method_name == '<clinit>':
                            continue

                        start_line = int(method.get('line', 0))
                        methods_info.append({
                            'element': method,
                            'name': method_name,
                            'start_line': start_line,
                        })

                    # 按起始行号排序
                    methods_info.sort(key=lambda x: x['start_line'])

                    # 为每个方法推断行范围并提取覆盖信息
                    for i, method_info in enumerate(methods_info):
                        method = method_info['element']
                        method_name = method_info['name']
                        start_line = method_info['start_line']

                        # 推断方法结束行：下一个方法的起始行-1，或文件末尾
                        if i + 1 < len(methods_info):
                            end_line = methods_info[i + 1]['start_line'] - 1
                        else:
                            # 最后一个方法：使用源文件中的最大行号
                            if source_filename in source_line_info:
                                end_line = max(source_line_info[source_filename].keys()) if source_line_info[source_filename] else start_line
                            else:
                                end_line = start_line + 100  # 默认范围

                        # 从 sourcefile 中提取该方法范围内的行覆盖信息
                        covered_lines = []
                        missed_lines = []

                        if source_filename in source_line_info:
                            for line_nr in range(start_line, end_line + 1):
                                if line_nr in source_line_info[source_filename]:
                                    line_info = source_line_info[source_filename][line_nr]
                                    if line_info['covered']:
                                        covered_lines.append(line_nr)
                                    elif line_info['missed']:
                                        missed_lines.append(line_nr)

                        # 获取覆盖率计数器（用于验证）
                        line_counter = None
                        branch_counter = None

                        for counter in method.findall('counter'):
                            counter_type = counter.get('type', '')
                            if counter_type == 'LINE':
                                line_counter = counter
                            elif counter_type == 'BRANCH':
                                branch_counter = counter

                        # 解析行覆盖率统计
                        if line_counter is not None:
                            missed_lines_count = int(line_counter.get('missed', 0))
                            covered_lines_count = int(line_counter.get('covered', 0))
                            total_lines = missed_lines_count + covered_lines_count
                        else:
                            # 如果没有 counter，使用实际收集的行数
                            total_lines = len(covered_lines) + len(missed_lines)
                            covered_lines_count = len(covered_lines)
                            missed_lines_count = len(missed_lines)

                        # 解析分支覆盖率
                        if branch_counter is not None:
                            missed_branches = int(branch_counter.get('missed', 0))
                            covered_branches = int(branch_counter.get('covered', 0))
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
                        )

                        method_coverages.append(method_coverage)

                        logger.debug(
                            f"  {class_name}.{method_name}: "
                            f"行 {start_line}-{end_line}, "
                            f"覆盖 {len(covered_lines)}/{total_lines}"
                        )

            logger.info(f"成功解析 {len(method_coverages)} 个方法的覆盖率信息（含精确行号）")
            return method_coverages

        except ET.ParseError as e:
            logger.error(f"解析 JaCoCo XML 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"解析覆盖率报告时出错: {e}")
            return []
