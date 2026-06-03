import sys
from pathlib import Path

# Asegura que los imports tipo `import ai_engine` funcionen
# desde el root del proyecto `cowork-clone/`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

