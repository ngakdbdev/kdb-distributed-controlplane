\l /app/schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
tpHost:first .u.getarg[args;`tphost;enlist "localhost"]
tpPort:"I"$first .u.getarg[args;`tpport;enlist "5010"]
dbDir:first .u.getarg[args;`dbdir;enlist "/data/db"]

.rdb.shard:`$shardId
.rdb.watermark:0Np
.rdb.startTime:.z.p
.rdb.tpHandle:0Ni
.rdb.tpAddr:`$":",tpHost,":",string tpPort
.rdb.dbDir:dbDir
.rdb.reconnectAttempts:0j
.rdb.lastReconnectAttempt:0Np

.rdb.upd:{[t;data]
  data:$[0>type data; enlist data; data];
  rows:update shard:.rdb.shard from data;
  if[not null .rdb.watermark; rows:select from rows where time > .rdb.watermark];
  if[count rows; t insert rows];
  }

.rdb.shedTo:{[w]
  .rdb.watermark::w;
  {[w;tbl] delete from tbl where time < w}[w] each `trade`risk;
  -1 "[rdb ",string[.rdb.shard],"] shed rows below ",string w;
  }

/ end-of-day: this chained rdb already sheds continuously as wdb broadcasts
/ its rolling watermark, so by end of day it should hold little to nothing
/ for the closing date - this is the defensive guarantee that it holds
/ NONE, immediately, rather than waiting on wdb's own eod watermark
/ broadcast to arrive. That broadcast still comes (belt-and-suspenders) but
/ isn't required for rdb to be clear of day `d` the moment the day closes.
.rdb.eod:{[d]
  cutoff:`timestamp$d+1;
  .rdb.shedTo[cutoff];
  -1 "[rdb ",string[.rdb.shard],"] end-of-day clear complete through ",string d;
  }

.rdb.health:{
  lt:$[count trade; max trade`time; 0Np];
  lr:$[count risk; max risk`time; 0Np];
  `status`shard`uptimeSec`rowsTrade`rowsRisk`lastTradeTs`lastRiskTs`watermark`tpConnected`reconnectAttempts!
    (`up;.rdb.shard;`long$(.z.p-.rdb.startTime)%1000000000;
     count trade; count risk; lt; lr; .rdb.watermark;
     not null .rdb.tpHandle; .rdb.reconnectAttempts)
  }

.rdb.loadWarm:{
  d:`date$.z.p;
  loaded:0j;
  {[d;tbl]
    f:` sv hsym[`$.rdb.dbDir],`$string d,tbl;
    rows:@[get;f;0#value tbl];
    if[count rows;
      tbl insert rows;
      loaded+:count rows;
      if[null .rdb.watermark; .rdb.watermark::max rows`time; .rdb.watermark::.rdb.watermark|max rows`time];
      -1 "[rdb ",string[.rdb.shard],"] warm-loaded ",string[count rows]," rows from ",string f;
      ];
    }[d] each `trade`risk;
  if[loaded>0; -1 "[rdb ",string[.rdb.shard],"] warm start complete (rows=",string loaded,") watermark=",string .rdb.watermark];
  }

.rdb.connectTp:{
  res:@[
    {
      h:hopen .rdb.tpAddr;
      h (`.u.sub;`trade;`);
      h (`.u.sub;`risk;`);
      h
      };
    ::;
    {[e] -1 "[rdb ",string[.rdb.shard],"] tp connect/resubscribe failed: ",e; 0Ni}
    ];
  if[not null res;
    .rdb.tpHandle::res;
    .rdb.reconnectAttempts::0;
    -1 "[rdb ",string[.rdb.shard],"] connected + subscribed to tp at ",string .rdb.tpAddr;
    ];
  res
  }

.rdb.tpIsAlive:{
  if[null .rdb.tpHandle; :0b];
  @[{.rdb.tpHandle (::); 1b}; ::; {0b}]
  }

.rdb.everConnected:0b

.rdb.checkConnection:{
  if[.rdb.tpIsAlive[]; :()];
  if[.rdb.everConnected; -1 "[rdb ",string[.rdb.shard],"] lost connection to tickerplant, will reconnect"];
  .rdb.tpHandle::0Ni;
  backoff:0D00:00:01 * 2 xexp (.rdb.reconnectAttempts & 5);
  if[(0Np~.rdb.lastReconnectAttempt) or (.z.p - .rdb.lastReconnectAttempt) > backoff;
    .rdb.lastReconnectAttempt::.z.p;
    .rdb.reconnectAttempts+:1;
    if[not null .rdb.connectTp[]; .rdb.everConnected::1b];
    ];
  }

.z.ts:{.rdb.checkConnection[]}
\t 5000

.u.upd:.rdb.upd
.u.shedTo:.rdb.shedTo
.u.eod:.rdb.eod

.rdb.loadWarm[]

if[not null .rdb.connectTp[]; .rdb.everConnected::1b];

-1 "[rdb ",shardId,"] chained rdb up, target ",string .rdb.tpAddr;

