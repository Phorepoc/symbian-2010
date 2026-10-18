#!/bin/sh
# fetch.sh -- fetch every package of sources.lock at its pinned commit from the Phorepoc forks into src/<package>.
# Shallow (one commit each); safe to re-run: a package already at its commit is skipped.
set -eu
cd "$(dirname "$0")"
BASE="${SOURCES_URL:-https://github.com/Phorepoc}"
mkdir -p src
grep -v '^#' sources.lock | while IFS="$(printf '\t')" read -r pkg commit; do
	[ -n "$pkg" ] || continue
	d="src/$pkg"
	if [ -d "$d/.git" ] && [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then
		echo "have  $pkg"
		continue
	fi
	echo "fetch $pkg @ ${commit%"${commit#????????}"}"
	mkdir -p "$d"
	[ -d "$d/.git" ] || git -C "$d" init -q
	git -C "$d" fetch -q --depth 1 "$BASE/$pkg.git" "$commit"
	git -C "$d" checkout -q --force FETCH_HEAD
done
