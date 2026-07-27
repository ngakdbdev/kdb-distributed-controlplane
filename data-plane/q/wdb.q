/ wdb.q - write-down database for one shard (Tick-X pattern)
/ Subscribes to the tickerplant, buffers ticks, and on a timer flushes
/ rows older than a cutoff to a fresh int-partition on disk, then tells
/ the tickerplant to broadcast the flush watermark so the chained RDB can
/ shed the same rows from memory. This keeps RDB and WDB/IDB tiers disjoint.
/
/ SELF-HEALING: same tickerplant-reconnect story as rdb.q - if the TP
/ restarts, this WDB must notice and resubscribe on its own rather than
/ silently stop receiving ticks (and therefore silently stop flushing and
/ silently stop broadcasting watermarks, which would then starve the RDB
/ tier too). See rdb.q for the fuller explanation of why this matters more
/ than it looks like it should.

\l schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
tpHost:first .u.getarg[args;`tphost;enlist "localhost"]
tpPort:"I"$first .u.getarg[args;`tpport;enlist "5010"]
flushIntv:"I"$first .u.getarg[args;`flushmin;enlist "2"]   / minutes
dbDir:first .u.getarg[args;`dbdir;enlist "./db"]

.wdb.shard:`$shardId
.wdb.dbDir:dbDir
.wdb.flushIntv:flushIntv
.wdb.lastWatermark:0Np
.wdb.lastFlushTime:.z.p
.wdb.tpHandle:0Ni
.wdb.tpAddr:`$":",tpHost,":",string tpPort
.wdb.reconnectAttempts:0j
.wdb.lastReconnectAttempt:0Np
.wdb.everConnected:0b

if[not `metrics in key `.; metrics::([] time:`timestamp$(); table:`symbol$();
    metric:`symbol$(); value:`float$())]

.wdb.logMetric:{[tbl;metric;val]
  `metrics insert (.z.p;tbl;metric;val);
  }

.wdb.upd:{[t;data]
  data:$[0>type data; enlist data; data];
  t insert update shard:.wdb.shard from data;
  .wdb.logMetric[t;`batch_arrived;count data];
  }

/ the tickerplant relays this after every WDB flush - the WDB itself never
/ needs to receive it, but chained RDB/IDB processes do; kept here for symmetry
.wdb.shedTo:{[w] }

.wdb.flush:{
  cutoff:.z.p - .wdb.flushIntv * 0D00:01;
  t:tables`.;
  t@:where not t in `metrics;
  {[cutoff;tbl]
    rows:select from tbl where time < cutoff;
    if[0=count rows; :()];
    system "mkdir -p ",.wdb.dbDir,"/",string[`date$cutoff];
    (` sv hsym[`$.wdb.dbDir],`$string[`date$cutoff],tbl) set rows;
    delete from tbl where time < cutoff;
    .wdb.logMetric[tbl;`rows_flushed;count rows];
    }[cutoff] each t;
  .wdb.lastWatermark::cutoff;
  / broadcast on a short-lived handle, independent of the subscription
  / handle above - a broadcast failure here just means the RDB tier keeps
  / rows a little longer than ideal, not a correctness problem, so this
  / stays a simple best-effort attempt rather than joining the reconnect logic
  h:@[hopen; .wdb.tpAddr; {[e] -1 "wdb: could not reach tp to broadcast watermark: ",e; 0Ni}];
  if[not null h; h (`.u.broadcastWatermark; cutoff); hclose h];
  -1 "[wdb ",shardId,"] flushed at ",string cutoff;
  }

.wdb.connectTp:{
  res:@[
    {
      h:hopen .wdb.tpAddr;
      h (`.u.sub;`trade;`);
      h (`.u.sub;`risk;`);
      h
      };
    ::;
    {[e] -1 "[wdb ",string[.wdb.shard],"] tp connect/resubscribe failed: ",e; 0Ni}
    ];
  if[not null res;
    .wdb.tpHandle::res;
    .wdb.reconnectAttempts::0;
    -1 "[wdb ",string[.wdb.shard],"] connected + subscribed to tp at ",string .wdb.tpAddr;
    ];
  res
  }

.wdb.tpIsAlive:{
  if[null .wdb.tpHandle; :0b];
  @[{.wdb.tpHandle (::); 1b}; ::; {0b}]
  }

.wdb.checkConnection:{
  if[.wdb.tpIsAlive[]; :()];
  if[.wdb.everConnected; -1 "[wdb ",string[.wdb.shard],"] lost connection to tickerplant, will reconnect"];
  .wdb.tpHandle::0Ni;
  backoff:0D00:00:01 * 2 xexp min[.wdb.reconnectAttempts;5];
  if[(0Np~.wdb.lastReconnectAttempt) or (.z.p - .wdb.lastReconnectAttempt) > backoff;
    .wdb.lastReconnectAttempt::.z.p;
    .wdb.reconnectAttempts+:1;
    if[not null .wdb.connectTp[]; .wdb.everConnected::1b];
    ];
  }

/ health endpoint the watchdog (and the gateway, via metrics) can poll
.wdb.health:{
  `status`shard`tpConnected`reconnectAttempts`lastWatermark!
    (`up;.wdb.shard;not null .wdb.tpHandle;.wdb.reconnectAttempts;.wdb.lastWatermark)
  }

/ single unified timer: checks the tp connection every tick (cheap), and
/ only actually flushes once flushIntv minutes have elapsed - kdb+ has one
/ global timer, so both concerns share it rather than fighting over it
.z.ts:{
  .wdb.checkConnection[];
  if[(.z.p - .wdb.lastFlushTime) > (.wdb.flushIntv * 0D00:01);
    .wdb.lastFlushTime::.z.p;
    .wdb.flush[];
    ];
  }
\t 5000

/ mirror handlers under the names the tickerplant actually calls on subscribers
.u.upd:.wdb.upd
.u.shedTo:.wdb.shedTo

if[not null .wdb.connectTp[]; .wdb.everConnected::1b];

-1 "[wdb ",shardId,"] write-down db up (self-healing tp connection enabled), flush every ",string[flushIntv]," min, writing to ",dbDir;
