"""
job.py - wire an extractor to a sink and run the export.

Pure orchestration over the two interfaces, so it's unit-tested with a fake
extractor (yields Arrow tables) and a fake sink (records calls) - no q, no
warehouse.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("export.job")


@dataclass
class ExportReport:
    table: str
    rows: int = 0
    batches: int = 0
    target: dict = field(default_factory=dict)

    def summary(self) -> str:
        return f"exported {self.rows:,} rows of {self.table} in {self.batches} batch(es)"


class ExportJob:
    def __init__(self, extractor, sink):
        self.extractor = extractor
        self.sink = sink

    def run(self, table: str, date=None, symbols=None, where=None) -> ExportReport:
        report = ExportReport(table=table)
        opened = False
        try:
            for batch in self.extractor.extract(table, date=date, symbols=symbols, where=where):
                if not opened:
                    self.sink.open(batch.schema, table)
                    opened = True
                report.rows += self.sink.write_table(batch)
                report.batches += 1
            if not opened:
                # nothing to export; open+close with the known schema so an empty
                # (but correctly-typed) target still gets created for parquet
                from . import schema as sch
                try:
                    self.sink.open(sch.arrow_schema_for(table), table)
                    opened = True
                except KeyError:
                    log.info("no rows and unknown table schema; nothing written")
        finally:
            if opened:
                report.target = self.sink.close()
        log.info(report.summary())
        return report
