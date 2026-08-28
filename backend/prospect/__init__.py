"""Acquisitions modules — condo termination targets, the national metro
screener, and offering-memorandum generation.

These arrived from the standalone Prospect app. They keep their own SQLite
(``data/prospect.db``) for the scored ``target`` table and the stage-2
declaration findings typed in against it; everything national (markets,
counties, the Miami-Dade owner roll) lives in the shared store that the map
side of Groundwork already reads.
"""
