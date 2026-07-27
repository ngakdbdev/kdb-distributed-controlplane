/ tick.q - tickerplant for one shard
/ usage: q tick.q <shardId> -p <port>
/   shardId: e.g. "A_M" or "N_Z" - used for the log filename and shard tag
/ Responsibilities (per the standard kdb-tick pattern):
/   1. accept inbound ticks from feed handlers via .u.upd
/   2. log every tick to a TP log file before ack, for disaster recovery
/   3. publish every tick to subscribed processes (WDB, chained RDB)
/   4. relay the flushed watermark broadcast from the WDB to the chained RDB,
/      on the same ordered connection the ticks travel on, so the shed
/      instruction can never race ahead of in-flight rows

\l schema.q

shardId:first .z.x
if[0=count shardId; shardId:"A_M"]

.u.shard:`$shardId
.u.l:`$":log/", shardId, "_tp"
.u.L:.u.l,".",string .z.d
if[not `TPLOG in key `.; system "mkdir log"]

.u.w:()!()                          / subscriber handles keyed by table
.u.i:0j                             / running message sequence number

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

.u.end:{[d]
  -1 "[tp ",shardId,"] end of day rollover for ",string d;
  }

.u.init[]
-1 "[tp ",shardId,"] tickerplant up on port ",string system"p";
