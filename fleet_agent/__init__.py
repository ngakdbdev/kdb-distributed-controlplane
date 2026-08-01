"""
fleet_agent - the process that runs inside a tenant's own AWS/Azure/GCP/on-prem
cluster and executes commands from the SaaS control plane.

The control plane never connects INTO the tenant's network. Instead this agent
calls out: it enrolls once with a one-time token, then heartbeats on a loop,
pulling queued commands and reporting results. Commands are start/stop/restart
of a local service, or - the reason this package exists - `provision` /
`deprovision`, which reconcile the local data plane to a desired shard count.

"Create ticker plant components in AWS from the UI" resolves, concretely, to:
tenant admin picks their AWS agent + a shard count -> control plane queues a
provision command carrying the desired topology -> this agent runs
`helm upgrade --set shardCount=N` (or the compose equivalent) in its own
cluster -> the ticker plant / rdb / idb / gateway processes for N shards come
up -> the agent reports success. kdb+ is never reached from our side.

Layering mirrors demokit: `provisioner` is a pure reconcile core with no
subprocess or network dependency (unit-tested with fakes), and `backends`
holds the real Helm/Compose executors that only run inside a real cluster.
"""
