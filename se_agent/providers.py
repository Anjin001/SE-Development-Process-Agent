"""模型供应商适配。

凭证、模型名和本地路径留在执行端，不进入 WorkerResult。
真实链路走 OpenAI 兼容接口；离线场景由 Worker 的模拟链路完成，不经过这里。
"""
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """供应商调用失败。Worker 转成 EXECUTION_FAILED。"""


class ProviderOutputError(ProviderError):
    """A completed request had missing or truncated file content."""

    def __init__(self, message, finish_reason, max_tokens, usage):
        super().__init__(message)
        self.finish_reason = finish_reason if finish_reason in ("length","stop","content_filter","tool_calls") else None
        self.max_tokens = max_tokens
        self.usage = usage


class ProviderTimeout(ProviderError):
    """调用超时。Worker 转成 WORKER_TIMEOUT。"""


@dataclass
class LLMResult:
    """一次模型调用的结果与用量。"""

    text: str
    input_tokens: int
    output_tokens: int

    @property
    def usage(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


def estimate_tokens(text):
    """粗略估算 token 数，中英混排按 4 个字符 1 个 token。"""
    return max(1, len(text) // 4)


class Provider:
    name = "base"

    def complete(self, model, prompt, system=None, max_output_tokens=None):
        raise NotImplementedError


class OpenAICompatibleProvider(Provider):
    """/chat/completions 接口，兼容 OpenAI 与自建网关。"""

    name = "openai_compatible"

    def __init__(self, base_url=None, api_key_env="SE_AGENT_API_KEY", timeout=120, *, api_key=None):
        self.base_url = (base_url or os.environ.get("SE_AGENT_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self._api_key = api_key

    @property
    def api_key(self):
        return self._api_key if self._api_key is not None else os.environ.get(self.api_key_env, "")

    def direct_deepseek(self, model):
        endpoint = urlsplit(self.base_url)
        return (endpoint.scheme == "https" and endpoint.hostname == "api.deepseek.com"
                and model in ("deepseek-flash","deepseek-v4-pro"))

    def complete(self, model, prompt, system=None, max_output_tokens=None, *, file_output=False):
        if self._api_key is None and not self.api_key:
            raise ProviderError(f"缺少凭证：请设置环境变量 {self.api_key_env}")
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        body = {"model": model, "messages": messages}
        if file_output and self.direct_deepseek(model):
            # Use the file's completion budget for content, not reasoning.
            body["thinking"] = {"type":"disabled"}
        if max_output_tokens:
            body["max_tokens"] = max_output_tokens
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        if self.api_key:
            request.add_unredirected_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise ProviderTimeout(f"{model} 调用超时") from exc
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise ProviderError(f"{model} 的执行接口返回 HTTP {status}") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout(f"{model} 调用超时") from exc
            raise ProviderError(f"{model} 调用失败：{exc.reason}") from exc
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            finish = choice.get("finish_reason")
            text = message.get("content")
            usage = payload.get("usage") or {}
            if not isinstance(usage,dict):
                usage = {}
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError(f"{model} 的接口未返回可识别的正文") from None
        has_text = isinstance(text,str) and bool(text.strip())
        counts = {local: usage[remote] for local,remote in
                  (("input_tokens","prompt_tokens"),("output_tokens","completion_tokens"))
                  if type(usage.get(remote)) is int and usage[remote]>=0}
        if not has_text or finish == "length":
            if finish == "length":
                detail = "达到生成上限，正文未返回或不完整"
                if "output_tokens" in counts and max_output_tokens:
                    detail += f"（本次生成 {counts['output_tokens']} / 上限 {max_output_tokens} token）"
                elif max_output_tokens:
                    detail += f"（请求上限 {max_output_tokens} token）"
            else:
                detail = "没有返回可写入文件的正文"
            raise ProviderOutputError(f"{model} {detail}",finish,max_output_tokens,counts)
        return LLMResult(text, counts.get("input_tokens",estimate_tokens(prompt)),
                         counts.get("output_tokens",estimate_tokens(text)))


def build_provider(spec):
    """按档位配置组装供应商。"""
    spec = spec or {}
    if spec.get("provider", OpenAICompatibleProvider.name) != OpenAICompatibleProvider.name:
        raise ProviderError(f"未接入的供应商：{spec.get('provider')}")
    return OpenAICompatibleProvider(spec.get("base_url"),
                                    spec.get("api_key_env", "SE_AGENT_API_KEY"))
