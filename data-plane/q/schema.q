/ schema.q - shared table schemas, loaded by every process in the chain
/ Two source tables: `trade` (B-PIPE-style equities ticks) and `risk` (CRIMS-style reference/risk records)

trade:([] time:`timestamp$(); sym:`symbol$(); price:`float$(); size:`long$();
          side:`symbol$(); venue:`symbol$(); shard:`symbol$())

risk:([] time:`timestamp$(); sym:`symbol$(); riskType:`symbol$(); limit:`float$();
         exposure:`float$(); status:`symbol$(); shard:`symbol$())

/ tables published by the tickerplant, and the shared sym enumeration domain
.u.tabs:`trade`risk
