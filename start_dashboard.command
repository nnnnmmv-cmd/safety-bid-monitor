#!/bin/bash
# 안전진단 입찰 모니터 — 대시보드 실행 (Finder에서 더블클릭으로 실행 가능)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "📊 안전진단 입찰 모니터 대시보드를 시작합니다..."
echo "   디렉토리: $DIR"
if [ ! -x "./.venv/bin/streamlit" ]; then
  echo "❌ 가상환경이 없거나 streamlit이 설치되지 않았습니다."
  echo "   터미널에서 다음을 실행하세요:"
  echo "   cd \"$DIR\" && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  read -p "엔터를 누르면 창이 닫힙니다..."
  exit 1
fi
export STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
# 8501은 다른 프로젝트(homecheck-sales-hub 등)가 쓰고 있을 수 있어 8502로 고정
exec ./.venv/bin/streamlit run dashboard.py --server.headless false --server.port 8502 --browser.gatherUsageStats false
