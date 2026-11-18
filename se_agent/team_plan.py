"""Turn a confirmed requirement into a handoff, parallel work packages and delivery."""
import re


FEATURES = (
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
        modules.append(dict(slug=slug,title=title.strip()[:120],scope=scope.strip()[:650],
                            requirement_ids=list(dict.fromkeys(refs)),kind=kind,minutes=minutes))
    if covered != known or not any(m["kind"]=="service" for m in modules):
        raise ValueError("模块未完整覆盖需求或缺少服务端工作包")
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
        "只约定边界，不包办业务实现。模块职责："+owner_notes,False)]
    for index, module in enumerate(modules,2):
        slug = module["slug"]
        if module["kind"]=="web":
            files = git(f"web/{slug}/index.html",f"web/{slug}/view.js",f"web/{slug}/view.css")
            practice = "使用交接文档给出的端点；独占模块目录。"
        else:
            files = git(f"app/{slug}/service.py",f"app/{slug}/routes.py",f"tests/test_{slug}.py")
            practice = "服务逻辑与路由分文件，编写覆盖正常与失败场景的模块测试。"
        tasks.append(task(f"T{index}",module["title"],"development","develop",module["requirement_ids"],
            ["T1"],files,module.get("minutes",15)*60,module["scope"]+"。遵循共同接口，不改其他模块文件。"+practice,True))
    tasks.append(task(f"T{len(tasks)+1}","合并功能分支并组装交付入口","integration","integrate",
        requirements,[t["id"] for t in tasks],
        git("app/main.py","web/index.html","README.md")+[("docs/delivery.md","text")],720,
        "在已合并的功能提交上组装可启动入口，按实际文件导入模块并注册路由，连接页面；"
        "给出实际的安装、启动和测试命令。区分已检查的证据与尚未运行的测试，不重写业务模块。",True))
    return tasks
