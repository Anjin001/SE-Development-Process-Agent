"""Shared v1 contract. All records use JSON-compatible fields."""

class Conflict(ValueError):
    """The current state or revision does not allow this operation."""


class DomainError(ValueError):
    """v2 business-validation failure that maps to a structured HTTP body.

    ``code`` and ``status`` are fixed per subclass. ``details`` carries the
    locator a client needs: a field path, a task id, a closed cycle path, or
    the versions that changed. ``as_body()`` produces the exact shape agreed
    in ``docs/接口契约-v2.md`` §6 and ``examples/v2/scenario.json``.

    Subclassing ``ValueError`` keeps the v1 server mapping (400) working for
    the mock regression until the v2 router reads ``status``.
    """

    code = "INVALID_REFERENCE"
    status = 400
    default_message = "请求无法处理"

    def __init__(self, message=None, **details):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    def as_body(self):
        return {"error": {"code": self.code,
                          "message": self.message,
                          "details": self.details}}


class PlanCycle(DomainError):
    """`depends_on` closes a loop; details.cycle is the closed path."""

    code = "PLAN_CYCLE"
    status = 422
    default_message = "任务依赖成环"


class InvalidReference(DomainError):
    """Unknown id, duplicate id, malformed field, or illegal path."""

    code = "INVALID_REFERENCE"
    status = 422
    default_message = "引用或字段无效"


class InvalidOutput(DomainError):
    """A task has no usable output or no completion condition."""

    code = "INVALID_OUTPUT"
    status = 422
    default_message = "任务输出无效"


class VersionConflict(DomainError):
    """The client saw an older revision or artifact version."""

    code = "VERSION_CONFLICT"
    status = 409
    default_message = "运行或产物版本已更新"


class InvalidState(DomainError):
    """The current run, task or plan state does not allow this operation."""

    code = "INVALID_STATE"
    status = 409
    default_message = "当前状态不允许该操作"


class StaleAttempt(DomainError):
    """A worker reported for an attempt that is no longer current."""

    code = "STALE_ATTEMPT"
    status = 409
    default_message = "当前尝试已更新"


class IdempotencyConflict(DomainError):
    """The same Idempotency-Key arrived with a different request."""

    code = "IDEMPOTENCY_CONFLICT"
    status = 409
    default_message = "同一幂等键提交了不同请求"


class NotFound(DomainError):
    """The referenced project, plan, run or specification does not exist."""

    code = "NOT_FOUND"
    status = 404
    default_message = "引用的资源不存在"
