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
.u.L:.u.l,".",string .z.d
if[not `TPLOG in key `.; system "mkdir -p log"]

.u.w:()!()                          / subscriber handles keyed by table
.u.i:0j                             / running message sequence number

/ ---------------------------------------------- slow-subscriber config + state
.u.maxSubBytes:$[count getenv`SLOW_SUB_MAX_BYTES; "J"$getenv`SLOW_SUB_MAX_BYTES; 52428800]  / 50 MB
.u.slowStrikes:$[count getenv`SLOW_SUB_STRIKES;  "J"$getenv`SLOW_SUB_STRIKES;  3]           / consecutive breaches before drop
.u.slowCheckMs:$[count getenv`SLOW_SUB_CHECK_MS; "J"$getenv`SLOW_SUB_CHECK_MS; 1000]        / how often to check (ms); 0 disables
.u.controlApi:$[count getenv`CONTROL_API_URL; getenv`CONTROL_API_URL; ""]
.u.internalSecret:$[count getenv`INTERNAL_SECRET; getenv`INTERNAL_SECRET; ""]

.u.strikes:()!()                    / handle -> consecutive-breach count
.u.discarded:([] time:`timestamp$(); handle:`int$(); bytes:`long$(); tables:`symbol$(); reason:`symbol$())

.u.init:{
  if[not `TPLOG in key `.; TPLOG::.u.L];
  L::hopen `$":",string TPLOG;
  .u.i::0j;
  }

.u.sub:{[t;s]
  if[not t in tables`.; '"unknown table: ",string t];
  .u.w[t],:enlist .z.w;
  (t;value t)}

.u.upd:{[t;data]
  data:$[0>type data; enlist data; data];
  t insert data;
  -25!(`.u.upd;t;data);              / log first, ack second (DR guarantee)
  .u.i+:1;
  if[count w:.u.w t; (neg w) @\: (`.u.upd;t;data)];
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
      tables:{`$", " sv string .u.tablesOf x} each hs)
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

.u.init[]
-1 "[tp ",shardId,"] tickerplant up on port ",string system"p",
   "; slow-sub discard > ",string[.u.maxSubBytes]," bytes x ",string[.u.slowStrikes]," checks";
