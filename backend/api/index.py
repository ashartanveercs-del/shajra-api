import sys
import os

# Add the parent directory (backend root) to the system path so it can find main.py and config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app
