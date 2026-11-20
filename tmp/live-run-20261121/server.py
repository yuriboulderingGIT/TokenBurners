import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / '.venv' / 'Lib' / 'site-packages'))
os.environ['DATA_DIR'] = str(root / 'tmp' / 'live-run-20261121' / 'data')
os.environ['LLM_CACHE'] = '0'

import uvicorn

uvicorn.run('app.main:app', host='127.0.0.1', port=18080, log_level='warning')
