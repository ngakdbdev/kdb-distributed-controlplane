/ tick.q - tickerplant for one shard
/ usage: q tick.q <shardId> -p <port>
/   shardId: e.g. "s0" / "s1" - used for the log filename and shard tag
/ Responsibilities (the standard kdb-tick pattern) plus SLOW-SUBSCRIBER
/ AUTO-DISCARD:
/   1. accept inbound ticks from feed handlers via .u.upd
/   2. log every tick to a TP log file before ack, for disaster recovery
/   3. publish every tick to subscribed processes (WDB, chained RDB)
/   4. relay the flushed watermark from the WDB to the chained RDB in order
/   5. watch each subscriber's outbound queue; if a consumer falls too far
/      behind (its queued bytes exceed a threshold for N consecutive checks),
/      DISCONNECT it. A slow subscriber otherwise backs up the tickerplant's
/      output buffers and can OOM the whole TP - dropping the one slow consumer
/      protects every other subscriber and the plant itself.

\l schema.q

shardId:first .z.x
if[0=count shardId; shardId:"s0"]

.u.shard:`$shardId
.u.l:`$":log/", shardId, "_tp"
.u.L:`$(string[.u.l],".",string .z.d)   / e.g. `:log/s0_tp.2026.08.03
if[not `TPLOG in key `.; system "mkdir -p log"]

.u.w:()!()                          / subscriber handles keyed by table
.u.i:0j                             / running message sequence number
.u.recv:0j                          / rows received from feeds
.u.pub:0j                           / rows pushed to subscribers
.u.dropped:0j                       / subscribers dropped (slow-consumer)
.u.lastTs:0Np                       / timestamp of last message
.u.pubNanos:0j                      / cumulative publish time (ns)
.u.pubN:0j                          / publish samples
.u.logNanos:0j                      / cumulative log-write time (ns)
.u.logN:0j                          / log-write samples

/ ---------------------------------------------- slow-subscriber config + state
.u.maxSubBytes:$[count getenv`SLOW_SUB_MAX_BYTES; "J"$getenv`SLOW_SUB_MAX_BYTES; 52428800]  / 50 MB
.u.slowStrikes:$[count getenv`SLOW_SUB_STRIKES;  "J"$getenv`SLOW_SUB_STRIKES;  3]           / consecutive breaches before drop
.u.slowCheckMs:$[count getenv`SLOW_SUB_CHECK_MS; "J"$getenv`SLOW_SUB_CHECK_MS; 1000]        / how often to check (ms); 0 disables
.u.controlApi:$[count getenv`CONTROL_API_URL; getenv`CONTROL_API_URL; ""]
.u.internalSecret:$[count getenv`INTERNAL_SECRET; getenv`INTERNAL_SECRET; ""]

.u.strikes:()!()                    / handle -> consecutive-breach count
.u.discarded:([] time:`timestamp$(); handle:`int$(); bytes:`long$(); tbls:`symbol$(); reason:`symbol$())

.u.init:{
  if[not `TPLOG in key `.; TPLOG::.u.L];
  L::hopen TPLOG;
  .u.i::0j;
  }

.u.sub:{[t;s]
  if[not t in tables`.; '"unknown table: ",string t];
  if[not t in key .u.w; .u.w[t]:`int$()];   / typed empty handle list -> no stray null on first sub
  .u.w[t],:.z.w;
  (t;value t)}

.u.upd:{[t;data]
  / feeds send a list of rows in schema order; coerce to typed columns so a
  / string from the wire lands in a `sym` column as a symbol, etc.
  tbl:value t; cs:cols tbl; tc:exec t from meta tbl;
  rows:$[0h>type first data; enlist data; data];   / single row -> one-row list
  d:$[98h=type data; data; flip cs!tc$'flip rows]; / already a table? use it; else build+cast
  t insert d;
  lw:.z.p; L enlist (`.u.upd;t;d); .u.logNanos+:`long$.z.p-lw; .u.logN+:1;
  .u.i+:1; .u.recv+:count d; .u.lastTs:.z.p;
  if[count w:.u.w t;
     pw:.z.p; (neg w) @\: (`.u.upd;t;d);
     .u.pubNanos+:`long$.z.p-pw; .u.pubN+:1; .u.pub+:(count w)*count d];
  }

/ the WDB calls this after every timed flush; the tickerplant relays it to
/ every subscriber on its own connection, guaranteeing ordering
.u.broadcastWatermark:{[w]
  subs:distinct raze value .u.w;
  if[count subs; (neg subs) @\: (`.u.shedTo;w)];
  }

/ ------------------------------------------------------ slow-subscriber logic
.u.subHandles:{distinct raze value .u.w}

/ bytes queued (async, unsent) on a handle's outbound buffer
.u.queued:{[h] $[h in key .z.W; sum .z.W h; 0]}

/ tables a given handle is subscribed to
.u.tablesOf:{[h] where {[hs;g] g in hs}[;h] each .u.w}

/ best-effort report to the control API's internal audit endpoint (secret in
/ the body so a plain q HTTP POST works - no custom headers needed)
.u.notifyDiscard:{[h;bytes;tbls]
  if[0=count .u.controlApi; :()];
  body:.j.j `secret`actor`action`target`detail`outcome!(
    .u.internalSecret;
    "tp:",shardId;
    "slow_sub_discard";
    "handle ",string h;
    "queued ",(string bytes)," bytes; tables ",(", " sv string tbls);
    "success");
  .[.Q.hp;
    (`$(.u.controlApi,"/audit/internal"); "application/json"; body);
    {[e] -1 "[tp ",shardId,"] discard notify failed: ",e}];
  }

.u.dropSub:{[h;bytes]
  tbls:.u.tablesOf h;
  .u.w:{[hs;g] hs except g}[;h] each .u.w;         / unsubscribe from every table
  if[h in key .u.strikes; .u.strikes:.u.strikes _ h];
  @[hclose; h; ::];                                 / close the slow handle
  `.u.discarded insert (.z.p; h; bytes; `$", " sv string tbls; `slow_consumer);
  .u.dropped+:1;
  -1 "[tp ",shardId,"] DISCARDED slow subscriber handle ",string[h],
     " (",string[bytes]," bytes queued over ",string[.u.slowStrikes]," checks)";
  .u.notifyDiscard[h;bytes;tbls];
  }

.u.checkSlow:{
  {[h]
    b:.u.queued h;
    $[b > .u.maxSubBytes;
      [ .u.strikes[h]:1 + $[h in key .u.strikes; .u.strikes h; 0];
        if[.u.strikes[h] >= .u.slowStrikes; .u.dropSub[h;b]] ];
      if[h in key .u.strikes; .u.strikes:.u.strikes _ h] ];   / recovered -> reset strikes
   } each .u.subHandles[];
  }

/ current per-subscriber queue depth + strike count, queryable over IPC by the
/ control plane / dashboard
.u.subStats:{
  hs:.u.subHandles[];
  ([] handle:hs;
      queuedBytes:.u.queued each hs;
      strikes:{$[x in key .u.strikes; .u.strikes x; 0]} each hs;
      tbls:{`$", " sv string .u.tablesOf x} each hs)
  }

/ clean up a subscriber that disconnected on its own (fixes the leaked-handle
/ bug in the vanilla version) - drop it from every table and from strikes
.z.pc:{[h]
  .u.w:{[hs;g] hs except g}[;h] each .u.w;
  if[h in key .u.strikes; .u.strikes:.u.strikes _ h];
  }

/ timer drives the slow-subscriber sweep
.z.ts:{ @[.u.checkSlow; ::; {[e] -1 "[tp] slow-sub check error: ",e}] }
if[.u.slowCheckMs>0; system "t ",string .u.slowCheckMs]

.u.end:{[d]
  -1 "[tp ",shardId,"] end of day rollover for ",string d;
  }

/ -------------------------------------------------- monitoring accessors (IPC)
/ point-in-time counters + gauges; the control plane polls this and derives
/ per-second rates from deltas between polls.
.u.stats:{
  hs:.u.subHandles[];
  qd:$[count hs; sum .u.queued each hs; 0];
  lag:$[count hs; max 0,.u.queued each hs; 0];
  `shard`recv`pub`dropped`queueDepth`subLag`subs`lastSeq`lastTs`pubLatencyUs`logLatencyUs`logBytes!(
    shardId; .u.recv; .u.pub; .u.dropped; qd; lag; count hs; .u.i; .u.lastTs;
    $[.u.pubN>0; (.u.pubNanos div .u.pubN) div 1000; 0];
    $[.u.logN>0; (.u.logNanos div .u.logN) div 1000; 0];
    @[hcount; .u.L; 0j])}

/ boolean health checks + overall; false where a signal is genuinely absent
/ (no feed yet, no subscribers) rather than faked green.
.u.health:{
  hs:.u.subHandles[];
  qd:$[count hs; sum .u.queued each hs; 0];
  lag:$[count hs; max 0,.u.queued each hs; 0];
  pl:$[.u.pubN>0; (.u.pubNanos div .u.pubN) div 1000; 0];
  checks:`process`feed`sequence`tplog`subscribers`publishLatency`queueDepth`downstreamLag!(
    1b;
    (not null .u.lastTs) and .u.lastTs > .z.p - 0D00:00:30;
    .u.i > 0;
    0 < @[hcount; .u.L; 0j];
    (count hs) > 0;
    pl < 100000;
    qd < .u.maxSubBytes;
    lag < .u.maxSubBytes);
  `shard`checks`overall!(shardId; checks; all value checks)}

.u.init[]
-1 "[tp ",shardId,"] tickerplant up on port ",(string system"p")," | slow-sub discard over ",
   string[.u.maxSubBytes]," bytes x ",string[.u.slowStrikes]," checks";
