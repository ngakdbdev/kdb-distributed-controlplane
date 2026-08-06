\l /app/schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
tpHost:first .u.getarg[args;`tphost;enlist "localhost"]
tpPort:"I"$first .u.getarg[args;`tpport;enlist "5010"]
flushIntv:"I"$first .u.getarg[args;`flushmin;enlist "2"]
dbDir:first .u.getarg[args;`dbdir;enlist "./db"]
hdbDir:first .u.getarg[args;`hdbdir;enlist "./hdb"]
idbHost:first .u.getarg[args;`idbhost;enlist ""]
idbPort:"I"$first .u.getarg[args;`idbport;enlist "0"]
hdbHost:first .u.getarg[args;`hdbhost;enlist ""]
hdbPort:"I"$first .u.getarg[args;`hdbport;enlist "0"]

.wdb.shard:`$shardId
.wdb.dbDir:dbDir
.wdb.hdbDir:hdbDir
.wdb.idbAddr:$[count idbHost;`$":",idbHost,":",string idbPort;`]
.wdb.hdbAddr:$[count hdbHost;`$":",hdbHost,":",string hdbPort;`]
.wdb.flushIntv:flushIntv
.wdb.lastWatermark:0Np
.wdb.lastSealedDate:0Nd
.wdb.lastFlushTime:.z.p
.wdb.tpHandle:0Ni
.wdb.tpAddr:`$":",tpHost,":",string tpPort
.wdb.reconnectAttempts:0j
.wdb.lastReconnectAttempt:0Np
.wdb.everConnected:0b

if[not `metrics in key `.; metrics::([] time:`timestamp$(); tbl:`symbol$(); metric_name:`symbol$(); val:`float$())]

.wdb.logMetric:{[tbl;metric;v]
  `metrics insert (.z.p;tbl;metric;"f"$v);
  }

.wdb.upd:{[t;data]
  data:$[0>type data; enlist data; data];
  t insert update shard:.wdb.shard from data;
  .wdb.logMetric[t;`batch_arrived;count data];
  }

.wdb.shedTo:{[w] }

/ scratch flat-file path for one date/table - what the rolling flush appends
/ to through the day, and what .wdb.seal consumes+retires at end of day.
/ `$string[d] MUST be parenthesized: bare `$string[d],tbl parses as
/ `$(string[d],tbl) (`$ binds rightward across the comma), casting a
/ string+symbol join and throwing 'type - a real trap, confirmed directly
/ against this build.
.wdb.scratchFile:{[d;tbl] ` sv hsym[`$.wdb.dbDir],(`$string[d]),tbl}

/ every row strictly before `cutoff` gets APPENDED to its date's scratch file
/ (a date accumulates across many flush cycles before it's sealed - this used
/ to overwrite the file each cycle, silently dropping every batch but the
/ last one for a given date) then dropped from the in-memory table.

.wdb.flushTo:{[cutoff]
  t:tables`.;
  t:t where not t in `metrics;
  {[cutoff;tbl]
    rows:select from tbl where time < cutoff;
    if[0=count rows; :()];
    dates:distinct `date$rows`time;
    {[tbl;rows;d]
      drows:select from rows where (`date$time)=d;
      ddir:.wdb.dbDir,"/",string d;
      system "mkdir -p ",ddir;
      f:.wdb.scratchFile[d;tbl];
      exists:tbl in key hsym `$ddir;
      existing:$[exists;get f;0#drows];
      f set existing,drows;
      .wdb.logMetric[tbl;`rows_flushed;count drows];
      }[tbl;rows] each dates;
    delete from tbl where time < cutoff;
    }[cutoff] each t;
  }

.wdb.flush:{
  cutoff:.z.p - .wdb.flushIntv * 0D00:01;
  .wdb.flushTo[cutoff];
  .wdb.lastWatermark::cutoff;
  h:@[hopen; .wdb.tpAddr; {[e] -1 "wdb: could not reach tp to broadcast watermark: ",e; 0Ni}];
  if[not null h; h (`.u.broadcastWatermark; cutoff); hclose h];
  -1 "[wdb ",shardId,"] flushed at ",string cutoff;
  }

/ seal one closed date's scratch file into a real partitioned HDB segment:
/ sort by sym, enumerate + splay via .Q.dpft, then retire the scratch file.
/ Uses a root-level temp var (not .wdb.sealtmp) because that's the form
/ .Q.dpft is proven (tested directly against this build) to accept.
.wdb.seal:{[d]
  {[d;tbl]
    f:.wdb.scratchFile[d;tbl];
    ddir:.wdb.dbDir,"/",string d;
    if[not tbl in key hsym `$ddir; :()];
    rows:get f;
    if[0=count rows; hdel f; :()];
    sealtmp::`sym xasc rows;
    @[{.Q.dpft[hsym `$.wdb.hdbDir;x;`sym;`sealtmp]};
      d;
      {[tbl;d;e] -1 "[wdb] EOD seal FAILED for ",string[tbl]," on ",string[d],": ",e}[tbl;d]];
    delete sealtmp from `.;
    hdel f;
    -1 "[wdb ",string[.wdb.shard],"] sealed ",string[count rows]," ",string[tbl]," rows -> hdb partition ",string d;
    }[d] each `trade`risk;
  }

/ best-effort ping to a downstream process after sealing - hdb reloads its
/ partitioned dir to see the new day, idb drops its now-redundant in-memory
/ copy of that date. Both also self-heal on their own timers if this misses.
.wdb.notify:{[addr;msg]
  if[null addr; :()];
  h:@[hopen; addr; {[a;e] -1 "wdb: could not reach ",string[a]," to notify: ",e; 0Ni}[addr]];
  if[not null h; @[h; msg; {[e] -1 "wdb: notify call failed: ",e}]; hclose h];
  }

/ end-of-day: force-flush every row still in memory dated on or before `d`
/ (the rolling flush only takes rows older than flushIntv - this closes the
/ gap for whatever arrived since the last cycle), seal the day into the HDB,
/ then push the watermark forward so the chained rdb sheds anything left
/ of day `d` too, and tell hdb/idb about the newly-sealed day.
.wdb.eod:{[d]
  cutoff:`timestamp$d+1;
  .wdb.flushTo[cutoff];
  .wdb.seal[d];
  h:@[hopen; .wdb.tpAddr; {[e] -1 "wdb: could not reach tp to broadcast eod watermark: ",e; 0Ni}];
  if[not null h; h (`.u.broadcastWatermark; cutoff); hclose h];
  .wdb.notify[.wdb.hdbAddr; (`.hdb.reload;::)];
  .wdb.notify[.wdb.idbAddr; (`.idb.dropDate;d)];
  .wdb.lastWatermark::cutoff;
  .wdb.lastSealedDate::d;
  -1 "[wdb ",shardId,"] end-of-day seal complete for ",string d;
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
  backoff:0D00:00:01 * 2 xexp (.wdb.reconnectAttempts & 5);
  if[(0Np~.wdb.lastReconnectAttempt) or (.z.p - .wdb.lastReconnectAttempt) > backoff;
    .wdb.lastReconnectAttempt::.z.p;
    .wdb.reconnectAttempts+:1;
    if[not null .wdb.connectTp[]; .wdb.everConnected::1b];
    ];
  }

.wdb.health:{
  `status`shard`tpConnected`reconnectAttempts`lastWatermark`lastSealedDate!
    (`up;.wdb.shard;not null .wdb.tpHandle;.wdb.reconnectAttempts;.wdb.lastWatermark;.wdb.lastSealedDate)
  }

.z.ts:{
  .wdb.checkConnection[];
  if[(.z.p - .wdb.lastFlushTime) > (.wdb.flushIntv * 0D00:01);
    .wdb.lastFlushTime::.z.p;
    .wdb.flush[];
    ];
  }
\t 5000

.u.upd:.wdb.upd
.u.shedTo:.wdb.shedTo
.u.eod:.wdb.eod

if[not null .wdb.connectTp[]; .wdb.everConnected::1b];

-1 "[wdb ",shardId,"] write-down db up, flush every ",string[flushIntv]," min, writing to ",dbDir,", sealing to ",hdbDir," at end of day";
