#!/usr/bin/env bash
# 全プロンプト × A/B × 3 回を、順番に（並列にせず）実行する。
# 各回は A1→B1→A2→B2→A3→B3 の交互。失敗した回は同じ枠でリトライし、3 回連続で失敗したら、そのプロンプトは打ち切って
# 次へ進む（NEEDS_JUDGMENT に記録）。再開できる: 同じ OUT_DIR で再実行すると、成功済みの枠は飛ばす。
# 使い方: scripts/bench_all.sh OUT_DIR [PROMPT_NAME ...]
set -u
here=$(cd "$(dirname "$0")" && pwd)
out=$(mkdir -p "$1" && cd "$1" && pwd); shift
names=("$@"); [ ${#names[@]} -eq 0 ] && names=($(cd "$here/../prompts" && ls *.txt | sed 's/\.txt$//'))
log() { echo "$(date +%H:%M:%S) $*" | tee -a "$out/progress.log"; }

for name in "${names[@]}"; do
  stop=0
  for n in 1 2 3; do
    for m in A B; do
      [ $stop = 1 ] && continue
      ok_meta=$(grep -l '"ok": true' "$out/$name/$m-$n"-t*/meta.json 2>/dev/null | head -1)
      [ -n "$ok_meta" ] && continue
      t=$(( $(ls -d "$out/$name/$m-$n"-t* 2>/dev/null | wc -l) + 1 ))
      while :; do
        log "start $name $m-$n try $t"
        if "$here/bench_run.sh" "$out" "$name" "$m" "$n" "$t" > /dev/null; then
          log "  ok   $(jq -r '"\(.real_s)s $\(.total_cost_usd) \(.turns)turns"' "$out/$name/$m-$n-t$t/meta.json")"; break
        fi
        log "  FAIL $(jq -r .fail_reason "$out/$name/$m-$n-t$t/meta.json")"
        if [ "$t" -ge 3 ]; then
          log "NEEDS_JUDGMENT $name $m-$n: 3 failures in a row, skipping the rest of this prompt"
          printf '%s\t%s-%s\n' "$name" "$m" "$n" >> "$out/needs_judgment.tsv"; stop=1; break
        fi
        t=$((t+1))
      done
    done
  done
done
log "all done"
[ -f "$out/needs_judgment.tsv" ] && { log "needs judgment:"; cat "$out/needs_judgment.tsv"; }
