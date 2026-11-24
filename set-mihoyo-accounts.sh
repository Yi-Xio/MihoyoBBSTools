#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_dir="$repo_dir/config"
output_file="$repo_dir/MIHOYO_ACCOUNTS.txt"

if [[ -d "$output_file" ]]; then
  printf '输出路径是目录 %s\n' "$output_file" >&2
  exit 1
fi

temp_file="$(mktemp "$output_file.tmp.XXXXXX")"
trap 'rm -f -- "$temp_file"' EXIT

ruby -ryaml -rjson - \
  "$config_dir/account-1.yaml" "$config_dir/account-2.yaml" > "$temp_file" <<'RUBY'
accounts = ARGV.map do |filename|
  config = YAML.safe_load(File.read(filename, encoding: "UTF-8"), aliases: true)
  abort "账号配置应为映射 #{File.basename(filename)}" unless config.is_a?(Hash)

  { "alias" => File.basename(filename, ".yaml"), "config" => config }
rescue Psych::Exception, SystemCallError
  abort "无法读取或解析账号配置 #{File.basename(filename)}"
end
print JSON.generate(accounts)
RUBY

mv -f -- "$temp_file" "$output_file"
printf '已生成 %s\n' "$output_file"
