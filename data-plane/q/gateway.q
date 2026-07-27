/ gateway.q - single entry point for the control API and any client.
/ Routes symbol-scoped queries to the right shard's RDB+IDB, fans out
/ across shards for un-scoped queries, and aggregates health + the
/ batch_sent/batch_arrived transit-lag metric for the dashboard.
/
/ Shard routing follows the same first-letter split used in KX's own
/ scalable ingestion blueprint: A-M -> shard 0, N-Z -> shard 1.

.gw.shardOf:{[sym] $[(upper first string sym) in .Q.A til 13; `A_M; `N_Z]}

/ registry: shard -> (rdb handle;idb handle;wdb handle), lazily opened
.gw.hosts:`A_M`N_Z!(
  `rdb`idb`wdb!(`$":rdb-a-m:5021";`$":idb-a-m:5031";`$":wdb-a-m:5041");
  `rdb`idb`wdb!(`$":rdb-n-z:5022";`$":idb-n-z:5032";`$":wdb-n-z:5042"))

.gw.conn:()!()

.gw.h:{[shard;tier]
  key_:` sv shard,tier;
  if[key_ in key .gw.conn; :.gw.conn key_];
  addr:.gw.hosts[shard][tier];
  h:@[hopen; addr; {[a;e] -2 "gateway: could not reach ",string[a],": ",e; 0N}[addr]];
  if[not null h; .gw.conn[key_]:h];
  h}

/ query trade or risk for a single symbol, checking IDB then RDB and
/ concatenating - the flush watermark guarantees no overlap between them
.gw.bySym:{[tbl;sym]
  shard:.gw.shardOf sym;
  rdbH:.gw.h[shard;`rdb]; idbH:.gw.h[shard;`idb];
  rdbRows:$[null rdbH; 0#([] time:`timestamp$()); rdbH ({[t;s] select from t where sym=s}; tbl; sym)];
  idbRows:$[null idbH; 0#([] time:`timestamp$()); idbH ({[t;s] select from t where sym=s}; tbl; sym)];
  `time xasc idbRows,rdbRows}

/ fan out across both shards for un-scoped queries (dashboards, demos)
.gw.all:{[tbl]
  raze {[tbl;shard]
    rdbH:.gw.h[shard;`rdb]; idbH:.gw.h[shard;`idb];
    rdbRows:$[null rdbH; 0#([] time:`timestamp$()); rdbH ({[t] select from t}; tbl)];
    idbRows:$[null idbH; 0#([] time:`timestamp$()); idbH ({[t] select from t}; tbl)];
    idbRows,rdbRows
    }[tbl] each key .gw.hosts}

.gw.health:{
  raze {[shard]
    rdbH:.gw.h[shard;`rdb]; idbH:.gw.h[shard;`idb]; wdbH:.gw.h[shard;`wdb];
    rdbStat:$[null rdbH; (`status`shard!(`down;shard)); @[rdbH;(`.rdb.health;::);{(`status`shard!(`down;x))}[shard]]];
    idbStat:$[null idbH; (`status`shard!(`down;shard)); @[idbH;(`.idb.health;::);{(`status`shard!(`down;x))}[shard]]];
    enlist `shard`rdb`idb!(shard;rdbStat;idbStat)
    }[] each key .gw.hosts}

/ per-table transit lag, sourced from each WDB's metrics table
.gw.transitLag:{
  raze {[shard]
    wdbH:.gw.h[shard;`wdb];
    if[null wdbH; :0#([] shard:`symbol$();table:`symbol$();metric:`symbol$();avgMs:`float$())];
    m:@[wdbH;({select avgMs:avg value from metrics where metric=`batch_arrived_lag_ms by table};::);
      {0#([] table:`symbol$();avgMs:`float$())}];
    update shard:shard from m
    }[] each key .gw.hosts}

.gw.query:{[tbl;sym]
  $[null sym; .gw.all tbl; .gw.bySym[tbl;sym]]
  }

-1 "[gateway] up, routing across shards: ",", " sv string key .gw.hosts;
