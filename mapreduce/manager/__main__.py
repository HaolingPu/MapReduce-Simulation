"""MapReduce framework Manager node."""
import os
import tempfile
import logging
import json
import time
import click
import mapreduce.utils
import socket
import threading
import queue
import shutil
import hashlib
from pathlib import Path


# Configure logging
LOGGER = logging.getLogger(__name__)


class Manager:
    """Represent a MapReduce framework Manager node."""

    def __init__(self, host, port):
        """Construct a Manager instance and start listening for messages."""
        self.host = host
        self.port = port
        self.signals = {"shutdown": False}
        self.workers = {}
        self.job_queue = queue.Queue()
        self.job_count = 0
        self.finished_job_tasks = 0


        thread_tcp_server = threading.Thread(target = self.manager_tcp_server)
        thread_tcp_server.start()
        #thread_tcp_client = threading.Thread(target = self.manager_tcp_client)
        #thread_tcp_client.start()
        

        self.run_job()
        thread_tcp_server.join()
        
        LOGGER.info(
            "Starting manager host=%s port=%s pwd=%s",
            host, port, os.getcwd(),
        )


# phling的code：
    def manager_tcp_server (self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # Bind the socket to the server
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen()
            sock.settimeout(1)

            LOGGER.info("TCP Server listening on %s:%s", self.host, self.port)

            while not self.signals["shutdown"]:
                try:
                    clientsocket, address = sock.accept()
                except socket.timeout:
                    continue
                LOGGER.info("Connection from %s", address[0])

                clientsocket.settimeout(1)

                with clientsocket:
                    message_chunks = []
                    while True:    # ????????
                        try:
                            data = clientsocket.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        message_chunks.append(data)

                # Decode list-of-byte-strings to UTF8 and parse JSON data
                message_bytes = b''.join(message_chunks)
                message_str = message_bytes.decode("utf-8")
                print("???", message_str)
                try:
                    message_dict = json.loads(message_str)
                except json.JSONDecodeError:
                    LOGGER.warning("Invalid JSON message received and ignored.")  # 加了为了debug
                    continue

                LOGGER.info("Received message: %s", message_dict)

                if message_dict["message_type"] == "shutdown" :
                    # send the shutdown message to all the workers
                    for worker_id in self.workers:
                        worker_host, worker_port = worker_id
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                            # connect to the server
                            sock.connect((worker_host, worker_port))
                            # send a message
                            message = json.dumps({"message_type": "shutdown"})
                            sock.sendall(message.encode('utf-8'))

                    self.signals["shutdown"] = True
                    LOGGER.info("Manager shut down!")

                elif message_dict["message_type"] == "register":
                    worker_id = (message_dict["worker_host"], message_dict["worker_port"])
                    worker_host, worker_port = worker_id
                    self.workers[worker_id] = {
                        "status": "ready",
                        "current_task": None
                        # "last_ping": time.time()
                    }
                    LOGGER.info(f"Worker registered: {worker_id}")
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                            # connect to the server
                            sock.connect((worker_host, worker_port))
                            ack_message = json.dumps({"message_type": "register_ack"})
                            sock.sendall(ack_message.encode('utf-8'))
                            LOGGER.info(f"Sent registration acknowledgment to worker {worker_id}.")
                elif message_dict["message_type"] == "new_manager_job":
                    job = {
                            "job_id": self.job_count,
                            "input_directory": message_dict["input_directory"],
                            "output_directory": message_dict["output_directory"],
                            "mapper_executable": message_dict["mapper_executable"],
                            "reducer_executable": message_dict["reducer_executable"],
                            "num_mappers" : message_dict["num_mappers"],
                            "num_reducers" : message_dict["num_reducers"]
                            }
                    self.job_count += 1
                    self.job_queue.put(job)
                    LOGGER.info(f"Added Job with Job id: {job['job_id']}")
                elif message_dict["message_type"] == "finished":
                    worker_id = (message_dict["worker_host"], message_dict["worker_port"])
                    self.finished_job_tasks += 1
                    self.workers[worker_id]['status'] = "ready"
                    #"task_id": int,
          
                        
    def run_job(self):
        
        while not self.signals["shutdown"]:
            try:
                # Wait for a job to be available in the queue or check periodically
                job = self.job_queue.get(timeout=1)  # Adjust timeout as necessary
                LOGGER.info(f"Starting job {job['job_id']}")
                # delete output directory
                output_directory = job["output_directory"]
                if os.path.exists(output_directory):
                    shutil.rmtree(output_directory)
                    LOGGER.info(f"Deleted existing output directory: {output_directory}")

                # Create the output directory
                os.makedirs(output_directory)
                LOGGER.info(f"Created output directory: {output_directory}")

                # Create a shared directory for temporary intermediate files
                prefix = f"mapreduce-shared-job{job['job_id']:05d}-" 
                # prefix = f"mapreduce-shared-job{self.job_id:05d}-"
                with tempfile.TemporaryDirectory(prefix=prefix) as tmpdir:
                    LOGGER.info("Created tmpdir %s", tmpdir)
                    # Change this loop so that it runs either until shutdown or when the job is completed.
                    while (not self.signals["shutdown"]) or (self.finished_job_tasks == job['num_mappers'] + job['num_reducers']):
                        print("GOAL,", job['num_mappers'], job['num_reducers'])
                        print(self.finished_job_tasks)
                        self.send_tasks(job, tmpdir)
                        time.sleep(0.1)
                LOGGER.info("Cleaned up tmpdir %s", tmpdir)

            except queue.Empty:
                # No job was available in the queue
                pass
    def send_tasks(self, job, tmpdir):
        files = []
        for filename in os.listdir(job['input_directory']):
            file_path = os.path.join(job['input_directory'], filename)
            if os.path.isfile(file_path):
                # Add the file to the list only if it is a regular file
                files.append(filename)
        # Sort the list of files by name
        sorted_files = sorted(files)
        partitions_files = [[] for _ in range(job['num_mappers'])]
        for i, file_name in enumerate(sorted_files):
            mapper_index = i % job['num_mappers']
            partitions_files[mapper_index].append(file_name)
        for j in range(len(partitions_files)):
            for worker_id in self.workers:
                if self.workers[worker_id]['status'] == "ready":
                    self.workers[worker_id]['current_task'] = j
                    self.workers[worker_id]['status'] = "busy"
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                        worker_host, worker_port = worker_id
                        sock.connect((worker_host, worker_port))
                        input_paths = []
                        for file in partitions_files[j]:
                            input_paths.append(str(job['input_directory']) + '/' + str(file))
                        context = {
                                    "message_type": "new_map_task",
                                    "task_id": j,
                                    "input_paths": input_paths,
                                    "executable": job['mapper_executable'],
                                    "output_directory": tmpdir,
                                    "num_partitions": job['num_reducers']
                                }
                        message = json.dumps(context)
                        sock.sendall(message.encode('utf-8'))
                        print(f"ASSIGNED Task ID {j} to Worker {worker_id}")
                        print(f"j = {j}")
                    break



@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=6000)
@click.option("--logfile", "logfile", default=None)
@click.option("--loglevel", "loglevel", default="info")
@click.option("--shared_dir", "shared_dir", default=None)
def main(host, port, logfile, loglevel, shared_dir):
    """Run Manager."""
    tempfile.tempdir = shared_dir
    if logfile:
        handler = logging.FileHandler(logfile)
    else:
        handler = logging.StreamHandler()
    formatter = logging.Formatter(
        f"Manager:{port} [%(levelname)s] %(message)s"
    )
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(loglevel.upper())
    Manager(host, port)


if __name__ == "__main__":
    main()
