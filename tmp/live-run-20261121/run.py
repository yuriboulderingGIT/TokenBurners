import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / '.venv' / 'Lib' / 'site-packages'))
sys.argv[0] = str(root / 'testset' / 'run_all.py')
runpy.run_path(sys.argv[0], run_name='__main__')
