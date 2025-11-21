import shlex
import subprocess
import psutil
import time


def run_cmd(cmd_string: str, timeout: int = None, memory_limit_gb: float = 30) -> bool:
    """
    Run a shell command with optional support for pipes, output redirection, timeout, and memory monitoring.

    Args:
        cmd_string (str): The shell command to execute.
        timeout (int, optional): Timeout in seconds for the command. Defaults to None.
        memory_limit_gb (float, optional): Maximum memory (in GB) allowed for the command. Defaults to None.

    Returns:
        bool: True if the command executed successfully, False otherwise.
    """
    cmd_list = shlex.split(cmd_string)

    try:
        print(f"Executing command: {cmd_string}")

        # Determine if the command includes a pipe
        if "|" in cmd_list:
            pipe_index = cmd_list.index("|")
            cmd_list1 = cmd_list[:pipe_index]
            cmd_list2 = cmd_list[pipe_index + 1:]

            # Execute the piped command
            with subprocess.Popen(cmd_list1, stdout=subprocess.PIPE) as p1, \
                    subprocess.Popen(cmd_list2, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p2:
                p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits early
                try:
                    output, errors = p2.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    print("Command timed out. Terminating...")
                    p2.terminate()
                    return False

                if p2.returncode == 0:
                    print(f"Command executed successfully: {cmd_string}")
                    return True
                else:
                    print(f"Command failed with return code {p2.returncode}: {errors.decode()}")
                    return False

        # Check if the command includes output redirection ('>')
        elif ">" in cmd_list:
            output_file_index = cmd_list.index(">") + 1
            if output_file_index < len(cmd_list):
                output_file = cmd_list[output_file_index]
                cmd_list = cmd_list[:cmd_list.index(">")]

                with open(output_file, "w") as f:
                    process = subprocess.Popen(cmd_list, stdout=f, stderr=subprocess.PIPE)
                    try:
                        _, errors = process.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        print("Command timed out. Terminating...")
                        process.terminate()
                        return False

                    if process.returncode == 0:
                        print(f"Command executed successfully: {cmd_string}")
                        return True
                    else:
                        print(f"Command failed with return code {process.returncode}: {errors.decode()}")
                        return False

        # Standard command execution
        else:
            with subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                # Monitor memory usage if a memory limit is specified
                if memory_limit_gb:
                    monitor_thread = start_memory_monitor(process.pid, memory_limit_gb)
                try:
                    output, errors = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    print("Command timed out. Terminating...")
                    process.terminate()
                    return False

                if process.returncode == 0:
                    print(f"Command executed successfully: {cmd_string}")
                    return True
                else:
                    print(f"Command failed with return code {process.returncode}: {errors.decode()}")
                    return False
    except Exception as e:
        print(f"An error occurred while executing the command: {e}")
        return False


def start_memory_monitor(pid: int, memory_limit_gb: float):
    """
    Start a thread to monitor memory usage of a process.

    Args:
        pid (int): Process ID to monitor.
        memory_limit_gb (float): Maximum memory (in GB) allowed for the process.

    Returns:
        threading.Thread: The thread monitoring the memory.
    """
    import threading

    def monitor():
        process = psutil.Process(pid)
        while process.is_running():
            memory_usage_gb = process.memory_info().rss / (1024 ** 3)  # Memory usage in GB
            if memory_usage_gb > memory_limit_gb:
                print(f"Memory usage exceeded: {memory_usage_gb:.2f} GB. Terminating process...")
                process.terminate()
                break
            time.sleep(1)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    return thread
