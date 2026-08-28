# data/

Empty on purpose — this is the code-only copy.

Rebuild everything here from public sources (see the root README):

    venv\Scripts\python.exe scripts\fetch_layers.py           map polygon layers
    venv\Scripts\python.exe scripts\fetch_zori.py             zori_rents.json
    venv\Scripts\python.exe scripts\fetch_zcta_population.py  zcta_pop.json

`prospect.db` (scored condo targets + your declaration review) rebuilds from
`scripts\prospect\` — the ingest and scoring sequence is in the root README.
`marks.db`, `geom_cache.db` and `memos\` are created on first use.
