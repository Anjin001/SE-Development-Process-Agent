"""Global input and model request limits."""

MAX_INPUT_BYTES = 1_000_000
MODEL_TIMEOUT_SECONDS = 600
# JSON escaping can expand an input byte to six bytes.
MAX_JSON_BODY_BYTES = 6 * MAX_INPUT_BYTES + 100_000
WORKER_LEASE_SECONDS = MODEL_TIMEOUT_SECONDS + 60


def public_limits():
    return dict(max_input_bytes=MAX_INPUT_BYTES,
                model_timeout_seconds=MODEL_TIMEOUT_SECONDS)
