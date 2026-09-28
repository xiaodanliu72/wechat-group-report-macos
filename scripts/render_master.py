#!/usr/bin/env python3
"""Run the magazine renderer using the configured project virtual environment."""
import sys
from run import main

if __name__ == '__main__':
    sys.argv = [sys.argv[0], 'render-master', *sys.argv[1:]]
    main()
