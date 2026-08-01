"""
demokit - the sales-demo toolkit for the kdb+ tick control plane.

Two operator tools live here, plus a small pure core that makes both of them
unit-testable without a live KDB-X install:

* `demokit.demo`      - a narrated, scripted end-to-end walkthrough that drives
                        the control-API over HTTP: log in, enable the feeds,
                        watch ingest climb, kill a tickerplant, watch the
                        watchdog heal it, then show the audit trail. This is
                        the thing you run (or talk over) in front of a prospect.

* `demokit.load_test` - a throughput + slow-subscriber harness that drives the
                        *data plane* over kdb+ IPC, reusing the real feed
                        publisher, and reports the numbers YOU measured on YOUR
                        deployment - never a number quoted from a slide.

The pure accounting core (`demokit.harness`) has no kdb+ or network
dependency, so its arithmetic is covered by `demokit/tests/` and runs in CI
with nothing installed but pytest.
"""
