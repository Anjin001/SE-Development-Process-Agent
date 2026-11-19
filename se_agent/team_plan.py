"""Turn a confirmed requirement into a handoff, parallel work packages and delivery."""
import re

from .plan_rules import _find_cycle


FEATURES = (
    ("devices", "设备查询", r"设备查询|查询设备|设备列表", "实现设备查询与详情，提供设备可用状态的读取接口。"),
    ("reservations", "预约与取消", r"预约|预订", "实现创建、查询和取消预约，封装时间冲突和预约状态规则。"),
    ("device_disable", "停用设备与预约处置", r"停用设备|设备停用|受影响的预约",
     "查询受影响预约，确认后将设备状态更新为不可用并通过预约服务取消预约，保存可供预约记录展示的原因。"),
    ("notes", "图文笔记发布", r"发布|发帖|图文|图片上传|笔记编辑|创建笔记", "处理草稿、图文校验、发布和笔记详情。"),
    ("feed", "浏览与信息流", r"浏览|信息流|关注流|查看.*更新|分页|搜索", "处理列表、可见性和分页。"),
    ("follows", "关注关系", r"关注|粉丝|取关", "实现关注、取消关注与作者关系查询。"),
    ("reactions", "互动与收藏", r"点赞|收藏|评论|互动|赞同", "实现互动与收藏的写入、撤销和查询。"),
    ("accounts", "账号与访问控制", r"账号|登录|注册|身份认证", "实现用户身份和访问条件。"),
)

RESERVED = {"shared","main","init","index"}


def fallback_modules(requirements):
    """Split recognisable behaviours even when the input is a single sentence."""
    modules = []
    for slug, title, pattern, scope in FEATURES:
        refs = [r["id"] for r in requirements if re.search(pattern,r["text"])]
        if refs:
            modules.append(dict(slug=slug,title=title,scope=scope,requirement_ids=refs,kind="service"))
    covered = {ref for m in modules for ref in m["requirement_ids"]}
    by_slug = {module["slug"]: module for module in modules}
    if "device_disable" in by_slug and "reservations" in by_slug:
        by_slug["device_disable"]["inputs_from"] = {"reservations": "预约状态查询和取消接口"}
    for index,item in enumerate(requirements,1):
        if item["id"] not in covered:
            modules.append(dict(slug=f"feature_{index}",title=item["title"],scope=item["text"],
                                requirement_ids=[item["id"]],kind="service"))
    app_refs = [r["id"] for r in requirements if re.search("应用|页面|工作台|网站|界面|网页",r["text"])]
    if app_refs and modules:
        modules.append(dict(slug="client",title="用户界面与业务串联",kind="web",requirement_ids=app_refs,
                            scope="按共同接口实现界面交互，不复制服务端规则。"))
    return modules


def validate_modules(value, requirements, minimum):
    known = {r["id"] for r in requirements}
    if not isinstance(value,list) or not minimum <= len(value) <= 12:
        raise ValueError("模块数量不符合拆分目标")
    modules, slugs, covered = [], set(), set()
    for module in value:
        if not isinstance(module,dict):
            raise ValueError("模块格式无效")
        slug,title,scope = (module.get(k) for k in ("slug","title","scope"))
        refs,kind = module.get("requirement_ids"),module.get("kind","service")
        if (not isinstance(slug,str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,30}",slug) or slug in slugs|RESERVED
                or kind not in ("service","web") or not isinstance(title,str) or not title.strip()
                or not isinstance(scope,str) or not scope.strip() or not isinstance(refs,list) or not refs
                or any(not isinstance(r,str) or r not in known for r in refs)):
            raise ValueError("模块职责、路径或需求引用无效")
        slugs.add(slug)
        covered.update(refs)
        minutes = module.get("minutes", 15)
        if type(minutes) is not int or not 1 <= minutes <= 120:
            minutes = 15
        inputs = module.get("inputs_from", {})
        if (not isinstance(inputs, dict) or any(
                not isinstance(key, str) or key == slug or not isinstance(reason, str) or not reason.strip()
                for key, reason in inputs.items())):
            raise ValueError(f"{slug} 的前置产物引用无效")
        modules.append(dict(slug=slug,title=title.strip()[:120],scope=scope.strip()[:650],
                            requirement_ids=list(dict.fromkeys(refs)),kind=kind,minutes=minutes,
                            inputs_from={key: reason.strip()[:250] for key, reason in inputs.items()}))
    if covered != known or not any(m["kind"]=="service" for m in modules):
        raise ValueError("模块未完整覆盖需求或缺少服务端工作包")
    for module in modules:
        missing = set(module["inputs_from"]) - slugs
        if missing:
            raise ValueError(f"{module['slug']} 引用了不存在的模块：{', '.join(sorted(missing))}")
    edges = [{"id": module["slug"], "depends_on": list(module["inputs_from"])} for module in modules]
    cycle = _find_cycle(edges, [edge["id"] for edge in edges])
    if cycle:
        raise ValueError("模块依赖成环：" + " → ".join(cycle))
    return modules


def team_tasks(review, modules):
    spec = review["specification"]
    requirements = [item["id"] for item in review["requirement"]["items"]]
    def task(id_,title,phase,role,refs,depends,paths,estimate,completion,review_required):
        outputs = [dict(id=f"OUT-{i:02}",kind=kind,path=path) for i,(path,kind) in enumerate(paths,1)]
        return dict(id=id_,title=title,phase=phase,role=role,requirement_ids=refs,depends_on=depends,
                    decision_ids=[d["id"] for d in spec["decisions"] if set(d["requirement_ids"])&set(refs)],
                    open_item_ids=[o["id"] for o in spec["open_items"] if o["blocking"]
                                   and not o["resolution"] and set(o["affected_requirement_ids"])&set(refs)]
                                   if phase == "development" else [],
                    write_paths=[o["path"] for o in outputs],outputs=outputs,estimate_seconds=estimate,
                    completion_condition=completion,review_required=review_required)
    def git(*paths):
        return [(path,"git_commit") for path in paths]

    owner_notes = "；".join(f"{m['slug']}：{m['scope']}" for m in modules)
    tasks = [task("T1","技术交接：定接口、数据结构和分支边界","planning","plan",requirements,[],
        git("docs/technical-handoff.md","docs/api-contract.json","app/shared.py","db/schema.sql"),480,
        "确认各模块路径、公开函数和 HTTP 接口、请求响应、数据结构与依赖。共享辅助代码和 SQL 与交接约定一致。"
        "列出每条用户操作对应的前端入口、服务端路由及负责人，特别检查登录和数据来源。"
        "只约定边界，不包办业务实现。模块职责："+owner_notes,True)]
    task_ids = {module["slug"]: f"T{index}" for index, module in enumerate(modules, 2)}
    for module in modules:
        slug = module["slug"]
        inputs = module.get("inputs_from", {})
        if module["kind"]=="web":
            files = git(f"web/{slug}/index.html",f"web/{slug}/view.js",f"web/{slug}/view.css",
                        f"tests/{slug}.test.mjs")
            practice = ("使用交接文档给出的端点；独占模块目录。导出可独立测试的逻辑，"
                        "用 node:test 覆盖正常及异常分支。")
        else:
            files = git(f"app/{slug}/service.py",f"app/{slug}/routes.py",f"tests/test_{slug}.py")
            practice = ("只实现接口契约中归本模块所有的 HTTP 方法与完整路径。保留已确认的业务路径；"
                        "服务逻辑与路由分文件，使用 unittest 编写覆盖正常与失败场景的模块测试。")
        upstream = "；".join(f"{task_ids[key]} 提供 {wanted}" for key, wanted in inputs.items())
        completion = module["scope"]+"。遵循共同接口，不改其他模块文件。"+practice
        if upstream:
            completion += "直接前置产物："+upstream
        tasks.append(task(task_ids[slug],module["title"],"development","develop",module["requirement_ids"],
            ["T1", *(task_ids[key] for key in inputs)],files,module.get("minutes",15)*60,completion,True))
    tasks.append(task(f"T{len(tasks)+1}","合并功能分支并组装交付入口","integration","integrate",
        requirements,[t["id"] for t in tasks],
        git("app/main.py","web/index.html"),720,
        "组装启动入口，按实际文件导入并注册每个模块。逐项对照前端调用与已注册的后端路由，"
        "包括 HTTP 方法和路径。发现缺失接口或鉴权、字段不一致时列出对应文件和负责模块。",True))
    integration_id = tasks[-1]["id"]
    tasks.append(task(f"T{len(tasks)+1}","编写端到端验收与回归用例","verification","review",
        requirements,[integration_id],git("tests/test_integration.py")+[("docs/verification.md","text")],600,
        "以实际集成入口编写可运行的端到端用例，逐条覆盖规格的主要用户操作、权限和失败场景。"
        "记录需求编号、操作、预期和受影响文件。明确哪些检查实际运行过，未执行的用例标为待实测。",True))
    tasks.append(task(f"T{len(tasks)+1}","整理启动说明与交付记录","documentation","docs",
        requirements,[tasks[-1]["id"]],git("README.md")+[("docs/delivery.md","text")],300,
        "以实际代码和验收记录编写安装、启动、测试命令，列出尚待验证或修复的功能。"
        "交付清单引用实际提交；禁止声称未执行的测试已经通过。",True))
    return tasks


def repair_tasks(tasks, proposal):
    """Apply review corrections without replacing task identity or output ownership."""
    changes = proposal.get("changes") if isinstance(proposal, dict) else None
    if not isinstance(changes, list) or not changes:
        raise ValueError("修复结果必须列出要修改的任务")
    by_id = {task["id"]: dict(task) for task in tasks}
    seen = set()
    for change in changes:
        id_ = change.get("id") if isinstance(change, dict) else None
        if not isinstance(id_, str) or id_ not in by_id or id_ in seen:
            raise ValueError("修复任务编号不存在或重复")
        seen.add(id_)
        task = by_id[id_]
        condition = change.get("completion_condition", task["completion_condition"])
        deps = change.get("depends_on", task["depends_on"])
        if not isinstance(condition, str) or not condition.strip() or len(condition) > 4000:
            raise ValueError(f"{id_} 的完成条件无效")
        if (not isinstance(deps, list) or any(not isinstance(dep, str) or dep not in by_id or dep == id_
                                             for dep in deps) or len(deps) != len(set(deps))):
            raise ValueError(f"{id_} 的依赖无效")
        if task["phase"] in ("planning", "integration", "verification", "documentation") and deps != task["depends_on"]:
            raise ValueError(f"{id_} 的阶段门禁依赖不可移除或替换")
        if task["phase"] == "development" and (
                "T1" not in deps or any(by_id[dep]["phase"] not in ("planning", "development") for dep in deps)):
            raise ValueError(f"{id_} 必须保留技术交接，只能依赖交接或开发任务")
        task.update(completion_condition=condition.strip(), depends_on=deps)
    revised = list(by_id.values())
    cycle = _find_cycle(revised, list(by_id))
    if cycle:
        raise ValueError("修复后依赖成环：" + " → ".join(cycle))
    if revised == tasks:
        raise ValueError("修复没有改变任务")
    return revised
