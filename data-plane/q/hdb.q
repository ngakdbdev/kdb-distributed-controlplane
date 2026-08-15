/ hdb.q - historical database for one shard
/ Memory-maps the partitioned, sym-enumerated directory that wdb seals a
/ trading day's data into at end of day (via .Q.dpft - see wdb.q's
/ .wdb.seal), and serves historical (pre-today) queries for the gateway.
/ Reloads on demand - wdb calls .hdb.reload right after sealing a new day -
/ or on its own timer as a fallback if that direct notification is ever
/ missed (network hiccup, hdb briefly down, ...).
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
/ 0 (default) = keep every sealed day forever. Purging history is a
/ deliberate, opt-in choice - it's destructive and irreversible (no cold-
/ storage archive step here, just delete), so it never happens unless
/ explicitly configured.
retentionDays:"I"$first .u.getarg[args;`retentiondays;enlist "0"]

.hdb.shard:`$shardId
.hdb.dir:hdbDir
.hdb.retentionDays:retentionDays
.hdb.lastReload:0Np
.hdb.lastPurge:0Np
.hdb.lastError:""

.hdb.reload:{
  system "mkdir -p ",.hdb.dir;
  @[{system "l ",.hdb.dir};
    ::;
    {[e] .hdb.lastError::e; -1 "[hdb ",string[.hdb.shard],"] reload failed: ",e}];
  .hdb.lastReload::.z.p;
  }

/ Deletes on-disk date-partition directories older than retentionDays.
/ Reads directory NAMES directly (not the currently-loaded table's distinct
/ dates) so it works even before the first reload has populated anything,
/ and so a partition that failed to load for some reason still gets purged
/ on schedule rather than silently surviving forever. Each date partition
/ is its own directory ("YYYY.MM.DD") per standard kdb+ partitioned-db
/ layout - `"D"$` parses that shape directly; anything that doesn't parse
/ as a date (a stray file, a different directory) is left alone.
.hdb.purgeOld:{
  if[.hdb.retentionDays=0; :()];
  cutoff:.z.d - .hdb.retentionDays;
  names:@[key;hsym `$.hdb.dir;`$()];
  parsed:{@[{"D"$x};x;0Nd]} each string names;
  old:names where (not null parsed) and parsed < cutoff;
  if[not count old; :()];
  {[dir;n]
    system "rm -rf ",dir,"/",string n;
    -1 "[hdb ",string[.hdb.shard],"] purged partition ",string[n]," (older than ",string[.hdb.retentionDays]," day retention)";
    }[.hdb.dir] each old;
  .hdb.lastPurge::.z.p;
  }

.hdb.dates:{
  / `exec date from trade` (the obvious way to ask a partitioned table
  / which dates it has) throws 'nyi' on this build - confirmed live
  / against real sealed data, reproducibly. .Q.pv (the partition-value
  / list \l itself populates when loading a partitioned directory) is the
  / same information without going through exec at all.
  $[`trade in tables`.; asc distinct .Q.pv; `date$()]
  }

.hdb.health:{
  dates:.hdb.dates[];
  `status`shard`partitions`oldestDate`newestDate`rowsTrade`rowsRisk`lastReload`lastError`retentionDays`lastPurge!
    (`up;.hdb.shard;count dates;
     $[count dates;min dates;0Nd];$[count dates;max dates;0Nd];
     $[`trade in tables`.;count trade;0j];$[`risk in tables`.;count risk;0j];
     .hdb.lastReload;.hdb.lastError;.hdb.retentionDays;.hdb.lastPurge)
  }

.z.ts:{.hdb.purgeOld[]; .hdb.reload[]}
\t 1000 * reloadSec

.hdb.purgeOld[]
.hdb.reload[]
-1 "[hdb ",shardId,"] historical db up, serving ",hdbDir,", reload every ",string[reloadSec],"s",
  $[retentionDays>0;", retention ",string[retentionDays]," day(s)";""];
