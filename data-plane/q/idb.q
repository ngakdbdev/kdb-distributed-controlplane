/ idb.q - intraday database for one shard
/ Polls the WDB's on-disk output directory and loads newly flushed
/ int-partitions into memory so they are queryable between the time
/ they leave the chained RDB and the time wdb seals the full HDB
/ day-partition at end of day. Once a day is sealed, wdb tells idb to
/ drop its now-redundant in-memory copy (retention-days eviction below
/ is the fallback if that notification is missed).

.u.getarg:{[a;k;def] $[k in key a; a k; def]}

args:.Q.opt .z.x
shardId:first .u.getarg[args;`shard;enlist "A_M"]
dbDir:first .u.getarg[args;`dbdir;enlist "./db"]
pollSec:"I"$first .u.getarg[args;`pollsec;enlist "15"]
retentionDays:"I"$first .u.getarg[args;`retentiondays;enlist "5"]

.idb.shard:`$shardId
.idb.dbDir:dbDir
.idb.retentionDays:retentionDays
.idb.loaded:()

if[not `trade in tables`.; trade:0#([] time:`timestamp$(); sym:`symbol$();
    price:`float$(); size:`long$(); side:`symbol$(); venue:`symbol$(); shard:`symbol$())]
if[not `risk in tables`.; risk:0#([] time:`timestamp$(); sym:`symbol$();
    riskType:`symbol$(); limit:`float$(); exposure:`float$(); status:`symbol$(); shard:`symbol$())]

.idb.scan:{
  @[{
    dates:key hsym `$.idb.dbDir;
    today:.z.d;
    {[today;d]
      tdir:.idb.dbDir,"/",string d;
      / today's scratch file keeps growing (wdb rewrites it with the full
      / cumulative day on every rolling flush) right up until end-of-day
      / sealing - loading it once and never again would freeze idb's view
      / of "today" at whatever partial batch existed on the first poll,
      / opening a real gap once rdb sheds those rows past its own short
      / window and before wdb's eod seal makes them queryable via hdb. So
      / today's date is always re-synced; only already-closed dates (a
      / prior day's file that for some reason wasn't sealed/cleaned up) are
      / cached as load-once.
      / d is a symbol (a directory name from `key hsym`), today is a real
      / date from .z.d - `date$d fails outright on a symbol, and `date$
      / string d silently mis-casts (distributes over characters, one date
      / per char) rather than parsing "YYYY.MM.DD" as a whole. "D"$string d
      / is the form proven earlier in this file to parse correctly.
      isOpen:("D"$string d)=today;
      / tdir/isOpen are passed explicitly rather than relied on as closure
      / captures - q lambdas nested inside another lambda do NOT see that
      / lambda's locals (only globals and their own params), unlike most
      / languages - a real trap, confirmed directly against this build
      / ("tdir" surfaced as an undefined-name error from the inner scope).
      {[d;tdir;isOpen;tbl]
        f:` sv hsym[`$.idb.dbDir],(`$string d),tbl;
        key_:string[d],"/",string tbl;
        / `in` distributes over key_'s characters instead of testing it as
        / one string when .idb.loaded is an untyped empty list `()` - also
        / confirmed directly. any ~/: is safe whether .idb.loaded is empty
        / or populated.
        alreadyLoaded:any key_~/:.idb.loaded;
        if[(not isOpen) and alreadyLoaded; :()];
        if[not tbl in key hsym `$tdir; :()];
        new:@[get;f;()];
        if[0=count new; :()];
        / re-sync: drop this date's rows before reinserting the fresh full
        / read, so re-scanning the still-growing file doesn't duplicate. d
        / is still the directory-name symbol here, not a date - parse it
        / the same proven way as isOpen above rather than comparing a date
        / column straight against a symbol.
        if[isOpen; delete from tbl where (`date$time)=("D"$string d)];
        tbl insert new;
        / bare ,:key_ FLATTENS key_'s characters into .idb.loaded instead of
        / appending it as one element (`,` between two plain lists
        / concatenates rather than nests) - confirmed directly against this
        / build. enlist is required to append key_ as a single entry.
        if[not alreadyLoaded; .idb.loaded,:enlist key_];
        -1 "[idb ",string[.idb.shard],"] loaded ",string[count new]," rows into ",
           string[tbl]," from ",key_,$[isOpen;" (open day, re-synced)";""];
        }[d;tdir;isOpen] each `trade`risk;
      }[today] each dates;
    };
    ::;
    {[e] -1 "[idb ",string[.idb.shard],"] scan skipped (",e,")"}];
  }

.idb.health:{
  `status`shard`rowsTrade`rowsRisk`partitionsLoaded`retentionDays!
    (`up;.idb.shard;count trade;count risk;count .idb.loaded;.idb.retentionDays)
  }

/ called directly by wdb right after it seals a day into the hdb - that data
/ is now durably queryable there, so idb's in-memory copy is redundant.
.idb.dropDate:{[d]
  {[d;tbl] delete from tbl where (`date$time)=d}[d] each `trade`risk;
  prefix:string[d],"/";
  keep:{[p;s] not p~(count p)#s}[prefix] each .idb.loaded;
  .idb.loaded:.idb.loaded where keep;
  -1 "[idb ",string[.idb.shard],"] dropped date ",string[d]," (sealed into hdb)";
  }

/ fallback safety net for whenever wdb's direct notify is missed (idb was
/ down at seal time, the call failed, ...): nothing should sit in idb's
/ memory forever, so age out anything older than the retention window too.
.idb.evictOld:{
  cutoff:.z.d - .idb.retentionDays;
  loadedDates:distinct "D"$first each "/" vs/: .idb.loaded;
  old:loadedDates where loadedDates < cutoff;
  .idb.dropDate each old;
  }

.z.ts:{
  .idb.scan[];
  .idb.evictOld[];
  }
\t 1000 * pollSec

-1 "[idb ",shardId,"] intraday db up, polling ",dbDir," every ",string[pollSec],"s";
