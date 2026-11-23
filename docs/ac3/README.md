# AC3: Protecting small customers from heavy reports

## Implemented feature

Added a concurrency limiter to the monthly-report endpoint.

- At most two whale reports execute simultaneously per application process.
- Additional whale requests wait asynchronously before entering the endpoint.
- Small customers bypass the limiter.
- The queue permits up to 80 waiting requests.
- Requests exceeding the queue capacity or 25-second wait timeout receive HTTP 503 with a Retry-After header.

WHALE_REPORT_CONCURRENCY=0 disables the limiter.
WHALE_REPORT_CONCURRENCY=2 enables the tested configuration.

The limit applies separately to each application process.

## Testing

Four unit tests passed, checking:
- The concurrent execution limit.
- Small-customer bypass.
- Slot release after a report fails.
- Queue timeout handling.

The performance harness also reported that functional tests passed.

Both performance runs used identical resource limits, 60 seconds per phase,
10 small-customer workers, and 40 whale workers.
Each configuration was measured once.

## Results

Comparison of split application mode with the limiter OFF and ON:

| Metric under whale load | OFF | ON |
|---|---:|---:|
| Small-customer p95 | 63.8 ms | 55.3 ms |
| Small-customer p99 | 161.5 ms | 148.9 ms |
| Small requests completed | 10,211 | 11,464 |
| Whale requests completed | 410 | 426 |
| Whale p95 | 7,834.8 ms | 6,335.9 ms |
| Small / whale errors | 0 / 0 | 0 / 0 |

With the limiter enabled, small-customer p95 was 13.3% lower,
and 12.3% more small requests completed.
Whale throughput also improved, with no recorded errors.

Small-customer p95 slowdown relative to the corresponding no-whale phase
decreased from 70.59% to 41.07%.

## Limitations

The implemented feature improved performance in this experiment.
However, the original AC3 target of less than 10% slowdown was not met.

These are single-run synthetic results. They do not establish repeatability,
1200-user capacity, or a 99.9% availability SLA.

## Reproduce

Run these commands from the repository root, one at a time.
Docker must be running and the project dependencies installed.

Unit tests:

```bash
python -m unittest discover -s worker_tests -p 'test_report_limit.py' -v
```

Baseline with the limiter disabled:

```bash
WHALE_REPORT_CONCURRENCY=0 python worker_tests/run.py --performance --duration 60 --repeats 1 --small-workers 10 --whale-workers 40
```

Implemented version with the limiter enabled:

```bash
WHALE_REPORT_CONCURRENCY=2 python worker_tests/run.py --performance --duration 60 --repeats 1 --small-workers 10 --whale-workers 40
```

## Evidence

- [Limiter OFF report](limiter-off.json)
- [Limiter ON report](limiter-on.json)