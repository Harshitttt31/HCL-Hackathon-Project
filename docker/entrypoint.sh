#!/bin/sh
# api: seed the database on first start (documents, rules, students), then serve the API
# ui:  serve the Streamlit front end
set -e
case "$1" in
  api)
    if [ ! -f "${SQLITE_PATH:-/app/data/university.db}" ]; then
      if [ ! -f data/source_register.csv ]; then
        echo "first start: data/ is not in git, building the synthetic documents and CSVs"
        python scripts/generate_synthetic.py --out data
      fi
      echo "first start: loading documents, rules and students"
      python scripts/seed_db.py --data data
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  ui)
    exec streamlit run app/frontend/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
    ;;
  *)
    exec "$@"
    ;;
esac
