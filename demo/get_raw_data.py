"""
Fetch raw todo data from Canvas API.

Run: python demo/get_raw_data.py
"""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from canvas_api import get_user_todo


def main():
    data = get_user_todo()

    # Pretty print to terminal
    print(json.dumps(data, indent=2))

    # Save to file in parent directory for inspection
    output_path = Path(__file__).parent.parent / "raw_todo.json"
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved raw Canvas response to {output_path}")

if __name__ == "__main__":
    main()
