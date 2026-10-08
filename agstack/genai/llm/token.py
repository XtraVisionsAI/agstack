"""Token 计数工具"""

import tiktoken


def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    """计算文本的 Token 数量

    :param text: 文本内容
    :param model: 模型名称
    :return: Token 数量
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    return len(encoding.encode(text))
