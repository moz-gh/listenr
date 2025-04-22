#!/usr/bin/env python
import sys
import os
import logging

# Basic logging for the trigger script itself
logging.basicConfig(level=logging.INFO, format='%(asctime)s - TRIGGER - %(message)s')

# Use XDG_RUNTIME_DIR if available and fallback to /tmp
signal_dir = os.environ.get('XDG_RUNTIME_DIR', '/tmp')
pid_file = os.path.join(signal_dir, "asr_service.pid") # Store main service PID

def create_signal(signal_name):
    signal_file = os.path.join(signal_dir, signal_name)
    logging.info(f"Creating signal file: {signal_file}")
    try:
        # Check if main service is running (optional but good)
        if not os.path.exists(pid_file):
             logging.warning("Main ASR service PID file not found. Is it running?")
             # Should we try to start it? For now, just signal.

        # Create signal file
        with open(signal_file, 'w') as f:
             f.write('trigger') # Content doesn't matter
        # The background service should remove this file once processed
    except IOError as e:
        logging.error(f"Error creating signal file {signal_file}: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'start':
        create_signal("asr_start_signal")
    elif len(sys.argv) > 1 and sys.argv[1] == 'stop':
        create_signal("asr_stop_signal")
    else:
        print(f"Usage: {sys.argv[0]} [start|stop]")
        sys.exit(1)
    sys.exit(0)