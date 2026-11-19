"""模型路由。

两档模型：低成本用于简单模块和文档，高能力用于冲突复核、方案取舍、
跨模块设计和故障定位。复杂任务先由高能力模型定边界，再交低成本模型实现。
档位、模型名和费用上限留在执行端，WorkerResult 只回报用了哪一档的供应商与模型。
"""
import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).with_name("model_config.json")


def load_config(path=None):
    """读取档位配置。可用 SE_AGENT_MODEL_CONFIG 指向自定义文件。

    相对路径按项目根目录解析，这样 .env 里写 se_agent/model_config.bailian.json
    在任意工作目录启动都能找到。
    """
    target = Path(path or os.environ.get("SE_AGENT_MODEL_CONFIG") or DEFAULT_CONFIG_PATH)
    if not target.is_absolute():
        target = Path(__file__).resolve().parent.parent / target
    with target.open(encoding="utf-8") as handle:
        config = json.load(handle)
    for tier in ("low_cost", "high_capability"):
        if tier not in config.get("tiers", {}):
            raise ValueError(f"模型配置缺少档位：{tier}")
    return config


class ModelRouter:
    """选择档位，并把复杂任务拆成先定边界、后实现的两步。"""

    def __init__(self, config):
        self.config = config

    @property
    def routing(self):
        return self.config["routing"]

    @property
    def max_cost_usd(self):
        return float(self.routing.get("max_cost_usd_per_attempt") or 0)

    def tier_for(self, task, spec_context=None):
        explicit = task.get("model_tier")
        if explicit in self.config["tiers"]:
            return explicit
        role = task.get("role")
        return (self.routing.get("role_tiers", {}).get(role)
                or self.routing.get("default_tier", "low_cost"))

    def plan(self, task, spec_context=None):
        """返回按顺序执行的步骤，每步含 stage/tier/provider/model。"""
        if task.get("role") in self.routing.get("two_stage_roles", []):
            return [self._step("boundary", "high_capability"),
                    self._step("implement", "low_cost")]
        return [self._step("implement", self.tier_for(task, spec_context))]

    def cost_usd(self, model, usage):
        """按配置单价估算一次调用的费用，缺单价时按 0 计。"""
        price = self.config.get("pricing", {}).get(model)
        if not price or not usage:
            return 0.0
        return (usage["input_tokens"] * price["input_usd_per_1k"]
                + usage["output_tokens"] * price["output_usd_per_1k"]) / 1000

    def describe(self):
        return {"tiers": sorted(self.config["tiers"]),
                "role_tiers": dict(self.routing.get("role_tiers", {})),
                "two_stage_roles": list(self.routing.get("two_stage_roles", [])),
                "max_cost_usd_per_attempt": self.max_cost_usd}

    def _step(self, stage, tier):
        spec = self.config["tiers"][tier]
        return {"stage": stage, "tier": tier,
                "provider": spec.get("provider"),
                "model": spec.get("model"),
                "base_url": spec.get("base_url"),
                "api_key_env": spec.get("api_key_env"),
                "max_output_tokens": spec.get("max_output_tokens")}
