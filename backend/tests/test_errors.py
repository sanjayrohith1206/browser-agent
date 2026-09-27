from __future__ import annotations

from app.llm.errors import error_status, is_quota_exhausted, is_transient

DAILY = (
    "429 RESOURCE_EXHAUSTED. You exceeded your current quota, please check your plan and "
    "billing details. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
)
PER_MINUTE = (
    "429 RESOURCE_EXHAUSTED. You exceeded your current quota. "
    "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
)


class ApiError(Exception):
    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


class Wrapper(Exception):
    pass


def wrapped(inner: Exception) -> Exception:
    try:
        raise Wrapper("Error calling model") from inner
    except Wrapper as exc:
        return exc


def test_daily_quota_is_permanent() -> None:
    exc = wrapped(ApiError(DAILY, 429))
    assert error_status(exc) == 429
    assert is_quota_exhausted(exc)
    assert not is_transient(exc)


def test_per_minute_quota_is_transient() -> None:
    exc = wrapped(ApiError(PER_MINUTE, 429))
    assert not is_quota_exhausted(exc)
    assert is_transient(exc)


def test_plain_rate_limits_and_overloads_are_transient() -> None:
    assert is_transient(ApiError("Too many requests", 429))
    assert is_transient(ApiError("high demand", 503))
    assert not is_transient(ApiError("bad request", 400))


def test_other_providers_out_of_credit() -> None:
    assert is_quota_exhausted(ApiError("Your credit balance is too low to access the API", 400))
    assert is_quota_exhausted(ApiError("insufficient_quota", 429))


def test_retry_after_is_parsed_from_provider_messages() -> None:
    from app.llm.errors import retry_after_seconds

    google = ApiError("429 RESOURCE_EXHAUSTED ... Please retry in 51.77s.", 429)
    assert retry_after_seconds(wrapped(google)) == 51.77
    info = ApiError("{'@type': 'RetryInfo', 'retryDelay': '12s'}", 429)
    assert retry_after_seconds(info) == 12.0
    assert retry_after_seconds(ApiError("overloaded", 503)) is None
