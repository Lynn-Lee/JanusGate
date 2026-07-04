"""Minimal Prometheus metrics registry for the FastAPI app."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import perf_counter

from fastapi import Request, Response

RequestHandler = Callable[[Request], Awaitable[Response]]

PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
REQUEST_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass
class HttpMetricSample:
    count: int = 0
    duration_sum: float = 0.0
    buckets: dict[float, int] = field(
        default_factory=lambda: dict.fromkeys(REQUEST_DURATION_BUCKETS, 0)
    )


class PrometheusMetricsRegistry:
    def __init__(self) -> None:
        self._samples: defaultdict[tuple[str, str, str], HttpMetricSample] = defaultdict(
            HttpMetricSample
        )

    def record_http_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        sample = self._samples[(method.upper(), path, str(status_code))]
        sample.count += 1
        sample.duration_sum += duration_seconds
        for bucket in REQUEST_DURATION_BUCKETS:
            if duration_seconds <= bucket:
                sample.buckets[bucket] += 1

    def render(self) -> str:
        lines = [
            "# HELP janusgate_http_requests_total Total HTTP requests handled by JanusGate.",
            "# TYPE janusgate_http_requests_total counter",
        ]
        for (method, path, status_code), sample in sorted(self._samples.items()):
            labels = _labels(method=method, path=path, status_code=status_code)
            lines.append(f"janusgate_http_requests_total{{{labels}}} {sample.count}")

        lines.extend(
            [
                "# HELP janusgate_http_request_duration_seconds HTTP request duration in seconds.",
                "# TYPE janusgate_http_request_duration_seconds histogram",
            ]
        )
        for (method, path, status_code), sample in sorted(self._samples.items()):
            cumulative = 0
            for bucket in REQUEST_DURATION_BUCKETS:
                cumulative = sample.buckets[bucket]
                labels = _labels(
                    method=method,
                    path=path,
                    status_code=status_code,
                    le=_format_bucket(bucket),
                )
                lines.append(
                    f"janusgate_http_request_duration_seconds_bucket{{{labels}}} {cumulative}"
                )
            labels = _labels(method=method, path=path, status_code=status_code, le="+Inf")
            lines.append(f"janusgate_http_request_duration_seconds_bucket{{{labels}}} {sample.count}")
            base_labels = _labels(method=method, path=path, status_code=status_code)
            lines.append(
                f"janusgate_http_request_duration_seconds_sum{{{base_labels}}} "
                f"{sample.duration_sum:.6f}"
            )
            lines.append(
                f"janusgate_http_request_duration_seconds_count{{{base_labels}}} {sample.count}"
            )
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        self._samples.clear()


metrics_registry = PrometheusMetricsRegistry()


async def prometheus_metrics_middleware(
    request: Request,
    call_next: RequestHandler,
) -> Response:
    started_at = perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        metrics_registry.record_http_request(
            method=request.method,
            path=route_path,
            status_code=response.status_code,
            duration_seconds=perf_counter() - started_at,
        )
    return response


def metrics_response() -> Response:
    return Response(metrics_registry.render(), media_type=PROMETHEUS_CONTENT_TYPE)


def _labels(**labels: str) -> str:
    return ",".join(f'{name}="{_escape_label_value(value)}"' for name, value in labels.items())


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_bucket(bucket: float) -> str:
    return f"{bucket:g}"
