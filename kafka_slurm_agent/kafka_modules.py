import ast
import json
import logging
import math
import os.path
import socket
import sys
import tempfile
import time
import traceback
import urllib
import datetime
import uuid
from queue import Queue
from threading import Thread
from urllib.error import URLError

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
from simple_slurm import Slurm
import getpass
from os.path import expanduser

from wrapt_timeout_decorator import timeout

from kafka_slurm_agent.command import Command
from kafka_slurm_agent.config_module import Config

CONFIG_FILE = 'kafkaslurm_cfg.py'

config_defaults = {
    'CLUSTER_NAME': 'my_cluster',
    'POLL_INTERVAL': 30.0,
    'BOOTSTRAP_SERVERS': 'localhost:9092',
    'MONITOR_AGENT_URL': 'http://localhost:6066/',
    'KAFKA_FAUST_BROKER_CREDENTIALS': None,
    'KAFKA_SECURITY_PROTOCOL': 'PLAINTEXT',
    'KAFKA_SASL_MECHANISM': None,
    'KAFKA_USERNAME': None,
    'KAFKA_PASSWORD': None,
    'WORKER_AGENT_MAX_WORKERS': 2,
    'WORKER_JOB_TIMEOUT': 86400,  # = 24h
    'HEARTBEAT_INTERVAL': 0.0,
    'KAFKA_CONSUMER_HEARTBEAT_INTERVAL_MS': 3000,
    'MONITOR_HEARTBEAT_INTERVAL_MS': 3000,
    'MONITOR_ONLY_DO_NOT_SUBMIT': False
}


class ConfigLoader:
    def __init__(self):
        self.config = None

    def get(self):
        if not self.config:
            self.load_config()
        return self.config

    def load_config(self):
        rootpath = expanduser('~')
        if not os.path.isfile(os.path.join(rootpath, CONFIG_FILE)):
            rootpath = os.path.abspath(os.path.dirname(__file__))
            while not os.path.isfile(os.path.join(rootpath, CONFIG_FILE)) and rootpath != os.path.abspath(os.sep):
                rootpath = os.path.abspath(os.path.dirname(rootpath))
        if not os.path.isfile(os.path.join(rootpath, CONFIG_FILE)):
            print(
                '{} configuration file not found in home folder or any parent folders of where the app is installed!'.format(
                    CONFIG_FILE))
            sys.exit(-1)
        config_defaults['PREFIX'] = rootpath
        config_defaults['SHARED_TMP'] = os.path.join(rootpath, 'tmp')
        self.config = Config(root_path=rootpath, defaults=config_defaults)
        self.config.from_pyfile(CONFIG_FILE)

config = ConfigLoader().get()


def setupLogger(directory, name, file_name=None):
    if not file_name:
        file_name = name + '.log'
    os.makedirs(directory, exist_ok=True)
    logger = logging.getLogger(name)
    hlogger = logging.FileHandler(os.path.join(directory, file_name))
    formatter = logging.Formatter('%(asctime)s %(name)s || %(levelname)s %(message)s')
    hlogger.setFormatter(formatter)
    logger.addHandler(hlogger)
    logger.setLevel(logging.INFO)
    return logger


class ClusterComputing:
    def __init__(self, input_args):
        self.input_job_id = input_args[1]
        if len(input_args) > 2:
            cfg_file = input_args[2].split('cfg_file=')[1]
            with open(cfg_file) as json_file:
                self.job_config = json.load(json_file)
        if len(input_args) > 3:
            self.slurm_job_id = input_args[3].split('job_id=')[1]
        else:
            self.slurm_job_id = os.getenv('SLURM_JOB_ID', -1)
        self.ss = StatusSender()
        self.rs = ResultsSender()
        self.logger = setupLogger(config['LOGS_DIR'], "clustercomputing")
        self.results = {'job_id': self.slurm_job_id, 'node': socket.gethostname(), 'cluster': config['CLUSTER_NAME']}

    def do_compute(self):
        pass

    def do_compute_timeout(self):
        timeout(dec_timeout=self.timeout, use_signals=False)(self.do_compute)()

    def compute(self):
        self.timeout = None
        if 'TIMEOUT' in self.job_config['slurm_pars'] and self.job_config['slurm_pars']['TIMEOUT']:
            self.timeout = int(self.job_config['slurm_pars']['TIMEOUT'])
            print('timeout from job config: {}'.format(self.timeout))
        self.ss.send(self.input_job_id, 'RUNNING', job_id=self.slurm_job_id, node=socket.gethostname())
        if 'ExecutorType' in self.job_config and self.job_config['ExecutorType']=='WRK_AGNT':
            #if self.timeout:
            #    try:
            #        self.do_compute_timeout()
            #    except TimeoutError as te:
            #        self.ss.send(self.input_job_id, 'TIMEOUT', job_id=self.slurm_job_id, node=socket.gethostname(),
            #                     error='Timeout after {}'.format(self.timeout))
            #        self.logger.error('Timeout job {} after {}'.format(self.input_job_id, self.slurm_job_id,
            #                                                                       self.timeout))
            #else:
            self.do_compute()
            self.ss.send(self.input_job_id, 'DONE', job_id=self.slurm_job_id, node=socket.gethostname())
        else:
            try:
                if self.timeout:
                    self.do_compute_timeout()
                else:
                    self.do_compute()
                self.ss.send(self.input_job_id, 'DONE', job_id=self.slurm_job_id, node=socket.gethostname())
            except TimeoutError as te:
                print('TIMEOUT fot job: input ID {} Slurm ID {} after {}'.format(self.input_job_id, self.slurm_job_id, self.timeout))
                self.ss.send(self.input_job_id, 'TIMEOUT', job_id=self.slurm_job_id, node=socket.gethostname(),
                             error='Timeout after {}'.format(self.timeout))
                self.logger.error('Timeout job {} slurm ID {} after {}'.format(self.input_job_id, self.slurm_job_id, self.timeout))
            except Exception as e:
                desc_exc = traceback.format_exc()
                self.ss.send(self.input_job_id, 'ERROR', job_id=self.slurm_job_id, node=socket.gethostname(), error=str(e) + '\n' + desc_exc[:2000] if len(desc_exc)>2000 else desc_exc)
                self.logger.error(desc_exc)
                self.logger.error(str(e))
            self.ss.producer.flush()

    def __del__(self):
        self.ss.producer.flush()
        self.rs.producer.flush()


class KafkaSender:
    def __init__(self):
        try:
            self.producer = self.init_producer()
        except NoBrokersAvailable as e:
            time.sleep(2)
            self.producer = self.init_producer()

    def init_producer(self):
        return KafkaProducer(bootstrap_servers=config['BOOTSTRAP_SERVERS'],
                                      client_id='{}_{}'.format(config['CLUSTER_NAME'], self.__class__.__name__.lower()),
                                      security_protocol=config['KAFKA_SECURITY_PROTOCOL'],
                                      sasl_mechanism=config['KAFKA_SASL_MECHANISM'],
                                      sasl_plain_username=config['KAFKA_USERNAME'],
                                      sasl_plain_password=config['KAFKA_PASSWORD'],
                                      value_serializer=lambda v: json.dumps(v).encode('utf-8'))


class StatusSender(KafkaSender):
    def send(self, jobid, status, job_id=None, node=None, error=None, custom_msg=None):
        val = {'status': status, 'cluster': config['CLUSTER_NAME'], 'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if job_id:
            val['job_id'] = job_id
        if node:
            val['node'] = node
        if error:
            val['error'] = error
        if custom_msg:
            val['message'] = custom_msg
        self.producer.send(config['TOPIC_STATUS'], key=jobid.encode('utf-8'), value=val)

    def remove(self, jobid):
        self.producer.send(config['TOPIC_STATUS'], key=jobid.encode('utf-8'), value=None)


class ResultsSender(KafkaSender):
    def send(self, jobid, results):
        results['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.producer.send(config['TOPIC_DONE'], key=jobid.encode('utf-8'), value={'results': results})


class HeartbeatSender(KafkaSender):
    def send(self):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.producer.send(config['TOPIC_HEARTBEAT'], key=config['CLUSTER_NAME'].encode('utf-8'), value={'timestamp': timestamp})


class ErrorSender(KafkaSender):
    def send(self, jobid, results, error):
        results['results']['error'] = str(error)
        results['results']['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.producer.send(config['TOPIC_ERROR'], key=jobid.encode('utf-8'), value=results)


class JobSubmitter(KafkaSender):
    def send(self, s_id, script='my_job.py', slurm_pars={'RESOURCES_REQUIRED': 1, 'JOB_TYPE': 'gpu'}, check=True, flush=True, ignore_error_status=False):
        status = None
        if check:
            status = self.check_status(s_id)
            if status is not None:
                if config['DEBUG']:
                    print('{} already processed: {}'.format(s_id, status))
                if not ignore_error_status or (ignore_error_status and status != 'ERROR'):
                    return s_id, False, status
        self.producer.send(config['TOPIC_NEW'], key=s_id.encode('utf-8'), value={'input_job_id': s_id, 'script': script,
                                                                                 'slurm_pars': slurm_pars,
                                                                                 'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        if flush:
            self.producer.flush()
        return s_id, True, status

    @staticmethod
    def check_status(s_id):
        try:
            url = config['MONITOR_AGENT_URL'] + config['MONITOR_AGENT_CONTEXT_PATH'] + 'check/' + s_id + '/'
            response = urllib.request.urlopen(url)
            res = response.read().decode("utf-8")
            status = ast.literal_eval(res)
            if status[s_id]:
                return status[s_id]['status']
            else:
                return None
        except URLError as e:
            raise ClusterAgentException('Cannot reach Monitor Agent at: ' + url)

    def send_many(self, ids, script='my_job.py', slurm_pars={'RESOURCES_REQUIRED': 1, 'JOB_TYPE': 'gpu'}, check=True, ignore_error_status=False):
        results = []
        for s_id in ids:
            results.append(self.send(s_id, script=script, slurm_pars=slurm_pars, check=check, flush=False, ignore_error_status=ignore_error_status))
        self.producer.flush()
        return results

    def __del__(self):
        self.producer.flush()


class ClusterAgentException(Exception):
    pass


class WorkerRunner(Thread):
    def __init__(self, queue, logger, stat_send, processing):
        Thread.__init__(self)
        self.queue = queue
        self.logger = logger
        self.stat_send = stat_send
        self.processing = processing

    def run(self):
        while True:
            job_id, input_job_id, cmd, time_out = self.queue.get()
            finished_ok = False
            rcode = -1000
            try:
                self.logger.info('Starting job {}: {}'.format(job_id, cmd))
                #self.stat_send.send(input_job_id, 'RUNNING', job_id, node=socket.gethostname())
                self.processing.append(input_job_id)
                os.environ["SLURM_JOB_ID"] = job_id
                if not time_out:
                    time_out = config['WORKER_JOB_TIMEOUT'] 
                rcode, out, error = WorkingAgent.run_command(cmd, time_out)
                if rcode != 0:
                    finished_ok = False
                    self.logger.error('Return code {}: {}'.format(job_id, rcode))
                    self.logger.error('OUT[{}]: {}'.format(job_id, out))
                    self.logger.error('ERROR[{}]: {}'.format(job_id, error))
                else:
                    self.logger.info('Return code {}: {}'.format(job_id, rcode))
                    self.logger.info('OUT[{}]: {}'.format(job_id, out))
                    self.logger.info('ERROR[{}]: {}'.format(job_id, error))
                    finished_ok = True
                    #self.stat_send.send(input_job_id, 'DONE', job_id, node=socket.gethostname())
                self.logger.info('Finished job {}: {}'.format(job_id, cmd))
            except TimeoutError as te:
                self.stat_send.send(input_job_id, 'TIMEOUT', job_id, node=socket.gethostname(),
                                    error='Timeout {}: {}, {}'.format(input_job_id, job_id, time_out))
                finished_ok = True                
                self.logger.error('TIMEOUT [{}: {}, {}]'.format(input_job_id, job_id, time_out))
            finally:
                self.processing.remove(input_job_id)
                if not finished_ok:
                    self.logger.info('Sending ERROR job {}: {}'.format(job_id, cmd))
                    self.stat_send.send(input_job_id, 'ERROR', job_id, node=socket.gethostname(), error='{}: {}, {}'.format(rcode, out, error[:2000] if len(error)>2000 else error))
                else:
                    self.logger.info('Finalizing job {}: {}'.format(job_id, cmd))
                self.queue.task_done()


class WorkingAgent:
    def __init__(self):
        self.consumer = KafkaConsumer(config['TOPIC_NEW'],
                                 bootstrap_servers=config['BOOTSTRAP_SERVERS'],
                                 security_protocol=config['KAFKA_SECURITY_PROTOCOL'],
                                 sasl_mechanism=config['KAFKA_SASL_MECHANISM'],
                                 sasl_plain_username=config['KAFKA_USERNAME'],
                                 sasl_plain_password=config['KAFKA_PASSWORD'],
                                 enable_auto_commit=False,
                                 heartbeat_interval_ms=config['KAFKA_CONSUMER_HEARTBEAT_INTERVAL_MS'],
                                 group_id=config['CLUSTER_AGENT_NEW_GROUP'],
                                 value_deserializer=lambda x: json.loads(x.decode('utf-8')))
        self.stat_send = StatusSender()
        self.script_name = None
        self.job_name_suffix = '_CLAG'

    def get_job_name(self, input_job_id):
        # TODO - override the method according to your needs
        return input_job_id + self.job_name_suffix

    def get_job_type(self, slurm_pars):
        return slurm_pars['JOB_TYPE'] if slurm_pars and 'JOB_TYPE' in slurm_pars else config['SLURM_JOB_TYPE']

    def is_job_gpu(self, slurm_pars):
        return self.get_job_type(slurm_pars) == 'gpu'

    def write_job_config(self, job_config):
        if not os.path.isdir(config['SHARED_TMP']):
            os.makedirs(config['SHARED_TMP'])
        tfile = tempfile.NamedTemporaryFile(dir=config['SHARED_TMP'], mode="w+", delete=False)
        json.dump(job_config, tfile)
        tfile.flush()
        return tfile.name

    def get_runner_batch_cmd(self, input_job_id, script, msg=None, job_id=None):
        # TODO - override the method according to your needs
        cmd = os.path.join(config['PREFIX'], 'venv', 'bin', 'python') + ' ' + script + ' ' + str(input_job_id)
        time_out = None
        if msg:
            if 'TIMEOUT' in msg['slurm_pars']:
                time_out = int(msg['slurm_pars']['TIMEOUT'])
            cmd += ' cfg_file=' + self.write_job_config(msg)
        if job_id:
            cmd += ' job_id=' + str(job_id)
        return cmd, time_out

    @staticmethod
    def run_command(cmd, timeout=10):
        comd = Command(cmd)
        comd.run(timeout=timeout)
        return comd.getReturnCode(), comd.getOut(), comd.getError()


class WorkerAgent(WorkingAgent):
    def __init__(self):
        super(WorkerAgent, self).__init__()
        self.logger = setupLogger(config['LOGS_DIR'], "workeragent")
        self.logger.info('Worker Agent Started')
        self.workers = config['WORKER_AGENT_MAX_WORKERS']
        self.queue = Queue()
        self.processing = []
        self.start_workers()

    @staticmethod
    def unique_id():
        return hex(uuid.uuid4().time)[2:-1]

    def check_queue_submit(self):
        i = 0
        while self.queue.qsize() < self.workers and i < self.workers*4:
            i += 1
            new_jobs = self.consumer.poll(max_records=max(math.floor(self.workers / config['SLURM_RESOURCES_REQUIRED']), 1),
                                          timeout_ms=2000)
            #self.logger.info('Got {} new jobs'.format(len(new_jobs)))
            for job in new_jobs.items():
                self.logger.info(job)
                for el in job[1]:
                    msg = el.value
                    msg['ExecutorType'] = 'WRK_AGNT'
                    self.logger.debug(msg['input_job_id'])
                    job_id = self.unique_id()
                    cmd, time_out = self.get_runner_batch_cmd(msg['input_job_id'], msg['script'], msg, job_id)
                    self.queue.put((job_id, msg['input_job_id'], cmd, time_out))
                    self.stat_send.send(msg['input_job_id'], 'SUBMITTED', job_id)
            self.consumer.commit()

    def check_job_status(self, input_job_id):
        if input_job_id in self.processing:
            return 'RUNNING', None, None
        else:
            return None, None, None

    def start_workers(self):
        for n in range(self.workers):
            worker = WorkerRunner(self.queue, self.logger, self.stat_send, self.processing)
            worker.daemon = True
            worker.start()
        self.queue.join()


class ClusterAgent(WorkingAgent):
    def __init__(self):
        super(ClusterAgent, self).__init__()
        self.logger = setupLogger(config['LOGS_DIR'], "clusteragent")
        self.logger.info('Cluster Agent Started')

    def check_queue_submit(self):
        func_name = 'self.slurm_get_idle_' + self.get_job_type(None) + 's'
        free = eval(func_name + "()")
        self.logger.info('Free {}s: {}'.format(config['SLURM_JOB_TYPE'].upper(), free))
        if 'SLURM_EXCLUDE' in config and config['SLURM_EXCLUDE'] != '':
            self.logger.info('Excluded nodes: {}/{}'.format(config['SLURM_EXCLUDE'], ClusterAgent.slurm_get_idle_excluded_cpus()))
        w = self.slurm_check_jobs_waiting()
        self.logger.info('Waiting: {}'.format(w))
        if w <= 1:
            self.logger.info('Polling: {}'.format(max(math.floor(free / config['SLURM_RESOURCES_REQUIRED']), 1)))
            new_jobs = self.consumer.poll(max_records=max(math.floor(free / config['SLURM_RESOURCES_REQUIRED']), 1),
                                          timeout_ms=2000)
            self.logger.info('Got {} new jobs'.format(len(new_jobs)))
            for job in new_jobs.items():
                self.logger.debug(job)
                for el in job[1]:
                    self.logger.debug(el.value['input_job_id'])
                    #msg = ast.literal_eval(job.value().decode('utf-8'))
                    job_id = self.submit_slurm_job(el.value['input_job_id'], el.value['script'], el.value['slurm_pars'], el.value)
                    self.stat_send.send(el.value['input_job_id'], 'SUBMITTED', job_id)
            self.consumer.commit()

    def check_job_statuses(self):
        cmd = 'squeue -o "%j %i %R %M %u" | grep {} | grep {}'.format(getpass.getuser(), self.job_name_suffix)
        comd = Command(cmd)
        comd.run(20)
        res = comd.getOut()
        statuses = {}
        if res:
            for line in res.splitlines():
                # 36315_AF2 596717 troll-8 6:53:54 prubach
                els = line.split(" ")
                input_job_id = ''.join(els[0][:-len(self.job_name_suffix)])
                slurm_job_id = ''.join(els[1])
                node = ''.join(els[2])
                run_time = ''.join(els[3])
                statuses[input_job_id] = (slurm_job_id, 'WAITING' if node.startswith('(') else 'RUNNING', node, self.parse_run_time(run_time))
        return statuses

    @staticmethod
    def check_job_status(job_id):
        cmd = 'squeue -o "%i %R %M" | grep ' + str(job_id)
        comd = Command(cmd)
        comd.run(10)
        res = comd.getOut()
        if res:
            res = res.splitlines()[0]
            node = ''.join(res.strip().split(" ")[1])
            run_time = ''.join(res.strip().split(" ")[2])
            return 'WAITING' if node.startswith('(') else 'RUNNING', node, ClusterAgent.parse_run_time(run_time)
        else:
            return None, None, None

    @staticmethod
    def parse_run_time(str_runtime):
        days = 0
        hrs = 0
        if str_runtime.find('-') > 0:
            days = int(str_runtime.split('-')[0])
            str_runtime = str_runtime.split('-')[1]
        hr_min_sec = str_runtime.split(':')
        if len(hr_min_sec) == 3:
            hrs = int(hr_min_sec[0])
            min = int(hr_min_sec[1])
            sec = int(hr_min_sec[2])
        else:
            min = int(hr_min_sec[0])
            sec = int(hr_min_sec[1])
        return days * 24 * 60 * 60 + hrs * 60 * 60 + min * 60 + sec

    @staticmethod
    def cancel_job(job_id):
        cmd = 'scancel {}'.format(job_id)
        comd = Command(cmd)
        comd.run(10)
        i = 0
        is_running = True
        while i < 30 and is_running:
            i+=1
            cmd = 'squeue -o "%i %R %M" | grep ' + str(job_id)
            comd = Command(cmd)
            comd.run(10)
            res = comd.getOut()
            if not res:
                return True
            time.sleep(1)
        return False

    def submit_slurm_job(self, input_job_id, script, slurm_params, msg=None):
        if not script:
            script = self.script_name
        job_name = self.get_job_name(input_job_id)
        slurm_out_dir = config['SLURM_OUT_DIR'] if 'SLURM_OUT_DIR' in config else config['PREFIX']
        slurm_pars = {'cpus_per_task': slurm_params[
            'RESOURCES_REQUIRED'] if slurm_params and 'RESOURCES_REQUIRED' in slurm_params else config[
            'SLURM_RESOURCES_REQUIRED'],
                      'job_name': job_name,
                      'partition': config['SLURM_PARTITION'],
                      'output': f'{slurm_out_dir}/{job_name}-{Slurm.JOB_ARRAY_MASTER_ID}.out'
                      }
        if 'MEM' in slurm_params:
            slurm_pars['mem'] = slurm_params['MEM']
        if self.is_job_gpu(slurm_params):
            slurm_pars['gres'] = 'gpu'
        if 'SLURM_EXCLUDE' in config and config['SLURM_EXCLUDE'] != '':
            slurm_pars['exclude'] = config['SLURM_EXCLUDE']
        slurm = Slurm(**slurm_pars)
        if msg:
            msg['ExecutorType'] = 'CL_AGNT'
        cmd, time_out = self.get_runner_batch_cmd(input_job_id, script, msg)
        slurm_job_id = slurm.sbatch(cmd)
        self.logger.info('Submitted: {}, id: {}'.format(input_job_id, slurm_job_id))
        return slurm_job_id

    def slurm_check_jobs_waiting(self):
        _, res, _ = self.run_command('squeue -o "%j %R %u" | grep ' + getpass.getuser() + ' | grep ' + self.job_name_suffix)
        waiting = 0
        if res:
            lines = res.splitlines()
            for line in lines:
                results = line.strip().split(" ")
                jobname, status, user = results[:3]
                if jobname.endswith(self.job_name_suffix) and status.startswith('(') and not status.startswith('(launch'):
                    waiting += 1
        return waiting

    @staticmethod
    def slurm_get_idle_gpus(state='idle'):
        _, res, _ = ClusterAgent.run_command('sinfo -o "%G %.3D %.6t %P" | grep ' + state + ' | grep gpu | grep ' + config['SLURM_PARTITION'] + "| awk '{print $1,$2}'")
        if res:
            lines = res.splitlines()
            gpus = 0
            for line in lines:
                els = line.strip().split(" ")
                gpus += int(els[0].split(":")[1].strip())*int(els[1].strip())
            return gpus
        else:
            return 0

    @staticmethod
    def slurm_get_idle_cpus():
        _, res, _ = ClusterAgent.run_command(
            'sinfo -o "%C %.3D %.6t %P" | grep idle | grep ' + config['SLURM_PARTITION'] + "| awk '{print $1,$2}'")
        cpus = 0
        if res:
            lines = res.splitlines()
            for line in lines:
                els = line.strip().split(" ")
                cpus += int(els[0].split("/")[1].strip())
        _, res, _ = ClusterAgent.run_command(
            'sinfo -o "%C %.3D %.6t %P" | grep mix | grep ' + config['SLURM_PARTITION'] + "| awk '{print $1,$2}'")
        if res:
            lines = res.splitlines()
            for line in lines:
                els = line.strip().split(" ")
                cpus += int(els[0].split("/")[1].strip())
        # Exclude cpus of excluded nodes
        excl_cpus = 0
        if 'SLURM_EXCLUDE' in config and config['SLURM_EXCLUDE'] != '':
            excl_cpus = ClusterAgent.slurm_get_idle_excluded_cpus()
        return cpus - excl_cpus

    @staticmethod
    def slurm_get_idle_excluded_cpus():
        excl_cpus = 0
        for node in config['SLURM_EXCLUDE'].split(','):
            _, res, _ = ClusterAgent.run_command(
                'sinfo --Node --long | grep ' + node + ' | grep ' + config['SLURM_PARTITION'] + "| awk '{print $5}'")
            if res:
                lines = res.splitlines()
                for line in lines:
                    excl_cpus += int(line.strip())
        return excl_cpus


class DataUpdaterException(Exception):
    pass


class DataUpdater:
    def __init__(self):
        pass

    def run(self, key, value):
        pass


if __name__ == '__main__':
    pass
