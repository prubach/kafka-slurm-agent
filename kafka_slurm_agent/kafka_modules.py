import ast
import json
import logging
import math
import os.path
import socket
import sys
import traceback
import urllib
import datetime
from urllib.error import URLError

from confluent_kafka import Consumer, Producer
from simple_slurm import Slurm
import getpass
from os.path import expanduser

from kafka_slurm_agent.command import Command
from kafka_slurm_agent.config_module import Config

CONFIG_FILE = 'kafkaslurm_cfg.py'

config_defaults = {
    'CLUSTER_NAME': 'my_cluster',
    'POLL_INTERVAL': 30.0,
    'BOOTSTRAP_SERVERS': 'localhost:9092',
    'MONITOR_AGENT_URL': 'http://localhost:6066/',
    'PREFIX': 'kafka_slurm_agent',
    'KAFKA_FAUST_BROKER_CREDENTIALS': None,
    'KAFKA_SECURITY_PROTOCOL': 'PLAINTEXT',
    'KAFKA_SASL_MECHANISM': None,
    'KAFKA_USERNAME': None,
    'KAFKA_PASSWORD': None,
    'WORKER_AGENT_MAX_WORKERS': 2
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
    def __init__(self, input_job_id):
        self.input_job_id = input_job_id
        self.slurm_job_id = int(os.getenv('SLURM_JOB_ID', -1))
        self.ss = StatusSender()
        self.rs = ResultsSender()
        self.logger = setupLogger(config['LOGS_DIR'], "clustercomputing")
        self.results = {'job_id': self.slurm_job_id, 'node': socket.gethostname(), 'cluster': config['CLUSTER_NAME']}

    def do_compute(self):
        pass

    def compute(self):
        self.ss.send(self.struct_name, 'RUNNING', job_id=self.slurm_job_id, node=socket.gethostname())
        try:
            self.do_compute()
            #self.rs.send(self.struct_name, self.results)
            self.ss.send(self.struct_name, 'DONE', job_id=self.slurm_job_id, node=socket.gethostname())
        except Exception as e:
            desc_exc = traceback.format_exc()[:10000]
            self.ss.send(self.struct_name, 'ERROR', job_id=self.slurm_job_id, node=socket.gethostname(), error=desc_exc)
            self.logger.error(desc_exc)


class KafkaSender:
    def __init__(self):
        cfg = {'bootstrap.servers' : config['BOOTSTRAP_SERVERS'],
                                  'client.id': '{}_{}'.format(config['CLUSTER_NAME'], self.__class__.__name__.lower()),
                                  'security.protocol': config['KAFKA_SECURITY_PROTOCOL']}
        if config['KAFKA_SASL_MECHANISM']:
            cfg.update({'sasl.mechanism': config['KAFKA_SASL_MECHANISM'],
                        'sasl.username': config['KAFKA_USERNAME'],
                        'sasl.password': config['KAFKA_PASSWORD']})
        self.producer = Producer(cfg)


class StatusSender(KafkaSender):
    def send(self, jobid, status, job_id=None, node=None, error=None):
        val = {'status': status, 'cluster': config['CLUSTER_NAME'], 'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        if job_id:
            val['job_id'] = job_id
        if node:
            val['node'] = node
        if error:
            val['error'] = error
        self.producer.produce(config['TOPIC_STATUS'], key=jobid.encode('utf-8'), value=val)

    def remove(self, jobid):
        self.producer.send(config['TOPIC_STATUS'], key=jobid.encode('utf-8'), value=None)


class ResultsSender(KafkaSender):
    @staticmethod
    def delivery_report(err, msg):
        if err is not None:
            print('Results not delivered!!!: {}'.format(err))
        else:
            print('Results delivered to {} [{}]'.format(msg.topic(), msg.partition()))

    def send(self, jobid, results):
        results['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.producer.produce(config['TOPIC_DONE'], key=jobid.encode('utf-8'), value={'results': results},
                              callback=ResultsSender.delivery_report)


class ErrorSender(KafkaSender):
    def send(self, jobid, results, error):
        results['results']['error'] = str(error)
        results['results']['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.producer.send(config['TOPIC_ERROR'], key=jobid.encode('utf-8'), value=results)


class JobSubmitter(KafkaSender):
    @staticmethod
    def delivery_report(err, msg):
        if err is not None:
            print('Job not submitted!!!: {}'.format(err))
        else:
            print('Job submitted to {} [{}]'.format(msg.topic(), msg.partition()))

    def send(self, s_id, script='my_job.py', slurm_pars={'RESOURCES_REQUIRED': 1, 'JOB_TYPE': 'gpu'}, check=True, flush=True, ignore_error_status=False):
        status = None
        if check:
            status = self.check_status(s_id)
            if status is not None:
                if config['DEBUG']:
                    print('{} already processed: {}'.format(s_id, status))
                if not ignore_error_status or (ignore_error_status and status != 'ERROR'):
                    return s_id, False, status
        self.producer.produce(config['TOPIC_NEW'], key=s_id.encode('utf-8'),
                              value=str({'input_job_id': s_id, 'script': script,
                                     'slurm_pars': slurm_pars,
                                     'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}).encode('utf-8'),
                              callback=JobSubmitter.delivery_report)
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

    def send_many(self, ids, script='my_job.py', slurm_pars={'RESOURCES_REQUIRED': 1, 'JOB_TYPE': 'gpu'}, ignore_error_status=False):
        results = []
        for s_id in ids:
            results.append(self.send(s_id, script=script, slurm_pars=slurm_pars, flush=False, ignore_error_status=ignore_error_status))
        self.producer.flush()
        return results

    def __del__(self):
        self.producer.flush()


class ClusterAgentException(Exception):
    pass


class WorkingAgent:
    def __init__(self):
        cfg = {'bootstrap.servers': config['BOOTSTRAP_SERVERS'],
               'security.protocol': config['KAFKA_SECURITY_PROTOCOL'],
               'enable.auto.commit': False,
               'heartbeat.interval.ms': 2000,
               'group.id': config['CLUSTER_AGENT_NEW_GROUP']}
        if config['KAFKA_SASL_MECHANISM']:
            cfg.update({'sasl.mechanism': config['KAFKA_SASL_MECHANISM'],
                        'sasl.username': config['KAFKA_USERNAME'],
                        'sasl.password': config['KAFKA_PASSWORD']})
        self.consumer = Consumer(cfg)
        self.consumer.subscribe([config['TOPIC_NEW']])
        self.stat_send = StatusSender()
        self.script_name = None
        self.logger = setupLogger(config['LOGS_DIR'], "clusteragent")
        self.logger.info('Cluster Agent Started')
        self.job_name_suffix = '_CLAG'

    def get_job_name(self, input_job_id):
        # TODO - override the method according to your needs
        return input_job_id

    def get_job_type(self, slurm_pars):
        return slurm_pars['JOB_TYPE'] if slurm_pars and 'JOB_TYPE' in slurm_pars else config['SLURM_JOB_TYPE']

    def is_job_gpu(self, slurm_pars):
        return self.get_job_type(slurm_pars) == 'gpu'

    def get_runner_batch_cmd(self, input_job_id, script, msg=None):
        # TODO - override the method according to your needs
        cmd = os.path.join(config['PREFIX'], 'venv', 'bin', 'python') + ' ' + script + ' ' + str(input_job_id)
        if msg:
            cmd += ' ' + str(msg)
        #print(cmd)
        return cmd


class WorkerAgent(WorkingAgent):
    def __init__(self):
        super(WorkerAgent, self).__init__()
        self.workers = config['WORKER_AGENT_MAX_WORKERS']

    def check_queue_submit(self):
        #self.logger.info('Free {}s: {}'.format(config['SLURM_JOB_TYPE'].upper(), free))
        runners = 1 # check how many are running
        while runners>0:
            job = self.consumer.poll(2.0)
            #self.logger.info('Got {} new jobs'.format(len(new_jobs)))
            if job is None:
                continue
            if job.error():
                self.logger.error("Consumer error: {}".format(job.error()))
            self.logger.debug(job)
            msg = ast.literal_eval(job.value().decode('utf-8'))
            self.logger.debug(msg['input_job_id'])
            job_id = self.submit_slurm_job(msg['input_job_id'], msg['script'], msg['slurm_pars'], msg)
            self.stat_send.send(msg['input_job_id'], 'SUBMITTED', int(job_id))
            self.consumer.commit()


class ClusterAgent(WorkingAgent):
    def __init__(self):
        super(ClusterAgent, self).__init__()

    def check_queue_submit(self):
        func_name = 'self.slurm_get_idle_' + self.get_job_type(None) + 's'
        free = eval(func_name + "()")
        self.logger.info('Free {}s: {}'.format(config['SLURM_JOB_TYPE'].upper(), free))
        w = self.slurm_check_jobs_waiting()
        self.logger.info('Waiting: {}'.format(w))
        if w <= 1:
            to_poll = max(math.floor(free / config['SLURM_RESOURCES_REQUIRED']), 1)
            self.logger.info('Polling: {}'.format(max(math.floor(free/config['SLURM_RESOURCES_REQUIRED']), 1)))
            for i in range(to_poll):
                job = self.consumer.poll(2.0)
                #self.logger.info('Got {} new jobs'.format(len(new_jobs)))
                if job is None:
                    continue
                if job.error():
                    self.logger.error("Consumer error: {}".format(job.error()))
                self.logger.debug(job)
                msg = ast.literal_eval(job.value().decode('utf-8'))
                self.logger.debug(msg['input_job_id'])
                job_id = self.submit_slurm_job(msg['input_job_id'], msg['script'], msg['slurm_pars'], msg)
                self.stat_send.send(msg['input_job_id'], 'SUBMITTED', int(job_id))
            self.consumer.commit()

    @staticmethod
    def slurm_check_job_status(job_id):
        cmd = 'squeue -o "%i %R" | grep ' + str(job_id)
        comd = Command(cmd)
        comd.run(10)
        res = comd.getOut()
        if res:
            res = res.splitlines()[0]
            res = ''.join(res.strip().split(" ")[1:])
            return 'WAITING' if res.startswith('(') else 'RUNNING', res
        else:
            return None, None

    def submit_slurm_job(self, input_job_id, script, slurm_params, msg=None):
        if not script:
            script = self.script_name
        job_name = self.get_job_name(input_job_id)
        prefix = config['PREFIX']
        slurm_pars = {'cpus_per_task': slurm_params['RESOURCES_REQUIRED'] if slurm_params and 'RESOURCES_REQUIRED' in slurm_params else config['SLURM_RESOURCES_REQUIRED'],
                      'job_name': job_name,
                      'partition': config['SLURM_PARTITION'],
                      'output': f'{prefix}slurm/{job_name}-{Slurm.JOB_ARRAY_MASTER_ID}.out'
                      }
        if 'MEM' in slurm_params:
            slurm_pars['mem'] = slurm_params['MEM']
        if self.is_job_gpu(slurm_params):
            slurm_pars['gres'] = 'gpu'
        slurm = Slurm(**slurm_pars)
        slurm_job_id = slurm.sbatch(self.get_runner_batch_cmd(input_job_id, script, msg))
        self.logger.info('Submitted: {}, id: {}'.format(input_job_id, slurm_job_id))
        return slurm_job_id

    def slurm_check_jobs_waiting(self):
        res = self.run_command('squeue -o "%j %R %u" | grep ' + getpass.getuser() + ' | grep ' + self.job_name_suffix)
        waiting = 0
        if res:
            lines = res.splitlines()
            for line in lines:
                jobname, status, user = line.strip().split(" ")
                if jobname.endswith(self.job_name_suffix) and status.startswith('('):
                    waiting += 1
        return waiting

    @staticmethod
    def run_command(cmd):
        comd = Command(cmd)
        comd.run(10)
        return comd.getOut()

    @staticmethod
    def slurm_get_idle_gpus(state='idle'):
        res = ClusterAgent.run_command('sinfo -o "%G %.3D %.6t %P" | grep ' + state + ' | grep gpu | grep ' + config['SLURM_PARTITION'] + "| awk '{print $1,$2}'")
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
        res = ClusterAgent.run_command('sinfo -o "%C %.3D %.6t %P" | grep idle | grep ' + config['SLURM_PARTITION'] + "| awk '{print $1,$2}'")
        if res:
            lines = res.splitlines()
            cpus = 0
            for line in lines:
                els = line.strip().split(" ")
                cpus += int(els[0].split("/")[1].strip())
            return cpus
        else:
            return 0


class DataUpdaterException(Exception):
    pass


class DataUpdater:
    def __init__(self):
        pass

    def run(self, key, value):
        pass


if __name__ == '__main__':
    pass
