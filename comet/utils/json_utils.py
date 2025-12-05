"""JSON处理工具函数"""


def extract_json_from_response(response: str) -> str:
    """
    从响应中提取JSON内容，兼容代码块包裹的情况

    Args:
        response: LLM响应字符串，可能包含代码块标记

    Returns:
        清理后的JSON字符串
    """
    if not response:
        return response

    response = response.strip()

    # 检查是否以代码块标记开头
    if response.startswith("```"):
        # 找到第一个换行符的位置
        first_newline = response.find("\n")
        if first_newline != -1:
            # 去除开头的代码块标记（如 ```json 或 ```）
            response = response[first_newline + 1:]
        else:
            # 如果没有换行符，直接去除开头的 ```
            response = response[3:]

    # 检查是否以代码块标记结尾
    if response.rstrip().endswith("```"):
        # 找到最后一个换行符的位置
        last_newline = response.rfind("\n")
        if last_newline != -1:
            # 去除结尾的代码块标记
            response = response[:last_newline]
        else:
            # 如果没有换行符，直接去除结尾的 ```
            response = response[:-3]

    return response.strip()
