"""Replay saved first-generation HTML through current QA; no API/LLM calls."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/ai-engine'))
from src.engine.interactive_creation import validate_interactive_html


async def main(directory):
    directory = Path(directory).resolve()
    summary = json.loads((directory / 'summary.json').read_text())
    reports = []
    for case in summary['completed']:
        if case['status'] != 'succeeded':
            continue
        start = time.monotonic()
        report = await validate_interactive_html((directory / (case['gameId'] + '.html')).read_text())
        item = {'case_id':case['case_id'],'gameId':case['gameId'],
                'elapsed_s':round(time.monotonic()-start,2),'report':report}
        reports.append(item)
        (directory / 'pipeline-replay.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
        print(json.dumps({'case_id':case['case_id'],'passed':report['passed'],
                          'elapsed_s':item['elapsed_s'],'issues':report['issues']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    # Replaying local HTML must not query environment DB settings.
    with patch('src.config.timeout_store.refresh'):
        asyncio.run(main(parser.parse_args().directory))
