ERROR_MESSAGES = {
    "section_not_found": "Learning section not found",
    "notion_not_configured": "Notion learning connection is not configured",
    "notion_not_available": "Notion learning connection is not available for this user",
    "source_unauthorized": "Source credential is invalid or expired",
    "source_forbidden": "Source access denied; check the connection permissions",
    "source_not_found": "Source page not found or not shared with the connection",
    "source_rate_limited": "Source rate limit reached; try again later",
    "source_timeout": "Source request timed out",
    "source_unavailable": "Source request failed",
    "source_redirect": "Source redirected; the configured address needs verification",
    "source_too_large": "Source response exceeds the reading limit",
    "invalid_source_response": "Source returned an invalid response",
    "source_schema_mismatch": "Source fields do not match the learning record schema",
}


class LearningSourceError(Exception):
    """只保存受控错误码，不保存请求头、正文或原始异常。"""

    def __init__(
        self,
        code: str,
        *,
        status_code: int = 502,
        retry_after: str | None = None,
    ) -> None:
        if code not in ERROR_MESSAGES:
            code = "source_unavailable"
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(ERROR_MESSAGES[code])
