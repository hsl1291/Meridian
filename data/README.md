# data/

Empty in the repository on purpose -- everything here is either built from
public sources or is your own work, and none of it belongs in git.

**Upgrading?** `install.bat` copies this folder in from your previous install
automatically (see the root README). To do it by hand:

    .venv\Scripts\python.exe install.py --import-from "C:\path\to\old\Meridian"

**Fresh machine?** Rebuild from public sources:

    .venv\Scripts\python.exe scripts\fetch_layers.py           map polygon layers
    .venv\Scripts\python.exe scripts\fetch_zori.py             zori_rents.json
    .venv\Scripts\python.exe scripts\fetch_zcta_population.py  zcta_pop.json

`prospect.db` (scored condo targets, deal stages, your declaration review)
rebuilds from `scripts\prospect\` -- the sequence is in the root README.
`marks.db`, `geom_cache.db` and `memos\` are created on first use.
