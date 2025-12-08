"""Maven Surefire 测试报告解析器"""

import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """单个测试用例的结果"""
    class_name: str
    method_name: str
    time: float
    passed: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    failure_type: Optional[str] = None
    failure_message: Optional[str] = None
    skipped: bool = False


@dataclass
class TestSuiteResult:
    """测试套件的结果"""
    name: str
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    skipped_tests: int
    time: float
    test_cases: List[TestResult]

    @property
    def success(self) -> bool:
        """测试套件是否全部通过"""
        return self.failed_tests == 0 and self.error_tests == 0


class SurefireParser:
    """Maven Surefire 测试报告解析器"""

    def __init__(self):
        """初始化解析器"""
        pass

    def parse_surefire_reports(self, reports_dir: str) -> List[TestSuiteResult]:
        """
        解析指定目录下的所有 Surefire 测试报告

        Args:
            reports_dir: Surefire 报告目录路径 (通常是 target/surefire-reports)

        Returns:
            测试套件结果列表
        """
        reports_path = Path(reports_dir)
        if not reports_path.exists() or not reports_path.is_dir():
            logger.debug(f"Surefire 报告目录不存在: {reports_dir}")
            return []

        results = []
        # 查找所有 TEST-*.xml 文件
        xml_files = list(reports_path.glob("TEST-*.xml"))

        if not xml_files:
            logger.warning(f"未找到 Surefire XML 报告文件: {reports_dir}")
            return []

        logger.debug(f"找到 {len(xml_files)} 个 Surefire 报告文件")

        for xml_file in xml_files:
            try:
                suite_result = self.parse_surefire_xml(str(xml_file))
                if suite_result:
                    results.append(suite_result)
            except Exception as e:
                logger.error(f"解析 Surefire 报告失败 {xml_file}: {e}")

        return results

    def parse_surefire_xml(self, xml_path: str) -> Optional[TestSuiteResult]:
        """
        解析单个 Surefire XML 报告文件

        Args:
            xml_path: XML 文件路径

        Returns:
            测试套件结果，解析失败返回 None
        """
        path = Path(xml_path)
        if not path.exists():
            logger.warning(f"Surefire XML 文件不存在: {xml_path}")
            return None

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # 解析 testsuite 元素属性
            suite_name = root.get('name', '')
            total_tests = int(root.get('tests', 0))
            failures = int(root.get('failures', 0))
            errors = int(root.get('errors', 0))
            skipped = int(root.get('skipped', 0))
            time = float(root.get('time', 0.0))

            passed = total_tests - failures - errors - skipped

            # 解析每个测试用例
            test_cases = []
            for testcase in root.findall('testcase'):
                test_result = self._parse_testcase(testcase)
                if test_result:
                    test_cases.append(test_result)

            suite_result = TestSuiteResult(
                name=suite_name,
                total_tests=total_tests,
                passed_tests=passed,
                failed_tests=failures,
                error_tests=errors,
                skipped_tests=skipped,
                time=time,
                test_cases=test_cases
            )

            logger.debug(
                f"解析测试套件: {suite_name} - "
                f"总计: {total_tests}, 通过: {passed}, "
                f"失败: {failures}, 错误: {errors}, 跳过: {skipped}"
            )

            return suite_result

        except ET.ParseError as e:
            logger.error(f"解析 Surefire XML 失败: {e}")
            return None
        except Exception as e:
            logger.error(f"解析 Surefire 报告时出错: {e}")
            return None

    def _parse_testcase(self, testcase_elem: ET.Element) -> Optional[TestResult]:
        """
        解析单个测试用例元素

        Args:
            testcase_elem: testcase XML 元素

        Returns:
            测试结果对象
        """
        try:
            class_name = testcase_elem.get('classname', '')
            method_name = testcase_elem.get('name', '')
            time = float(testcase_elem.get('time', 0.0))

            # 检查是否有失败、错误或跳过
            failure = testcase_elem.find('failure')
            error = testcase_elem.find('error')
            skipped_elem = testcase_elem.find('skipped')

            passed = failure is None and error is None and skipped_elem is None
            is_skipped = skipped_elem is not None

            error_type = None
            error_message = None
            failure_type = None
            failure_message = None

            if error is not None:
                error_type = error.get('type', '')
                error_message = error.get('message', '')
                if not error_message:
                    error_message = error.text or ''

            if failure is not None:
                failure_type = failure.get('type', '')
                failure_message = failure.get('message', '')
                if not failure_message:
                    failure_message = failure.text or ''

            return TestResult(
                class_name=class_name,
                method_name=method_name,
                time=time,
                passed=passed,
                error_type=error_type,
                error_message=error_message,
                failure_type=failure_type,
                failure_message=failure_message,
                skipped=is_skipped
            )

        except Exception as e:
            logger.error(f"解析测试用例失败: {e}")
            return None

    def get_failed_test_names(self, reports_dir: str) -> Set[str]:
        """
        获取所有失败的测试方法名称集合

        Args:
            reports_dir: Surefire 报告目录路径

        Returns:
            失败测试的全限定名称集合 (格式: class_name.method_name)
        """
        results = self.parse_surefire_reports(reports_dir)
        failed_tests = set()

        for suite in results:
            for test_case in suite.test_cases:
                if not test_case.passed and not test_case.skipped:
                    # 构建全限定名称
                    full_name = f"{test_case.class_name}.{test_case.method_name}"
                    failed_tests.add(full_name)

        return failed_tests

    def get_test_summary(self, reports_dir: str) -> Dict[str, int]:
        """
        获取测试统计摘要

        Args:
            reports_dir: Surefire 报告目录路径

        Returns:
            统计信息字典
        """
        results = self.parse_surefire_reports(reports_dir)

        summary = {
            'total_suites': len(results),
            'total_tests': 0,
            'passed_tests': 0,
            'failed_tests': 0,
            'error_tests': 0,
            'skipped_tests': 0,
        }

        for suite in results:
            summary['total_tests'] += suite.total_tests
            summary['passed_tests'] += suite.passed_tests
            summary['failed_tests'] += suite.failed_tests
            summary['error_tests'] += suite.error_tests
            summary['skipped_tests'] += suite.skipped_tests

        return summary
