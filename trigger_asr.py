#!/usr/bin/env python
import sys
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - TRIGGER - %(message)s')

signal_dir = '/tmp'

pid_file = os.path.join(signal_dir, "asr_service.pid")

def create_signal(signal_name):
    signal_file = os.path.join(signal_dir, signal_name)
    logging.info(f"Creating signal file: {signal_file}")
    try:
        if not os.path.exists(pid_file):
            logging.warning("Main ASR service PID file not found. Is it running?")

        with open(signal_file, 'w') as f:
            f.write('trigger')
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