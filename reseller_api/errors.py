from __future__ import annotations


class ResellerAPIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        request_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" + (f" (request_id={request_id})" if request_id else ""))
        self.code = code
        self.message = message
        self.request_id = request_id
        self.status_code = status_code
