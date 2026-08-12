/ tick.q - tickerplant for one shard
/ usage: q tick.q <shardId> -p <port>
/   shardId: e.g. "s0" / "s1" - used for the log filename and shard tag
/ Responsibilities (the standard kdb-tick pattern) plus SLOW-SUBSCRIBER
/ AUTO-DISCARD plus END-OF-DAY ROLLOVER:
/   1. accept inbound ticks from feed handlers via .u.upd
/   2. log every tick to a TP log file before ack, for disaster recovery
/   3. publish every tick to subscribed processes (WDB, chained RDB)
/   4. relay the flushed watermark from the WDB to the chained RDB in order
/   5. watch each subscriber's outbound queue; if a consumer falls too far
/      behind (its queued bytes exceed a threshold for N consecutive checks),
/      DISCONNECT it. A slow subscriber otherwise backs up the tickerplant's
/      output buffers and can OOM the whole TP - dropping the one slow consumer
/      protects every other subscriber and the plant itself.
/   6. detect the trading-day boundary itself (no external cron dependency)
/      and broadcast end-of-day to every subscriber, then rotate its own log.

\l /app/schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

/ shardId is a bare positional arg ("tick.q s0 -p 5010"), unlike the other
/ tiers - keep that convention; only the eod hour comes via .Q.opt below.
shardId:first .z.x
if[0=count shardId; shardId:"s0"]

args:.Q.opt .z.x

/ trading-day boundary: which UTC hour the day rolls over at. 0 = midnight UTC
/ (the default - unchanged behaviour); set e.g. 21 to roll over at 21:00 UTC
/ for an exchange whose close is there instead. Per-TickHouse config, not a
/ hardcoded assumption.
.u.eodHour:"I"$first .u.getarg[args;`eodhour;enlist "0"]
.u.tradingDate:{`date$(.z.p - .u.eodHour*0D01:00:00)}
.u.today:.u.tradingDate[]

.u.l:`$":log/", shardId, "_tp"
.u.L:`$(string[.u.l],".",string .u.today)   / e.g. `:log/s0_tp.2026.08.03
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
.u.errs:0j                          / .u.upd errors (bad batches)

/ ---------------------------------------------- slow-subscriber config + state
.u.maxSubBytes:$[count getenv`SLOW_SUB_MAX_BYTES; "J"$getenv`SLOW_SUB_MAX_BYTES; 52428800]  / 50 MB
.u.slowStrikes:$[count getenv`SLOW_SUB_STRIKES;  "J"$getenv`SLOW_SUB_STRIKES;  3]           / consecutive breaches before drop
.u.slowCheckMs:$[count getenv`SLOW_SUB_CHECK_MS; "J"$getenv`SLOW_SUB_CHECK_MS; 1000]        / how often to check (ms); 0 disables the slow-sub sweep (eod check still runs)
.u.controlApi:$[count getenv`CONTROL_API_URL; getenv`CONTROL_API_URL; ""]
.u.internalSecret:$[count getenv`INTERNAL_SECRET; getenv`INTERNAL_SECRET; ""]

.u.strikes:()!()                    / handle -> consecutive-breach count
.u.discarded:([] time:`timestamp$(); handle:`int$(); bytes:`long$(); tbls:`symbol$(); reason:`symbol$())

.u.init:{
  if[not `TPLOG in key `.; TPLOG::.u.L];
  L::hopen TPLOG;
  .u.i::0j;
  }

/ NOTE: the standard kdb-tick pattern has .u.sub return (t; value t) - the
/ table's CURRENT CONTENT - so a cold subscriber can seed its in-memory view
/ from it instead of just future updates. Neither subscriber in THIS
/ architecture uses that: rdb.q warm-starts from wdb's on-disk files, wdb.q
/ warm-starts from its own db dir - both discard .u.sub's return value
/ entirely (confirmed: grep shows zero uses of it in either). Returning
/ `value t` here was therefore pure overhead - and a GROWING one, since t
/ itself was never purged (see .u.doUpd below): confirmed live, tp-s0's
/ `trade` had grown to 7M+ rows after a day's real volume, so EVERY
/ reconnect synchronously shipped that entire table over IPC as part of the
/ handshake. That's what was actually causing the repeated "tp connect/
/ resubscribe failed" cycles on wdb-s0/rdb-s0 tonight (the sync call timing
/ out or the connection resetting mid-transfer) - not a data-volume problem,
/ a handshake-cost problem that got worse the longer the tp ran.
.u.sub:{[t;s]
  if[not t in tables`.; '"unknown table: ",string t];
  if[not t in key .u.w; .u.w[t]:`int$()];   / typed empty handle list -> no stray null on first sub
  .u.w[t],:.z.w;
  t}

.u.upd:{[t;data]
  / never let a bad batch fail silently on the async path: run the real work
  / protected, and LOG the exact error + a data sample so a broken feed is
  / diagnosable from `docker logs tp-*` instead of vanishing.
  @[.u.doUpd t; data; {[e]
     .u.errs+:1;
     -1 "[tp ",shardId,"] .u.upd ERROR: ",e," | sample=",(-3!3#data);
   }]}

.u.doUpd:{[t;data]
  / feeds send a list of rows in schema order; coerce to typed columns so a
  / string from the wire lands in a `sym` column as a symbol, etc. `tbl`
  / only needs the table's SCHEMA (column names/types via meta) - that's
  / unaffected by row count, so the empty typed table from schema.q works
  / fine and there's no need to have ever accumulated rows into it.
  tbl:value t; cs:cols tbl; tc:exec t from meta tbl;
  rows:$[0h>type first data; enlist data; data];   / single row -> one-row list
  d:$[98h=type data; data; flip cs!tc$'flip rows]; / already a table? use it; else build+cast
  / Deliberately NOT `t insert d` - the tickerplant is a pass-through, not a
  / store: nothing here reads trade/risk's accumulated rows (.u.sub above
  / used to, that's exactly the bug this fixes - see its comment), and
  / letting them grow unbounded for a full trading day is a genuine, live-
  / confirmed memory/handshake-cost problem, not a hypothetical one. The TP
  / LOG (L below) remains the real disaster-recovery record.
  lw:.z.p; L enlist (`.u.upd;t;d); .u.logNanos+:`long$.z.p-lw; .u.logN+:1;
  .u.i+:1; .u.recv+:count d; .u.lastTs:.z.p;
  if[count w:.u.w t;
     pw:.z.p; (neg w) @\: (`.u.upd;t;d);
     .u.pubNanos+:`long$.z.p-pw; .u.pubN+:1; .u.pub+:(count w)*count d];
  }

/ the WDB calls this after every timed flush (including its end-of-day one);
/ the tickerplant relays it to every subscriber on its own connection,
/ guaranteeing ordering
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

/ clean up a subscriber that disconnected on its own (fixes the leaked-handle
/ bug in the vanilla version) - drop it from every table and from strikes
.z.pc:{[h]
  .u.w:{[hs;g] hs except g}[;h] each .u.w;
  if[h in key .u.strikes; .u.strikes:.u.strikes _ h];
  }

/ ---------------------------------------------------------- end-of-day logic
/ relay end-of-day to every subscriber (wdb seals the closing day into the
/ hdb and force-flushes any tail rows; rdb defensively clears itself of the
/ closing day too, belt-and-suspenders with the watermark wdb's seal sends)
.u.broadcastEod:{[d]
  subs:distinct raze value .u.w;
  if[count subs; (neg subs) @\: (`.u.eod;d)];
  }

/ close the current log, open a fresh one named for the new trading day, and
/ restart the sequence counter - without this a tp that outlives midnight
/ keeps appending to yesterday's log file forever.
.u.rotateLog:{
  @[hclose;L;{[e]}];
  TPLOG::`$(string[.u.l],".",string .u.today);
  L::hopen TPLOG;
  .u.i::0j;
  -1 "[tp ",shardId,"] rotated tp log -> ",string TPLOG;
  }

.u.doEod:{[d]
  -1 "[tp ",shardId,"] end of day rollover for ",string d;
  .u.broadcastEod[d];
  .u.rotateLog[];
  }

/ self-triggered: no external cron/scheduler container needed or assumed up.
/ checked on every timer tick regardless of whether the slow-sub sweep is
/ enabled - a disabled slow-sub feature must never also silently disable EOD.
.u.checkEod:{
  d:.u.tradingDate[];
  if[d>.u.today;
    closing:.u.today;
    .u.today::d;
    .u.doEod[closing];
    ];
  }

/ manual/test override: force end-of-day for `d` right now, independent of
/ the wall clock. Also advances .u.today so the automatic check doesn't fire
/ again for the same day.
.u.end:{[d]
  .u.today::d+1;
  .u.doEod[d];
  }

/ timer drives both the slow-subscriber sweep and the eod check
.z.ts:{
  @[.u.checkEod; ::; {[e] -1 "[tp ",shardId,"] eod check error: ",e}];
  if[.u.slowCheckMs>0; @[.u.checkSlow; ::; {[e] -1 "[tp ",shardId,"] slow-sub check error: ",e}]];
  }
.u.timerMs:$[.u.slowCheckMs>0;.u.slowCheckMs;1000]
system "t ",string .u.timerMs

/ -------------------------------------------------- monitoring accessors (IPC)
/ point-in-time counters + gauges; the control plane polls this and derives
/ per-second rates from deltas between polls.
.u.stats:{
  hs:.u.subHandles[];
  qd:$[count hs; sum .u.queued each hs; 0];
  lag:$[count hs; max 0,.u.queued each hs; 0];
  `shard`recv`pub`dropped`errs`queueDepth`subLag`subs`lastSeq`lastTs`pubLatencyUs`logLatencyUs`logBytes!(
    shardId; .u.recv; .u.pub; .u.dropped; .u.errs; qd; lag; count hs; .u.i; .u.lastTs;
    $[.u.pubN>0; (.u.pubNanos div .u.pubN) div 1000; 0];
    $[.u.logN>0; (.u.logNanos div .u.logN) div 1000; 0];
    @[hcount; TPLOG; 0j])}

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
    0 < @[hcount; TPLOG; 0j];
    (count hs) > 0;
    pl < 100000;
    qd < .u.maxSubBytes;
    lag < .u.maxSubBytes);
  `shard`checks`overall!(shardId; checks; all value checks)}

.u.init[]
-1 "[tp ",shardId,"] tickerplant up on port ",(string system"p")," | slow-sub discard over ",
   string[.u.maxSubBytes]," bytes x ",string[.u.slowStrikes]," checks | eod hour ",string[.u.eodHour],"h UTC";
