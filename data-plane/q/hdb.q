/ hdb.q - historical database for one shard
/ Memory-maps the partitioned, sym-enumerated directory that wdb seals a
/ trading day's data into at end of day (via .Q.dpft - see wdb.q's
/ .wdb.seal), and serves historical (pre-today) queries for the gateway.
/ Reloads on demand - wdb calls .hdb.reload right after sealing a new day -
/ or on its own timer as a fallback if that direct notification is ever
/ missed (network hiccup, hdb briefly down, ...).
/
/ Before the first end-of-day seal ever happens (a brand new TickHouse, day
/ one), the directory has no date partitions yet: \l on it is a safe no-op
/ (tested directly against this build), so trade/risk just stay the empty
/ schema.q placeholders until there's real history to serve.

\l /app/schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
hdbDir:first .u.getarg[args;`hdbdir;enlist "./hdb"]
reloadSec:"I"$first .u.getarg[args;`reloadsec;enlist "60"]

.hdb.shard:`$shardId
.hdb.dir:hdbDir
.hdb.lastReload:0Np
.hdb.lastError:""

.hdb.reload:{
  system "mkdir -p ",.hdb.dir;
  @[{system "l ",.hdb.dir};
    ::;
    {[e] .hdb.lastError::e; -1 "[hdb ",string[.hdb.shard],"] reload failed: ",e}];
  .hdb.lastReload::.z.p;
  }

.hdb.dates:{
  $[(`trade in tables`.) and (`date in cols trade); distinct exec date from trade; `date$()]
  }

.hdb.health:{
  dates:.hdb.dates[];
  `status`shard`partitions`oldestDate`newestDate`rowsTrade`rowsRisk`lastReload`lastError!
    (`up;.hdb.shard;count dates;
     $[count dates;min dates;0Nd];$[count dates;max dates;0Nd];
     $[`trade in tables`.;count trade;0j];$[`risk in tables`.;count risk;0j];
     .hdb.lastReload;.hdb.lastError)
  }

.z.ts:{.hdb.reload[]}
\t 1000 * reloadSec

.hdb.reload[]
-1 "[hdb ",shardId,"] historical db up, serving ",hdbDir,", reload every ",string[reloadSec],"s";
