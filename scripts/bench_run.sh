#!/usr/bin/env bash
# 1 回分の計測: Claude Code (haiku) で 1 つのプロンプトを、A か B で実行して、記録と判定を残す。
#   A = Claude Code + typesafe-auto-browsing スキル    B = Claude Code + Playwright MCP
# 使い方: scripts/bench_run.sh OUT_DIR PROMPT_NAME A|B N [TRY]   （TRY = リトライの通し番号、既定 1）
# 終了コード: 0 = 成功、1 = 失敗（理由は meta.json の fail_reason）
set -u
out=$(mkdir -p "$1" && cd "$1" && pwd) name=$2 m=$3 n=$4 t=${5:-1}
repo=$(cd "$(dirname "$0")/.." && pwd)
work=${BENCH_WORK:-/tmp/bench-work}
d=$out/$name/$m-$n-t$t
mkdir -p "$d" "$work/cc-a/.claude/skills/typesafe-auto-browsing" "$work/cc-b" "$out/profile-A"

# 環境（毎回同じ内容を書き直すだけ）
cp "$repo/skills/typesafe-auto-browsing/SKILL.md" "$work/cc-a/.claude/skills/typesafe-auto-browsing/SKILL.md"
cat > "$work/mcp.json" <<EOF
{"mcpServers":{"playwright":{"command":"npx","args":["-y","@playwright/mcp@latest","--browser","chrome","--config","$repo/src/typesafe_auto_browsing/playwright-mcp.json"]}}}
EOF

goal=$(grep -v '^#' "$repo/prompts/$name.txt" | grep -v '^$' | tr '\n' ' ' | sed 's/ $//')
sid=$(uuidgen | tr A-Z a-z)
common=(--model haiku --session-id "$sid" --output-format stream-json --verbose --disallowedTools WebFetch WebSearch --permission-mode dontAsk)

if [ "$m" = A ]; then
  cwd=$work/cc-a
  prompt="typesafe-auto-browsing をつかって、$goal"
  mkdir -p "$d/tsab-home"
  ln -sfn "$out/profile-A" "$d/tsab-home/playwright"   # Chrome のプロファイルは、B の既定と同じく、実行をまたいで共有
  export TYPESAFE_AUTO_BROWSING_HOME=$d/tsab-home
  opts=(--strict-mcp-config --setting-sources project --allowedTools Skill Bash Read Grep)
else
  cwd=$work/cc-b
  prompt="playwright-mcp をつかって、$goal"
  opts=(--strict-mcp-config --mcp-config "$work/mcp.json" --setting-sources "" --allowedTools mcp__playwright)
fi

printf '%s\n' "$prompt" > "$d/prompt.txt"
{ printf 'cd %q && ' "$cwd"; printf '%q ' claude -p "$prompt" "${common[@]}" "${opts[@]}"; echo; } > "$d/cmd.sh"

start=$(date +%Y-%m-%dT%H:%M:%S%z)
# 実時間は /usr/bin/time、15 分で打ち切り
(cd "$cwd" && /usr/bin/time -p -o "$d/time.txt" perl -e 'alarm shift; exec @ARGV' 900 \
  claude -p "$prompt" "${common[@]}" "${opts[@]}" > "$d/stream.jsonl" 2> "$d/stderr.txt" < /dev/null)
code=$?
real=$(awk '/^real/{print $2}' "$d/time.txt")

# 集計と判定
res=$(jq -c 'select(.type=="result")' "$d/stream.jsonl" 2>/dev/null | tail -1)
cost=$(jq -r '.total_cost_usd // 0' <<<"$res" 2>/dev/null || echo 0); cost=${cost:-0}
turns=$(jq -r '.num_turns // 0' <<<"$res" 2>/dev/null || echo 0); turns=${turns:-0}
answer=$(jq -r '.result // ""' <<<"$res" 2>/dev/null)
is_error=$(jq -r 'if .is_error == false then "false" else "true" end' <<<"$res" 2>/dev/null || echo true)  # // は false を欠損扱いにするので使わない
tools=$(jq -r 'select(.type=="assistant")|.message.content[]?|select(.type=="tool_use")|.name' "$d/stream.jsonl" 2>/dev/null)
skill=$(jq -r 'select(.type=="assistant")|.message.content[]?|select(.type=="tool_use" and .name=="Skill")|.input.skill' "$d/stream.jsonl" 2>/dev/null)
# CLI の呼び出し（typesafe-auto-browsing の後ろが空白と目的文かオプション）。パスに含まれる名前には反応させない
tsab_cmd=$(jq -r 'select(.type=="assistant")|.message.content[]?|select(.type=="tool_use" and .name=="Bash")|.input.command' "$d/stream.jsonl" 2>/dev/null | grep -cE 'typesafe-auto-browsing +(["'"'"'-]|https?:)')
pw=$(grep -c '^mcp__playwright__' <<<"$tools")

tsab_cost=0 tsab_ok=""
if [ "$m" = A ]; then
  outcomes=$(cat "$d"/tsab-home/logs/*.jsonl 2>/dev/null | jq -c 'select(.kind=="outcome")')
  tsab_cost=$(jq -s 'map(.usage.cost_usd)|add // 0' <<<"$outcomes")
  tsab_ok=$(jq -s 'if length==0 then "none" else (last.success|tostring) end' -r <<<"$outcomes")
fi

fail=""
[ "$code" -ne 0 ] && fail="claude exit $code"
[ -z "$fail" ] && [ "$is_error" != false ] && fail="is_error"
[ -z "$fail" ] && [ -z "${answer//[[:space:]]/}" ] && fail="empty answer"
if [ -z "$fail" ] && [ "$m" = A ]; then
  [ "$skill" != "typesafe-auto-browsing" ] && fail="skill not called"
  [ -z "$fail" ] && [ "$tsab_cmd" -eq 0 ] && fail="cli not called"
  [ -z "$fail" ] && [ "$pw" -gt 0 ] && fail="playwright called directly"
  [ -z "$fail" ] && [ "$tsab_ok" != true ] && fail="cli success=$tsab_ok"
elif [ -z "$fail" ]; then
  [ "$pw" -eq 0 ] && fail="playwright not called"
  [ -z "$fail" ] && [ -n "$skill" ] && fail="skill called"
  [ -z "$fail" ] && [ "$tsab_cmd" -gt 0 ] && fail="cli called"
fi

transcript=$(find "$HOME/.claude/projects" -name "$sid.jsonl" 2>/dev/null | head -1)
[ -n "$transcript" ] && cp "$transcript" "$d/transcript.jsonl"
jq -n --arg prompt "$name" --arg m "$m" --argjson n "$n" --argjson t "$t" --arg sid "$sid" --arg transcript "$transcript" \
  --arg start "$start" --argjson real "${real:-0}" --argjson cost "$cost" --argjson tsab "$tsab_cost" --argjson turns "$turns" \
  --arg fail "$fail" --arg answer "$answer" --arg cwd "$cwd" \
  '{prompt:$prompt,method:$m,n:$n,try:$t,session_id:$sid,transcript:$transcript,cwd:$cwd,start:$start,real_s:$real,
    claude_cost_usd:$cost,typesafe_cost_usd:$tsab,total_cost_usd:($cost+$tsab),turns:$turns,
    ok:($fail==""),fail_reason:$fail,answer:$answer}' > "$d/meta.json"
jq -r '[.prompt,.method,.n,.try,.session_id,.start,.real_s,.claude_cost_usd,.typesafe_cost_usd,.total_cost_usd,.turns,(if .ok then "ok" else "FAIL: "+.fail_reason end)]|@tsv' "$d/meta.json" >> "$out/index.tsv"
cat "$d/meta.json"
[ -z "$fail" ]
