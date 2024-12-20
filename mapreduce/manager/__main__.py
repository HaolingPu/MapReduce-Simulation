"""MapReduce framework Manager node."""
import os
import tempfile
import logging
import json
import time
import socket
import threading
import queue
import shutil
import copy
import click
from mapreduce.utils import listen_message


# Configure logging
LOGGER = logging.getLogger(__name__)


class Manager:
    """Represent a MapReduce framework Manager node."""

    def __init__(self, host, port):
        """Construct a Manager instance and start listening for messages."""
        # self.host = host
        # self.port = port
        self.signals = {"shutdown": False}
        self.workers = {}
        self.job_queue = queue.Queue()
        # self.job_count = 0
        # self.finished_job_tasks = 0
        self.job_info = {"job_count": 0, "finished_job_tasks": 0}
        self.current_task = None
        self.copy_task = None
        self.havejob = False
        thread_tcp_server = threading.Thread(target=self.manager_tcp_server,
                                             args=(host, port))
        thread_tcp_server.name = "manager_tcp_server"
        thread_udp_server = threading.Thread(target=self.manager_udp_server,
                                             args=(host, port))
        thread_udp_server.name = "manager_udp_server"
        thread_fault_tolerance = threading.Thread(
            target=self.fault_tolerance_thread
        )
        thread_tcp_server.start()
        thread_udp_server.start()
        thread_fault_tolerance.start()
        # formatter = logging.Formatter(
        #     f"Manager:{port}:%(threadName)s [%(levelname)s] %(message)s"
        # )
        while not self.signals["shutdown"]:
            if self.havejob is True:
                self.run_job()
                print("inside while", self.signals["shutdown"])
                self.havejob = not self.job_queue.empty()
                self.job_info["finished_job_tasks"] = 0
            time.sleep(0.1)

        print("run_job is done")
        thread_tcp_server.join()
        thread_udp_server.join()
        thread_fault_tolerance.join()
        LOGGER.info(
            "Starting manager host=%s port=%s pwd=%s",
            host, port, os.getcwd(),
        )

    def manager_tcp_server(self, host, port):
        """Construct a Manager instance and start listening for messages."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # Bind the socket to the server
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sock.listen()
            sock.settimeout(0.1)

            LOGGER.info("TCP Server listening on %s:%s", host, port)

            while not self.signals["shutdown"]:
                try:
                    clientsocket, address = sock.accept()
                except socket.timeout:
                    continue
                LOGGER.info("Connection from %s", address[0])

                clientsocket.settimeout(0.1)

                try:
                    message_dict = json.loads(listen_message(clientsocket))
                except json.JSONDecodeError:
                    LOGGER.warning(
                        "Invalid JSON message received and ignored."
                    )
                    continue

                LOGGER.info("Received message: %s", message_dict)

                if message_dict["message_type"] == "shutdown":
                    for worker_id, worker in self.workers.items():
                        if worker["status"] != "dead":
                            worker_host, worker_port = worker_id
                            worker["status"] = "dead"
                            try:
                                with socket.socket(
                                    socket.AF_INET, socket.SOCK_STREAM
                                ) as sock1:
                                    # connect to the server
                                    sock1.connect((worker_host, worker_port))
                                    # send a message
                                    message = json.dumps(
                                        {"message_type": "shutdown"}
                                    )
                                    sock1.sendall(message.encode('utf-8'))
                            except ConnectionRefusedError:
                                worker["status"] = "dead"
                                LOGGER.info("ConnectionRefusedError")
                    self.signals["shutdown"] = True
                    print(self.signals["shutdown"])
                    LOGGER.info("Manager shut down!")
                    break
                if message_dict["message_type"] == "register":
                    self.handle_register(message_dict)
                    # check the dead worker alive now
                    # worker_id = (
                    #     message_dict["worker_host"],
                    #     message_dict["worker_port"]
                    # )
                    # worker_host, worker_port = worker_id
                    # try:
                    #     with socket.socket(
                    #         socket.AF_INET, socket.SOCK_STREAM
                    #     ) as sock2:
                    #         # connect to the server
                    #         sock2.connect((worker_host, worker_port))
                    #         ack_message = json.dumps(
                    #             {"message_type": "register_ack"}
                    #         )
                    #         sock2.sendall(ack_message.encode('utf-8'))
                    #         LOGGER.info(
                    #             "Sent registration"
                    #             " acknowledgment to worker %s.",
                    #             worker_id
                    #         )
                    # except ConnectionRefusedError:
                    #     self.con_err_refuse(worker_id)
                    #     LOGGER.info("ConnectionRefusedError")

                    # if worker_id in self.workers:
                    #     if self.workers[worker_id]["status"] == "dead":
                    #         self.workers[worker_id]["status"] = "ready"
                    #         self.workers[worker_id]["current_stage"] = None
                    #         LOGGER.info(
                    #             "Recognized Dead worker%s is now alive",
                    #             worker_id
                    #         )
                    #     elif self.workers[worker_id]["status"] == "busy":
                    #         # reassign task
                    #         task_id = (
                    #             self.workers[worker_id]["current_task_id"]
                    #         )
                    #         # split into two cases
                    #         self.append_failed_task(worker_id, task_id)
                    #         self.workers[worker_id]["status"] = "ready"
                    #         self.workers[worker_id]["current_stage"] = None
                    #         LOGGER.info(
                    #             "Unrecognized Dead worker%s is now alive",
                    #             worker_id
                    #         )
                    # else:
                    #     LOGGER.info("create a new worker object here!!!")
                    #     self.workers[worker_id] = {
                    #         "status": "ready",  # ready, busy, dead
                    #         "current_task_id": None,
                    #         "current_stage": None,
                    #         "last_ping": time.time()
                    #     }

                    #     LOGGER.info("New worker registered: %s", worker_id)

                elif message_dict["message_type"] == "new_manager_job":
                    job = {
                            "job_id": self.job_info['job_count'],
                            "input_directory": message_dict["input_directory"],
                            "output_directory":
                            message_dict["output_directory"],
                            "mapper_executable":
                            message_dict["mapper_executable"],
                            "reducer_executable":
                            message_dict["reducer_executable"],
                            "num_mappers": message_dict["num_mappers"],
                            "num_reducers": message_dict["num_reducers"]
                            }
                    self.job_info['job_count'] += 1
                    self.job_queue.put(job)
                    LOGGER.info("Added Job with Job id: %s", job['job_id'])
                    self.havejob = True
                elif message_dict["message_type"] == "finished":
                    worker_id = (
                        message_dict["worker_host"],
                        message_dict["worker_port"]
                    )
                    self.job_info["finished_job_tasks"] += 1
                    self.workers[worker_id]['status'] = "ready"
                    # "task_id": int,

    def handle_register(self, message_dict):
        """Handle Register Message."""
        # check the dead worker alive now
        worker_id = (
                        message_dict["worker_host"],
                        message_dict["worker_port"]
                    )
        worker_host, worker_port = worker_id
        try:
            with socket.socket(
                socket.AF_INET, socket.SOCK_STREAM
            ) as sock2:
                # connect to the server
                sock2.connect((worker_host, worker_port))
                ack_message = json.dumps(
                    {"message_type": "register_ack"}
                )
                sock2.sendall(ack_message.encode('utf-8'))
                LOGGER.info(
                    "Sent registration"
                    " acknowledgment to worker %s.",
                    worker_id
                )
        except ConnectionRefusedError:
            self.con_err_refuse(worker_id)
            LOGGER.info("ConnectionRefusedError")

        if worker_id in self.workers:
            if self.workers[worker_id]["status"] == "dead":
                self.workers[worker_id]["status"] = "ready"
                self.workers[worker_id]["current_stage"] = None
                LOGGER.info(
                    "Recognized Dead worker%s is now alive",
                    worker_id
                )
            elif self.workers[worker_id]["status"] == "busy":
                # reassign task
                task_id = (
                    self.workers[worker_id]["current_task_id"]
                )
                # split into two cases
                self.append_failed_task(worker_id, task_id)
                self.workers[worker_id]["status"] = "ready"
                self.workers[worker_id]["current_stage"] = None
                LOGGER.info(
                    "Unrecognized Dead worker%s is now alive",
                    worker_id
                )
        else:
            LOGGER.info("create a new worker object here!!!")
            self.workers[worker_id] = {
                "status": "ready",  # ready, busy, dead
                "current_task_id": None,
                "current_stage": None,
                "last_ping": time.time()
            }

            LOGGER.info("New worker registered: %s", worker_id)

    def manager_udp_server(self, host, port):
        """Construct a Manager instance and start listening for messages."""
        # Create an INET, DGRAM socket, this is UDP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock3:
            # Bind the UDP socket to the server
            sock3.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock3.bind((host, port))
            sock3.settimeout(0.1)
            # Receive incoming UDP messages
            while not self.signals["shutdown"]:
                try:
                    message_bytes = sock3.recv(4096)
                except socket.timeout:
                    continue
                message_str = message_bytes.decode("utf-8")
                message_dict = json.loads(message_str)
                if message_dict["message_type"] == "heartbeat":
                    worker_host = message_dict["worker_host"]
                    worker_port = message_dict["worker_port"]
                    worker_id = (worker_host, worker_port)
                    if worker_id not in self.workers:
                        continue
                    self.workers[worker_id]["last_ping"] = time.time()
                    # it looks like we don't have the worker yet. WHY?
                    # update the worker status if it was dead
                    if self.workers[worker_id]["status"] == "dead":
                        self.workers[worker_id]["status"] = "ready"
                        LOGGER.info("Worker %s is alive again!", worker_id)

    def fault_tolerance_thread(self):
        """Construct a Manager instance and start listening for messages."""
        while not self.signals["shutdown"]:
            for worker_id, worker in self.workers.items():
                if worker["last_ping"] is None:
                    continue
                if time.time() - worker["last_ping"] > 10 or \
                        worker["status"] == "dead":
                    # the worker is dead
                    if worker["status"] == "busy":
                        task_id = worker["current_task_id"]
                        self.append_failed_task(worker_id, task_id)
                        worker["status"] = "dead"
                        LOGGER.info("worker is dead")

                    worker["current_task_id"] = None
                    worker["current_stage"] = None
            time.sleep(0.1)

    def run_job(self):
        """Construct a Manager instance and start listening for messages."""
        # while not self.signals["shutdown"]:
        print("Signal in runjob top:", self.signals["shutdown"])
        if self.job_queue:
            # Wait for a job to be available in the queue or check periodically
            job = self.job_queue.get()  # Adjust timeout as necessary
            files = []
            for filename in os.listdir(job['input_directory']):
                file_path = os.path.join(job['input_directory'], filename)
                if os.path.isfile(file_path):
                    # Add the file to the list only if it is a regular file
                    files.append(filename)
            # Sort the list of files by name
            sorted_files = sorted(files)

            # a list of tuples [[0,[]], [1,[]], ...]
            self.current_task = [[j, []] for j in range(job['num_mappers'])]

            for i, file_name in enumerate(sorted_files):
                mapper_index = i % job['num_mappers']
                self.current_task[mapper_index][1].append(file_name)

            # self.current_task = [[] for _ in range(job['num_mappers'])]
            # for i, file_name in enumerate(sorted_files):
            #     mapper_index = i % job['num_mappers']
            #     self.current_task[mapper_index].append(file_name)

            self.copy_task = copy.deepcopy(self.current_task)

            LOGGER.info("Starting job %s", job['job_id'])
            # delete output directory
            output_directory = job["output_directory"]
            if os.path.exists(output_directory):
                shutil.rmtree(output_directory)
                LOGGER.info(
                    "Deleted existing output directory: %s",
                    output_directory
                )

            # Create the output directory
            os.makedirs(output_directory)
            LOGGER.info("Created output directory: %s", output_directory)

            # Create a shared directory for temporary intermediate files
            prefix = f"mapreduce-shared-job{job['job_id']:05d}-"
            with tempfile.TemporaryDirectory(prefix=prefix) as tmpdir:
                LOGGER.info("Created tmpdir %s", tmpdir)

                # run mapping job
                while (not self.signals["shutdown"]) \
                        and (self.job_info["finished_job_tasks"]
                             != job['num_mappers']):
                    if self.current_task:
                        self.send_mapping_tasks(job, tmpdir)
                    print("Stuck here 1")
                    time.sleep(0.1)

                self.copy_task.clear()
                self.current_task.clear()
                # create reduce tasks, this is overwritten by a new empty list
                self.current_task = [[] for _ in range(job['num_reducers'])]
                sorted_dir = sorted(os.listdir(tmpdir))
                for partition_file in sorted_dir:
                    # partition file is "123.txt"
                    task_reduce_id = int(partition_file[-5:])
                    file_path = os.path.join(tmpdir, partition_file)
                    self.current_task[task_reduce_id].append(file_path)

                self.copy_task = copy.deepcopy(self.current_task)

                # run reducing job
                while (not self.signals["shutdown"]) \
                        and (self.job_info["finished_job_tasks"] !=
                             job['num_mappers'] + job['num_reducers']):
                    if self.current_task:
                        self.send_reducing_tasks(job)
                    print("Stuck here 2")
                    time.sleep(0.1)

                self.copy_task.clear()
                self.current_task.clear()
        time.sleep(0.1)

    def send_mapping_tasks(self, job, tmpdir):
        """Construct a Manager instance and start listening for messages."""
        try:  # a list of tuples [[0,[]], [1,[]], ...]
            for worker_id, worker in self.workers.items():
                if worker['status'] == "ready":
                    task_map_id = self.current_task[0][0]
                    worker['current_task_id'] = task_map_id
                    worker['current_stage'] = "mapping"
                    worker['status'] = "busy"
                    with socket.socket(
                        socket.AF_INET, socket.SOCK_STREAM
                    ) as sock4:
                        worker_host, worker_port = worker_id
                        sock4.connect((worker_host, worker_port))
                        input_paths = []
                        for file in self.current_task[0][1]:
                            # the list of files for the first task
                            input_paths.append(
                                str(job['input_directory']) + '/' + str(file)
                            )
                        context = {
                                    "message_type": "new_map_task",
                                    "task_id": task_map_id,
                                    "input_paths": input_paths,
                                    "executable": job['mapper_executable'],
                                    "output_directory": tmpdir,
                                    "num_partitions": job['num_reducers']
                                }
                        message = json.dumps(context)
                        sock4.sendall(message.encode('utf-8'))
                    self.current_task.pop(0)
                    break

        except ConnectionRefusedError:
            worker["status"] = "dead"
            LOGGER.info("ConnectionRefusedError")

    def send_reducing_tasks(self, job):
        """Construct a Manager instance and start listening for messages."""
        try:
            for worker_id, worker in self.workers.items():
                if worker['status'] == "ready":
                    extract_id = self.current_task[0][0]
                    task_reduce_id = int(extract_id[-5:])
                    worker['current_task_id'] = task_reduce_id
                    worker['current_stage'] = "reducing"
                    worker['status'] = "busy"
                    LOGGER.info("HEY there")
                    LOGGER.info(self.current_task[0])
                    with socket.socket(
                        socket.AF_INET, socket.SOCK_STREAM
                    ) as sock5:
                        worker_host, worker_port = worker_id
                        sock5.connect((worker_host, worker_port))
                        context = {
                                    "message_type": "new_reduce_task",
                                    "task_id": task_reduce_id,
                                    "executable": job['reducer_executable'],
                                    "input_paths": self.current_task[0],
                                    "output_directory":
                                    job['output_directory'],
                                }
                        message = json.dumps(context)
                        sock5.sendall(message.encode('utf-8'))
                    self.current_task.pop(0)
                    break
        except ConnectionRefusedError:
            worker["status"] = "dead"
            LOGGER.info("ConnectionRefusedError")

    def con_err_refuse(self, worker_id):
        """Construct a Manager instance and start listening for messages."""
        if self.workers[worker_id]["status"] == "busy":
            task_id = self.workers[worker_id]["current_task_id"]
            self.append_failed_task(worker_id, task_id)
        self.workers[worker_id]["status"] = "dead"

    def append_failed_task(self, worker_id, task_id):
        """Construct a Manager instance and start listening for messages."""
        if self.workers[worker_id]["current_stage"] == "mapping":
            for task in self.copy_task:
                if task[0] == task_id:
                    self.current_task.append(task)
                    break
        elif self.workers[worker_id]["current_stage"] == "reducing":
            self.current_task.append(self.copy_task[task_id])


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
