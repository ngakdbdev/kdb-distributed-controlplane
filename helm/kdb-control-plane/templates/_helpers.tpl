{{/*
Shard topology helpers.

Per-shard Deployments/Services/PVCs only need the shard INDEX ids (s0..s{N-1})
and uniform per-tier ports, so those templates just `range until .Values.shardCount`
directly - no range math needed.

The gateway's shards.json is the one artifact that needs the letter-range
partition, so it's computed here in Sprig, mirroring app.topology.letter_ranges:
partition A-Z into N contiguous ranges, the first (26 mod N) getting one extra
letter. scripts/check_topology_sync.py renders this and diffs it against the
Python module so the two implementations can't silently drift.
*/}}

{{- define "kdb-control-plane.shardsJson" -}}
{{- $n := int .Values.shardCount -}}
{{- if or (lt $n 1) (gt $n 26) -}}
{{- fail (printf "shardCount must be between 1 and 26, got %d" $n) -}}
{{- end -}}
{{- $alphabet := "ABCDEFGHIJKLMNOPQRSTUVWXYZ" -}}
{{- $base := div 26 $n -}}
{{- $extra := mod 26 $n -}}
{{- $shards := list -}}
{{- range $i := until $n -}}
  {{- $size := add $base (ternary 1 0 (lt $i $extra)) -}}
  {{- $offset := add (mul $i $base) (min $i $extra) -}}
  {{- $lo := substr $offset (add $offset 1) $alphabet -}}
  {{- $hi := substr (sub (add $offset $size) 1) (add $offset $size) $alphabet -}}
  {{- $label := ternary $lo (printf "%s-%s" $lo $hi) (eq $lo $hi) -}}
  {{- $shard := dict
        "id"    (printf "s%d" $i)
        "label" $label
        "lo"    $lo
        "hi"    $hi
        "rdb"   (printf "rdb-s%d:5020" $i)
        "idb"   (printf "idb-s%d:5030" $i)
        "wdb"   (printf "wdb-s%d:5040" $i) -}}
  {{- $shards = append $shards $shard -}}
{{- end -}}
{{- dict "shardCount" $n "gatewayPort" 5050 "shards" $shards | toJson -}}
{{- end -}}
