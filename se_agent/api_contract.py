"""Build the team's compact, machine-readable API contract from independent slices."""
import json
import re


CONTRACT_PATH = "docs/api-contract.json"
_ROUTE = re.compile(r"/(?:[A-Za-z0-9_-]+|\{[A-Za-z_][A-Za-z0-9_]*\})(?:/(?:[A-Za-z0-9_-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))*")
_HANDLER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def service_units(tasks):
    units = []
    for task in tasks:
        for output in task["outputs"]:
            match = re.fullmatch(r"app/([a-z][a-z0-9_]{0,30})/routes\.py", output["path"] or "")
            if match and task["phase"]=="development":
                units.append(dict(name=match[1], title=task["title"], scope=task["completion_condition"],
                                  path=output["path"], refs=task["requirement_ids"]))
                break
    return units


def parse_endpoints(text, occupied, *, prefix=None):
    obj = json.loads(text)
    raw = obj.get("endpoints") if isinstance(obj,dict) else None
    if not isinstance(raw,list) or not 1 <= len(raw) <= 8:
        raise ValueError("每个服务需要 1 到 8 个接口")
    found, handlers, entries = set(), set(), []
    for item in raw:
        if not isinstance(item,dict):
            raise ValueError("接口条目格式无效")
        method, path, handler = (item.get(k) for k in ("method","path","handler"))
        if (method not in ("GET","POST","PUT","PATCH","DELETE") or not isinstance(path,str) or len(path)>180
                or not _ROUTE.fullmatch(path) or not isinstance(handler,str) or not _HANDLER.fullmatch(handler)):
            raise ValueError("HTTP 方法、路由或函数名无效")
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            raise ValueError(f"{method} {path} 不属于本组路由前缀 {prefix}")
        # /notes/{id} and /notes/{note_id} occupy the same route.
        key = method,re.sub(r"\{[^}]+\}","{}",path)
        if key in occupied or key in found:
            raise ValueError(f"路由重复：{method} {path}")
        if handler in handlers:
            raise ValueError(f"本组处理函数名重复：{handler}")
        entry = dict(method=method,path=path,handler=handler)
        for field in ("request","response"):
            values = item.get(field,[])
            if (not isinstance(values,list) or len(values)>12
                    or any(not isinstance(v,str) or not _HANDLER.fullmatch(v) for v in values)):
                raise ValueError("请求和响应只列字段名，不展开完整模式")
            entry[field] = list(dict.fromkeys(values))
        entries.append(entry)
        found.add(key); handlers.add(handler)
    return entries, found


def view_for_file(content, path):
    """Keep just this service's interface in leaf tasks; clients and integration need all."""
    match = re.fullmatch(r"app/([a-z][a-z0-9_]{0,30})/(?:routes|service)\.py|tests/test_([a-z][a-z0-9_]{0,30})\.py",path)
    if not match:
        return content
    try:
        value = json.loads(content)
        name = match[1] or match[2]
        unit = value["modules"][name]
    except (KeyError,TypeError,ValueError):
        return content
    return json.dumps(dict(format=value["format"],modules={name:unit}),ensure_ascii=False,separators=(",",":"))
