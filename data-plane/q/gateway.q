/ gateway.q - routes queries/health across every shard's tiers.
/ Learns shard count/routing entirely from the mounted shards.json (env
/ SHARDS_JSON, default /app/shards.json) - genuinely never hardcoded, so this
/ scales past 2 shards without touching this file.

.gw.addr:{[hp] `$":",hp}

.gw.loadConfig:{
  path:$[count getenv`SHARDS_JSON; getenv`SHARDS_JSON; "/app/shards.json"];
  .gw.cfg::.j.k raze read0 hsym `$path;
  rows:.gw.cfg`shards;
  ids:`$rows`id;
  .gw.shardRanges::([] id:ids; lo:first each rows`lo; hi:first each rows`hi);
  .gw.hosts::ids!{[r]
      (`tp`rdb`idb`wdb`hdb)!.gw.addr each (r`tp;r`rdb;r`idb;r`wdb;r`hdb)
    } each rows;
  -1 "[gateway] up, routing across ",string[count ids]," shards: ",", " sv string ids;
  }

.gw.conn:()!()

.gw.get:{[d;k;def] $[(k in key d); d k; def]}
.gw.msDiff:{[a;b] $[(null a) or (null b); 0n; (`float$`long$(a-b))%1000000f]}
.gw.msAge:{[a] $[null a; 0n; (`float$`long$(.z.p-a))%1000000f]}

.gw.downTier:{[shard]
  `status`shard`uptimeSec`rowsTrade`rowsRisk`lastTradeTs`lastRiskTs`watermark`tpConnected`reconnectAttempts!
    (`down;shard;0j;0j;0j;0Np;0Np;0Np;0b;0j)
  }

.gw.downTp:{[shard]
  `status`shard`recv`pub`dropped`errs`queueDepth`subLag`subs`lastSeq`lastTs`pubLatencyUs`logLatencyUs`logBytes!
    (`down;shard;0j;0j;0j;0j;0j;0j;0j;0j;0Np;0j;0j;0j)
  }

.gw.downIdb:{[shard]
  `status`shard`rowsTrade`rowsRisk`partitionsLoaded`retentionDays!(`down;shard;0j;0j;0j;0j)
  }

.gw.downWdb:{[shard]
  `status`shard`tpConnected`reconnectAttempts`lastWatermark`lastSealedDate!(`down;shard;0b;0j;0Np;0Nd)
  }

.gw.downHdb:{[shard]
  `status`shard`partitions`oldestDate`newestDate`rowsTrade`rowsRisk`lastReload`lastError!
    (`down;shard;0j;0Nd;0Nd;0j;0j;0Np;"")
  }

.gw.firstAlpha:{[sym] a:(upper string sym) inter .Q.A; $[count a; first a; " "]}

.gw.shardOf:{[sym]
  c:.gw.firstAlpha sym;
  m:select from .gw.shardRanges where lo<=c, c<=hi;
  $[count m; first m`id; first .gw.shardRanges`id]
  }

.gw.h:{[shard;tier]
  key_:` sv shard,tier;
  if[key_ in key .gw.conn; :.gw.conn key_];
  addr:.gw.hosts[shard][tier];
  h:@[hopen; addr; {[a;e] -1 "gateway: could not reach ",string[a],": ",e; 0N}[addr]];
  if[not null h; .gw.conn[key_]:h];
  h
  }

/ On a failed call, drop the cached handle before returning the fallback so
/ the NEXT fetch re-hopens instead of reusing a handle left stale by a
/ restart on the other end (.gw.h only hopens when the key is absent from
/ .gw.conn - without this, one restart on the remote side wedges that
/ shard/tier as permanently "down" until the gateway itself is restarted).
.gw.fetch:{[shard;tier;expr;fallback]
  h:.gw.h[shard;tier];
  $[null h; fallback;
    @[h;(expr;::);{[shard;tier;h;fb;e]
        key_:` sv shard,tier;
        .gw.conn:key_ _ .gw.conn;
        @[hclose;h;()];
        fb
      }[shard;tier;h;fallback]]]
  }

.gw.bySym:{[tbl;sym]
  shard:.gw.shardOf sym;
  rdbH:.gw.h[shard;`rdb];
  idbH:.gw.h[shard;`idb];
  hdbH:.gw.h[shard;`hdb];
  rdbRows:$[null rdbH; 0#([] time:`timestamp$()); rdbH ({[t;s] select from t where sym=s}; tbl; sym)];
  idbRows:$[null idbH; 0#([] time:`timestamp$()); idbH ({[t;s] select from t where sym=s}; tbl; sym)];
  / hdb rows carry a leading `date` column (partitioned-table artifact) that
  / rdb/idb rows don't - drop it so the three concatenate cleanly.
  hdbRows:$[null hdbH; 0#([] time:`timestamp$()); hdbH ({[t;s] delete date from select from t where sym=s}; tbl; sym)];
  `time xasc hdbRows,idbRows,rdbRows
  }

.gw.all:{[tbl]
  raze {[tbl;shard]
    rdbH:.gw.h[shard;`rdb];
    idbH:.gw.h[shard;`idb];
    hdbH:.gw.h[shard;`hdb];
    rdbRows:$[null rdbH; 0#([] time:`timestamp$()); rdbH ({[t] select from t}; tbl)];
    idbRows:$[null idbH; 0#([] time:`timestamp$()); idbH ({[t] select from t}; tbl)];
    hdbRows:$[null hdbH; 0#([] time:`timestamp$()); hdbH ({[t] delete date from select from t}; tbl)];
    hdbRows,idbRows,rdbRows
    }[tbl] each key .gw.hosts
  }

.gw.health:{
  {[shard]
    rdbStat:.gw.fetch[shard;`rdb;`.rdb.health; .gw.downTier shard];
    idbStat:.gw.fetch[shard;`idb;`.idb.health; .gw.downIdb shard];
    wdbStat:.gw.fetch[shard;`wdb;`.wdb.health; .gw.downWdb shard];
    hdbStat:.gw.fetch[shard;`hdb;`.hdb.health; .gw.downHdb shard];
    tpStat:.gw.fetch[shard;`tp;`.u.stats; .gw.downTp shard];
    `shard`tp`rdb`idb`wdb`hdb!(shard;tpStat;rdbStat;idbStat;wdbStat;hdbStat)
    } each key .gw.hosts
  }

.gw.transitLag:{
  raze {[shard]
    rows:([] shard:`symbol$(); table:`symbol$(); stage:`symbol$(); lagMs:`float$(); queueBytes:`long$(); rdbRows:`long$(); watermark:`timestamp$());
    tpStat:.gw.fetch[shard;`tp;`.u.stats; .gw.downTp shard];
    rdbStat:.gw.fetch[shard;`rdb;`.rdb.health; .gw.downTier shard];
    wdbStat:.gw.fetch[shard;`wdb;`.wdb.health; .gw.downWdb shard];
    tpLast:.gw.get[tpStat;`lastTs;0Np];
    tpQueue:`long$.gw.get[tpStat;`queueDepth;0j];
    wm:.gw.get[wdbStat;`lastWatermark;0Np];
    rdbTradeTs:.gw.get[rdbStat;`lastTradeTs;0Np];
    rdbRiskTs:.gw.get[rdbStat;`lastRiskTs;0Np];
    rdbTradeRows:`long$.gw.get[rdbStat;`rowsTrade;0j];
    rdbRiskRows:`long$.gw.get[rdbStat;`rowsRisk;0j];
    rows,:enlist (shard;`trade;`feed_to_tp;.gw.msAge tpLast;tpQueue;rdbTradeRows;wm);
    rows,:enlist (shard;`trade;`tp_to_rdb;.gw.msDiff[tpLast;rdbTradeTs];tpQueue;rdbTradeRows;wm);
    rows,:enlist (shard;`trade;`rdb_to_gateway;.gw.msAge rdbTradeTs;tpQueue;rdbTradeRows;wm);
    rows,:enlist (shard;`trade;`tp_to_wdb_flush;.gw.msDiff[tpLast;wm];tpQueue;rdbTradeRows;wm);
    rows,:enlist (shard;`risk;`feed_to_tp;.gw.msAge tpLast;tpQueue;rdbRiskRows;wm);
    rows,:enlist (shard;`risk;`tp_to_rdb;.gw.msDiff[tpLast;rdbRiskTs];tpQueue;rdbRiskRows;wm);
    rows,:enlist (shard;`risk;`rdb_to_gateway;.gw.msAge rdbRiskTs;tpQueue;rdbRiskRows;wm);
    rows,:enlist (shard;`risk;`tp_to_wdb_flush;.gw.msDiff[tpLast;wm];tpQueue;rdbRiskRows;wm);
    rows
    } each key .gw.hosts
  }

.gw.componentMetrics:{
  ([] shard:`symbol$();
      tpRecv:`long$(); tpPub:`long$(); tpQueue:`long$(); tpSubLag:`long$(); tpPubLatencyUs:`long$(); tpLogLatencyUs:`long$();
      rdbRowsTrade:`long$(); rdbRowsRisk:`long$(); rdbReconnects:`long$(); rdbConnected:`boolean$();
      wdbConnected:`boolean$(); wdbReconnects:`long$(); wdbLastWatermark:`timestamp$();
      hdbConnected:`boolean$(); hdbPartitions:`long$());
  raze {[shard]
    tpStat:.gw.fetch[shard;`tp;`.u.stats; .gw.downTp shard];
    rdbStat:.gw.fetch[shard;`rdb;`.rdb.health; .gw.downTier shard];
    wdbStat:.gw.fetch[shard;`wdb;`.wdb.health; .gw.downWdb shard];
    hdbStat:.gw.fetch[shard;`hdb;`.hdb.health; .gw.downHdb shard];
    enlist (shard;
            `long$.gw.get[tpStat;`recv;0j]; `long$.gw.get[tpStat;`pub;0j]; `long$.gw.get[tpStat;`queueDepth;0j]; `long$.gw.get[tpStat;`subLag;0j];
            `long$.gw.get[tpStat;`pubLatencyUs;0j]; `long$.gw.get[tpStat;`logLatencyUs;0j];
            `long$.gw.get[rdbStat;`rowsTrade;0j]; `long$.gw.get[rdbStat;`rowsRisk;0j]; `long$.gw.get[rdbStat;`reconnectAttempts;0j]; not null .gw.h[shard;`rdb];
            `boolean$.gw.get[wdbStat;`tpConnected;0b]; `long$.gw.get[wdbStat;`reconnectAttempts;0j]; .gw.get[wdbStat;`lastWatermark;0Np];
            not null .gw.h[shard;`hdb]; `long$.gw.get[hdbStat;`partitions;0j])
    } each key .gw.hosts
  }

.gw.topology:{
  t:.gw.cfg`shards;
  ([] id:`$t`id; label:t`label; lo:t`lo; hi:t`hi)
  }

.gw.query:{[tbl;sym] $[null sym; .gw.all tbl; .gw.bySym[tbl;sym]]}

.gw.loadConfig[]
