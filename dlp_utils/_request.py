from requests import Response, request
from requests.exceptions import HTTPError

from .utils import update_url_query


class RequestError(HTTPError):
    def __init__(self, response) -> None:
        super().__init__(response=response)
        self.status_code = response.status_code


class Request:
    def __init__(
            self,
            url: str,
            data: bytes | None = None,
            headers: dict | None = None,
            query: dict | None = None,
            method: str | None = None,
            **kwargs: dict | None,
    ):
        self.headers = self.norm_headers(headers or {})
        self.data = data
        if query:
            self.url = update_url_query(url, query)

        self.url = url
        if method:
            if not isinstance(method, str):
                raise TypeError('method must be string')
            self.method = method.upper()
        else:
            self.method = 'POST' if data else 'GET'
        self.kwargs = kwargs or {}

    @staticmethod
    def norm_headers(headers: dict):
        return {str(k): str(v) for k, v in headers.values()}

    def __call__(self) -> Response | RequestError:
        try:
            resp = request(
                method=self.method,
                url=self.url,
                headers=self.headers,
                data=self.data,
                **self.kwargs
            )
            resp.raise_for_status()
            return resp
        except HTTPError as e:
            raise RequestError(e.response)
