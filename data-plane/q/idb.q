/ idb.q - intraday database for one shard
/ Polls the WDB's on-disk output directory and loads newly flushed
/ int-partitions into memory so they are queryable between the time
/ they leave the chained RDB and the time the full HDB day-partition
/ is built at end of day.

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
dbDir:first .u.getarg[args;`dbdir;enlist "./db"]
pollSec:"I"$first .u.getarg[args;`pollsec;enlist "15"]

.idb.shard:`$shardId
.idb.dbDir:dbDir
.idb.loaded:()

if[not `trade in tables`.; trade:0#([] time:`timestamp$(); sym:`symbol$();
    price:`float$(); size:`long$(); side:`symbol$(); venue:`symbol$(); shard:`symbol$())]
if[not `risk in tables`.; risk:0#([] time:`timestamp$(); sym:`symbol$();
    riskType:`symbol$(); limit:`float$(); exposure:`float$(); status:`symbol$(); shard:`symbol$())]

.idb.scan:{
  if[not `$.idb.dbDir in key `.; :()];
  dates:key hsym `$.idb.dbDir;
  {[d]
    tdir:.idb.dbDir,"/",string d;
    if[not `$tdir in key `.; :()];
    {[d;tbl]
      f:` sv hsym[`$.idb.dbDir],`$string d,`$tbl;
      key_:string[d],"/",string tbl;
      if[key_ in .idb.loaded; :()];
      if[not (` sv hsym[`$.idb.dbDir],`$string d,`$tbl) in key hsym `$.idb.dbDir;:()];
      new:@[get;f;()];
      if[0=count new; :()];
      tbl insert new;
      .idb.loaded,:key_;
      -1 "[idb ",string[.idb.shard],"] loaded ",string[count new]," rows into ",
         string[tbl]," from ",key_;
      }[d] each `trade`risk;
    } each dates;
  }

.idb.health:{
  `status`shard`rowsTrade`rowsRisk`partitionsLoaded!
    (`up;.idb.shard;count trade;count risk;count .idb.loaded)
  }

.z.ts:{.idb.scan[]}
\t 1000 * pollSec

-1 "[idb ",shardId,"] intraday db up, polling ",dbDir," every ",string[pollSec],"s";
