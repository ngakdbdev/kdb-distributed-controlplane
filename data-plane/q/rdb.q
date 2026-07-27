/ rdb.q - chained (query) real-time database for one shard
/ Subscribes to the tickerplant like the WDB does, but never writes to disk.
/ Its end-of-day handler just clears memory, because the WDB already
/ persisted everything. It drops rows below the flush watermark whenever
/ the tickerplant relays one, so it and the IDB stay disjoint.
/
/ SELF-HEALING: a container-level restart of the RDB itself is only half
/ the story - if the TICKERPLANT restarts (crash, watchdog-triggered
/ restart, node eviction), every RDB/WDB subscribed to it goes silently
/ stale unless it notices and resubscribes. This file polls its own
/ connection to the tickerplant on a timer and transparently reconnects +
/ resubscribes if it's gone, with backoff so a genuinely down tickerplant
/ doesn't get hammered with reconnect attempts.

\l schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
tpHost:first .u.getarg[args;`tphost;enlist "localhost"]
tpPort:"I"$first .u.getarg[args;`tpport;enlist "5010"]

.rdb.shard:`$shardId
.rdb.watermark:0Np
.rdb.startTime:.z.p
.rdb.tpHandle:0Ni                / 0Ni = "not connected" sentinel
.rdb.tpAddr:`$":",tpHost,":",string tpPort
.rdb.reconnectAttempts:0j
.rdb.lastReconnectAttempt:0Np

.rdb.upd:{[t;data]
  data:$[0>type data; enlist data; data];
  t insert update shard:.rdb.shard from data;
  }

/ called by the tickerplant relay after the WDB flushes - drop everything
/ already persisted so a gateway query across RDB+IDB never double-counts
.rdb.shedTo:{[w]
  .rdb.watermark::w;
  {[w;tbl] delete from tbl where time < w}[w] each `trade`risk;
  -1 "[rdb ",string[.rdb.shard],"] shed rows below ",string w;
  }

/ health endpoint the watchdog (and the gateway) polls
.rdb.health:{
  `status`shard`uptimeSec`rowsTrade`rowsRisk`watermark`tpConnected`reconnectAttempts!
    (`up;.rdb.shard;`long$(.z.p-.rdb.startTime)%1000000000;
     count trade; count risk; .rdb.watermark;
     not null .rdb.tpHandle; .rdb.reconnectAttempts)
  }

/ (re)establish the tickerplant connection and resubscribe both tables -
/ safe to call whether this is the first connection at startup or a
/ reconnect after the tp restarted. Protected eval: never crashes the RDB
/ itself even if the tickerplant is completely unreachable.
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

/ cheap liveness probe - if the handle is stale, this throws, which is our
/ signal to reconnect. `::` is a no-op round trip that touches the socket.
.rdb.tpIsAlive:{
  if[null .rdb.tpHandle; :0b];
  @[{.rdb.tpHandle (::); 1b}; ::; {0b}]
  }

/ runs every timer tick: if disconnected, back off (max 30s between
/ attempts) so a genuinely-down tickerplant doesn't get hammered
.rdb.everConnected:0b

.rdb.checkConnection:{
  if[.rdb.tpIsAlive[]; :()];
  if[.rdb.everConnected; -1 "[rdb ",string[.rdb.shard],"] lost connection to tickerplant, will reconnect"];
  .rdb.tpHandle::0Ni;
  backoff:0D00:00:01 * 2 xexp min[.rdb.reconnectAttempts;5];  / 1s,2s,4s,8s,16s,32s cap
  if[(0Np~.rdb.lastReconnectAttempt) or (.z.p - .rdb.lastReconnectAttempt) > backoff;
    .rdb.lastReconnectAttempt::.z.p;
    .rdb.reconnectAttempts+:1;
    if[not null .rdb.connectTp[]; .rdb.everConnected::1b];
    ];
  }

.z.ts:{.rdb.checkConnection[]}
\t 5000

/ the tickerplant calls `.u.upd` and relays `.u.shedTo` - mirror both
.u.upd:.rdb.upd
.u.shedTo:.rdb.shedTo

if[not null .rdb.connectTp[]; .rdb.everConnected::1b];

-1 "[rdb ",shardId,"] chained rdb up (self-healing tp connection enabled), target ",string .rdb.tpAddr;

