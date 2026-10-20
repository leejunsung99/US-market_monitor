"""Read-only probes of public official sources; does not store or send events."""

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from us_market_monitor.config import Config
from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.main import build_collectors
from us_market_monitor.processors.classifier import Classifier


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = Config.load(Path.cwd())
    classifier = Classifier(config.watchlist, config.filters)
    sources = [
        s
        for s in build_collectors(config, threading.Event())
        if s.name.startswith(("economic:", "official:", "calendar:"))
    ]

    def probe(source):
        cursor = {}
        try:
            entries = source.collect(utcnow() - timedelta(hours=24), cursor)
            calendar = getattr(source, "is_calendar", False)
            approved = 0 if calendar else sum(classifier.classify(e) is not None for e in entries)
            return {
                "source": source.name,
                "status": "ok",
                "recent_entries": len(entries),
                "approved": approved,
                "details": cursor,
                "titles": [] if calendar else [e.title for e in entries],
            }
        except Exception as error:
            return {
                "source": source.name,
                "status": "failed",
                "error": str(error) if isinstance(error, FetchError) else type(error).__name__,
            }

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = []
        for result in pool.map(probe, sources):
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"checked_at": utcnow().isoformat(), "sources": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return int(any(r["status"] != "ok" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
