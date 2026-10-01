"""
Bridge to the ETL4 handoff package (database5553/) - full per-depth VSWIR/TIR
spectra for 5 holes, restored into its own Postgres database. See
../etl4_bridge.py for the Django-facing side of this; nothing in here is
imported directly except through that module.

_media_reader.py and _media_common.py are unmodified copies of the files
of the same name in database5553/etl4/database/ - the package's own
tested read path (Parquet row lookup, spectral byte-range reads, image
cropping maths), not reimplemented here. If that package is ever updated,
re-copy those two files as-is.

_db_common.py is NOT a copy - the original reads connection details from a
local_config.json next to it and resolves data paths from its own file
location. This one is a small from-scratch shim providing exactly what
those two files import from it, wired to Django settings instead.
"""
