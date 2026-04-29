import psutil
import time
import logging
import threading
from datetime import datetime


class ResourceMonitor:
    """Monitors memory usage and timeout constraints."""

    def __init__(self, memory_threshold: int, timeout: int):
        self.memory_threshold = memory_threshold
        self.timeout = timeout
        self.start_time = time.time()
        self.process = psutil.Process()
        self.logger = logging.getLogger("mahilda")
        self.should_stop = False
        self._stop_event = threading.Event()
        self._timed_out = False

    def monitor(self):
        """Continuously monitors memory and execution time."""
        while not self._stop_event.is_set():
            try:
                elapsed_time = time.time() - self.start_time
                memory_usage = self.process.memory_info().rss
                for child in self.process.children(recursive=True):
                    memory_usage += child.memory_info().rss

                memory_usage_gb = memory_usage / (1024**3)

                self.logger.debug(f"Total Memory Usage: {memory_usage_gb:.2f} GB, Elapsed Time: {elapsed_time:.1f}s")

                if memory_usage_gb > (self.memory_threshold / (1024**3)):
                    self.logger.error(f"Memory usage exceeded: {memory_usage_gb:.2f} GB")
                    self.should_stop = True
                    break

                if elapsed_time > self.timeout:
                    self._timed_out = True
                    self.logger.error(f"Execution timeout exceeded: {elapsed_time:.1f}s > {self.timeout}s")
                    self.should_stop = True
                    # Set stop event to wake any waiting threads quickly
                    self._stop_event.set()
                    break

                # Sleep for a short interval or until stop event is set for responsiveness
                if self._stop_event.wait(timeout=1.0):
                    break

            except Exception as e:
                self.logger.error(f"Error in resource monitor: {e}")
                break

    def stop(self):
        """Stop the resource monitor."""
        self._stop_event.set()
        self.should_stop = True

    def reset_timer(self):
        """Reset the start time for timeout calculation."""
        self.start_time = time.time()
        self._timed_out = False

    def is_timed_out(self) -> bool:
        """Return True if the last stop was due to timeout."""
        return self._timed_out
