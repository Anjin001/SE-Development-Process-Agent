"""Requirement intake and isolated proposal generation."""

def intake(markdown):
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("请输入需求文档")
    text = markdown.strip()
    return {"id": "REQ-01", "version": 1, "markdown": text}


def race(requirement):
    return [
        {"id": "A", "source": requirement["id"], "title": "先定义统一规则，再并行实现", "summary": "先固定接口和业务边界，再并行开发功能，最后集成。"},
        {"id": "B", "source": requirement["id"], "title": "先交付主链路，再补齐边界", "summary": "先实现最短端到端链路，再处理异常和交付说明。"},
    ]
