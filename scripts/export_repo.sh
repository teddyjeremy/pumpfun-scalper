#!/bin/bash
# Build a clean export of livebot + research code into /workspace/pumpfun_export (no data, no keys, no logs).
set -e
SRC=/workspace/pumpfun; DST=/workspace/pumpfun_export
mkdir -p $DST
find $DST -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
(cd $SRC/livebot && tar cf - --exclude='.env' --exclude='*.env' --exclude='./data' --exclude='./fixtures' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='*.jsonl' --exclude='*.json.gz' --exclude='*.csv' --exclude='*.log' \
  --exclude='*.bak*' --exclude='*.pkl' --exclude='.pytest_cache' .) | (cd $DST && tar xf -)
mkdir -p $DST/research/screen/v2 $DST/research/screen/quote_coins $DST/research/screen/cache $DST/research/paper $DST/research/lib
cp $SRC/screen/*.py $DST/research/screen/
cp $SRC/screen/v2/*.py $DST/research/screen/v2/
cp $SRC/screen/quote_coins/*.py $DST/research/screen/quote_coins/ 2>/dev/null || true
cp $SRC/screen/cache/*.md $DST/research/screen/cache/
cp $SRC/screen/cache/scalp_tapes/report_tables.md $DST/research/screen/cache/scalp_report_tables.md 2>/dev/null || true
cp $SRC/paper/*.py $DST/research/paper/
cp $SRC/lib/*.py $DST/research/lib/
cp $SRC/RULEBOOK.md $DST/research/RULEBOOK_v1.md 2>/dev/null || true
cp $SRC/analyze.py $SRC/rescore_live.py $DST/research/ 2>/dev/null || true
cat > $DST/.gitignore <<'G'
.env
*.env
data/
fixtures/
__pycache__/
*.pyc
*.jsonl
*.json.gz
*.csv
*.log
*.pkl
*.bak*
.pytest_cache/
G
echo exported; find $DST -type f | wc -l
