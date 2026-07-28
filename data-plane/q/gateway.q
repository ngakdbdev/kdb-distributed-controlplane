/ gateway.q - single entry point for the control API and any client.
/ Routes symbol-scoped queries to the right shard's RDB+IDB, fans out
/ across shards for un-scoped queries, and aggregates health + the
/ batch_sent/batch_arrived transit-lag metric for the dashboard.
/
/ SHARD TOPOLOGY IS NO LONGER HARDCODED. At startup the gateway reads a
/ shards.json document (generated from the single shard-count knob by
/ app.topology / scripts/gen_topology.py, mounted here as a ConfigMap on
/ k8s or a bind mount under compose) and builds its routing table and host
/ map from it. This is what makes N-way sharding real: change the shard
/ count, regenerate shards.json, and the gateway routes across N shards
/ with no code change. If the file is missing or unparseable it falls back
/ to the built-in 2-shard A-M / N-Z topology so it always comes up.

/ ---------------------------------------------------------------- topology load
.gw.shardsFile:$[count getenv `SHARDS_JSON; getenv `SHARDS_JSON; "/app/shards.json"]

/ built-in fallback (2 shards, uniform ports) - matches app.topology at N=2
.gw.fallback:(
  `id`label`lo`hi`rdb`idb`wdb!("s0";"A-M";"A";"M";"rdb-s0:5020";"idb-s0:5030";"wdb-s0:5040");
  `id`label`lo`hi`rdb`idb`wdb!("s1";"N-Z";"N";"Z";"rdb-s1:5020";"idb-s1:5030";"wdb-s1:5040"))

.gw.loadTopology:{
  parsed:@[
    {[f] .j.k raze read0 hsym `$f};
    .gw.shardsFile;
    {[e] -1 "[gateway] could not read/parse ",.gw.shardsFile,": ",e,
            " - falling back to built-in 2-shard topology"; (::)}
    ];
  shards:$[(::)~parsed; .gw.fallback; parsed`shards];
  / normalise: each element is a dict with string values
  shards
  }

.gw.shards:.gw.loadTopology[]

/ id (symbol) -> `rdb`idb`wdb!(address syms), lazily hopened via .gw.h
.gw.hosts:(!) . flip {[s]
  addr:{[hp] `$":",hp};
  (`$s`id; `rdb`idb`wdb!(addr s`rdb; addr s`idb; addr s`wdb))
  } each .gw.shards

/ routing table: first-letter range -> shard id, for symbol-scoped queries
.gw.routes:{[s] `lo`hi`id!(first s`lo; first s`hi; `$s`id)} each .gw.shards
.gw.routes:$[0=count .gw.routes; ([] lo:"A"; hi:"Z"; id:`s0); (uj/) enlist each .gw.routes]

/ first A-Z letter of a symbol (skipping digits/punctuation); ' ' if none,
/ which falls through to the first shard - mirrors app.topology.shard_of
.gw.firstAlpha:{[sym] a:(upper string sym) inter .Q.A; $[count a; first a; " "]}

.gw.shardOf:{[sym]
  c:.gw.firstAlpha sym;
  hit:select from .gw.routes where lo<=c, c<=hi;
  $[count hit; first hit`id; first .gw.routes`id]}

/ ---------------------------------------------------------------- connections
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

/ fan out across all shards for un-scoped queries (dashboards, demos)
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

/ topology, exposed so the control API / UI can show which shard owns what
.gw.topology:{
  ([] id:`$.gw.shards@\:`id; label:.gw.shards@\:`label;
      lo:.gw.shards@\:`lo; hi:.gw.shards@\:`hi)}

.gw.query:{[tbl;sym]
  $[null sym; .gw.all tbl; .gw.bySym[tbl;sym]]
  }

-1 "[gateway] up, routing across ",string[count .gw.hosts]," shards: ",", " sv string key .gw.hosts;
