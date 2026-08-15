\l /app/schema.q

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
tpHost:first .u.getarg[args;`tphost;enlist "localhost"]
tpPort:"I"$first .u.getarg[args;`tpport;enlist "5010"]
flushIntv:"I"$first .u.getarg[args;`flushmin;enlist "2"]
/ how long the chained RDB keeps live data in memory, independent of how
/ often THIS process flushes its own buffer to disk (flushmin) - defaults to
/ flushmin for backward compatibility (old behavior: retention == flush
/ cadence). Must be >= flushmin: RDB can only safely shed what's already
/ been flushed to the scratch file, so asking it to retain LESS than the
/ flush cadence would shed data that was never durably written yet.
retentionIntv:"I"$first .u.getarg[args;`retentionmin;enlist string flushIntv]
if[retentionIntv<flushIntv;
  -1 "wdb: -retentionmin (",string[retentionIntv],") < -flushmin (",string[flushIntv],") - retention cannot be shorter than the flush cadence, using flushmin";
  retentionIntv:flushIntv];
dbDir:first .u.getarg[args;`dbdir;enlist "./db"]
hdbDir:first .u.getarg[args;`hdbdir;enlist "./hdb"]
/ same UTC-hour trading-day boundary tick.q's own -eodhour uses (see
/ .u.tradingDate there) - needed here too now that EOD catch-up (below)
/ has to independently know "today" rather than only ever hearing it from
/ the tickerplant's own broadcast.
eodHour:"I"$first .u.getarg[args;`eodhour;enlist "0"]
idbHost:first .u.getarg[args;`idbhost;enlist ""]
idbPort:"I"$first .u.getarg[args;`idbport;enlist "0"]
hdbHost:first .u.getarg[args;`hdbhost;enlist ""]
hdbPort:"I"$first .u.getarg[args;`hdbport;enlist "0"]

.wdb.shard:`$shardId
.wdb.dbDir:dbDir
.wdb.hdbDir:hdbDir
.wdb.eodHour:eodHour
.wdb.tradingDate:{`date$(.z.p - .wdb.eodHour*0D01:00:00)}
.wdb.idbAddr:$[count idbHost;`$":",idbHost,":",string idbPort;`]
.wdb.hdbAddr:$[count hdbHost;`$":",hdbHost,":",string hdbPort;`]
.wdb.flushIntv:flushIntv
.wdb.retentionIntv:retentionIntv
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

/ Two cutoffs, deliberately different: flushCutoff bounds THIS process's own
/ memory (rows older than flushIntv move from wdb's buffer to the scratch
/ file); retentionCutoff is what gets broadcast as the shed watermark, and
/ bounds the chained RDB's memory instead (rows older than retentionIntv get
/ deleted there). retentionIntv >= flushIntv is enforced at startup, so
/ retentionCutoff is always <= flushCutoff - RDB is only ever told to shed
/ data that's already safely on disk by the time it sheds it.
.wdb.flush:{
  flushCutoff:.z.p - .wdb.flushIntv * 0D00:01;
  .wdb.flushTo[flushCutoff];
  retentionCutoff:.z.p - .wdb.retentionIntv * 0D00:01;
  .wdb.lastWatermark::retentionCutoff;
  h:@[hopen; .wdb.tpAddr; {[e] -1 "wdb: could not reach tp to broadcast watermark: ",e; 0Ni}];
  if[not null h; h (`.u.broadcastWatermark; retentionCutoff); hclose h];
  -1 "[wdb ",shardId,"] flushed at ",string[flushCutoff]," (rdb retention watermark ",string[retentionCutoff],")";
  }

/ seal one closed date's scratch file into a real partitioned HDB segment:
/ sort by sym, enumerate + splay via .Q.dpft, then retire the scratch file.
/ .Q.dpft's 4th arg is BOTH the source var it reads AND the destination
/ partition name it writes - a fixed placeholder there (this function's
/ original form used `sealtmp) means every seal ever produced a partition
/ literally named "sealtmp" on disk, never trade/risk. tbl is bound under
/ its real name via set[] (needed because the name is computed, not
/ literal) so .Q.dpft's own output is correctly named.
.wdb.seal:{[d]
  {[d;tbl]
    f:.wdb.scratchFile[d;tbl];
    ddir:.wdb.dbDir,"/",string d;
    / always splay BOTH tables, even with 0 rows (no scratch file at all, or
    / one that's empty) - confirmed live: a date with real trade but zero
    / risk events (a genuinely common case - not every day sees a risk
    / breach) got a trade splay but no risk splay at all, and that missing
    / risk partition then broke plain "count risk"/"select from risk" with
    / no date filter for EVERY OTHER date too, not just the sparse one -
    / kdb+'s partitioned aggregation expects every table present (even as
    / an empty splay) in every partition it knows about.
    hasScratch:tbl in key hsym `$ddir;
    rows:$[hasScratch; get f; 0#get tbl];
    set[tbl; `sym xasc rows];
    / defensively clear any STALE splay left in the target directory by a
    / PREVIOUSLY INTERRUPTED seal for this exact date/table - confirmed
    / live: an earlier crash mid-.Q.dpft left exactly this kind of
    / leftover (real column data, never fully finalized), and a later
    / retry colliding with it made .Q.dpft hang INDEFINITELY rather than
    / error (discovered via EOD catch-up below finally re-attempting a
    / date nothing had retried in days). A stale target dir is by
    / definition incomplete garbage - .Q.dpft owns creating and retiring
    / its own temp state on every successful run, so nothing legitimate is
    / ever relying on one still being there before a fresh attempt.
    staleTarget:.wdb.hdbDir,"/",string[d],"/",string[tbl];
    @[system; "rm -rf ",staleTarget; {[p;e] -1 "[wdb] could not clear stale target at ",p,": ",e}[staleTarget]];
    @[{[dir;dt;tb] .Q.dpft[dir;dt;`sym;tb]}[hsym `$.wdb.hdbDir;d];
      tbl;
      {[tbl;d;e] -1 "[wdb] EOD seal FAILED for ",string[tbl]," on ",string[d],": ",e}[tbl;d]];
    ![`.;();0b;enlist tbl];
    if[hasScratch; hdel f];
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

/ ------------------------------------------------------ EOD catch-up (self-heal)
/ .wdb.eod only ever fires from the tickerplant's live broadcast (tick.q's
/ .z.ts timer, sent once over an async handle - fire-and-forget, no ack,
/ no retry). If this process wasn't connected/subscribed at that exact
/ moment - a reconnect cycle, a restart, or tick.q's own slow-subscriber
/ shedding (.u.dropSub) disconnecting it - the broadcast is gone for good.
/ Confirmed live against this deployment: SIX consecutive days of scratch
/ files sat unsealed in dbDir with nothing logged anywhere - a missed EOD
/ was completely invisible until someone noticed HDB/IDB had no data for
/ dates that should have been sealed days earlier.
/ Run at startup and on a periodic check (throttled in .z.ts below) so a
/ missed broadcast is never permanent - the same "self-triggered, no
/ external cron dependency" principle tick.q's own EOD check already uses
/ for the forward-triggering half of this. Seals AT MOST ONE backlogged
/ date per call, deliberately: .Q.dpft splaying a full day's scratch file
/ blocks this single-threaded process for its whole duration (confirmed
/ live: one day's file here was 1.7GB), and catching up several days back
/ to back risks starving the live tick stream long enough that tick.q's
/ own slow-subscriber shedding disconnects this process mid-catch-up -
/ reproducing the exact failure mode this function exists to recover from.
/ The periodic check keeps re-running, so a multi-day backlog drains one
/ date per interval rather than all at once.
/ does date `d` still have an actual unsealed scratch file (trade or risk)
/ present? A backlog date directory can exist but be genuinely EMPTY - both
/ tables already sealed+retired normally, OR (confirmed live, see the
/ .wdb.seal comment above) their source scratch was deleted by an OLDER,
/ now-lost attempt that never completed its .Q.dpft splay, meaning that
/ table's data for that date is unrecoverable. Either way there is nothing
/ left TO seal for it - it must not be selected as "the" backlogged date
/ forever, which would starve real progress through the rest of a
/ multi-date backlog (a plain directory-existence check did exactly that
/ live: an already-empty 08.06 kept winning "oldest backlog date" over a
/ genuinely-unsealed, much more recent date on the same shard).
.wdb.hasScratch:{[d]
  ddir:.wdb.dbDir,"/",string d;
  entries:@[key; hsym `$ddir; {`$()}];
  any `trade`risk in entries
  }
.wdb.catchUpSeals:{
  today:.wdb.tradingDate[];
  entries:@[key; hsym `$.wdb.dbDir; {`$()}];
  if[0=count entries; :()];
  dates:{@[{"D"$string x};x;{0Nd}]} each entries;   / non-date entries (e.g. stray files) -> null, filtered below
  dates:dates where not null dates;
  backlog:asc dates where dates<today;
  backlog:backlog where .wdb.hasScratch each backlog;
  if[0=count backlog; :()];
  d:first backlog;
  -1 "[wdb ",shardId,"] EOD catch-up: sealing backlogged date ",string[d]," - not sealed by the normal broadcast (",string[count backlog]," date(s) still behind after this one)";
  .wdb.seal[d];
  / .wdb.eod notifies hdb/idb right after sealing so the new day is
  / queryable immediately rather than waiting on hdb's own reload timer -
  / catch-up sealing skipped this same step (called .wdb.seal directly),
  / confirmed live: a freshly caught-up date sat sealed-but-invisible on
  / disk until hdb's periodic reload eventually picked it up on its own.
  .wdb.notify[.wdb.hdbAddr; (`.hdb.reload;::)];
  .wdb.notify[.wdb.idbAddr; (`.idb.dropDate;d)];
  }
.wdb.lastCatchUpCheck:0Np

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
  / throttled to every 10 min - see .wdb.catchUpSeals' own comment on why
  / this seals at most one backlogged date per call rather than draining a
  / whole backlog immediately.
  if[(0Np~.wdb.lastCatchUpCheck) or (.z.p - .wdb.lastCatchUpCheck) > 0D00:10;
    .wdb.lastCatchUpCheck::.z.p;
    .wdb.catchUpSeals[];
    ];
  }
\t 5000

.u.upd:.wdb.upd
.u.shedTo:.wdb.shedTo
.u.eod:.wdb.eod

if[not null .wdb.connectTp[]; .wdb.everConnected::1b];
.wdb.lastCatchUpCheck::.z.p;
.wdb.catchUpSeals[];

-1 "[wdb ",shardId,"] write-down db up, flush every ",string[flushIntv]," min, writing to ",dbDir,", sealing to ",hdbDir," at end of day";
