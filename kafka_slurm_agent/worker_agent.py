import ast
import os
import socket

import faust
import sys
from pydoc import locate
from kafka_slurm_agent.kafka_modules import config, HeartbeatSender
from concurrent.futures import ThreadPoolExecutor

app = faust.App(config['WORKER_NAME'] + '_worker_agent',
                group_id=1,
                broker='kafka://' + config['BOOTSTRAP_SERVERS'],
                broker_credentials=config['KAFKA_FAUST_BROKER_CREDENTIALS'],
                processing_guarantee='exactly_once',
                broker_max_poll_records=20,
                transaction_timeout_ms=120000,
                topic_partitions=1)
#store='rocksdb://',
jobs_topic = app.topic(config['TOPIC_STATUS'], partitions=1)
job_status = app.Table('job_status', default='')

thread_pool = ThreadPoolExecutor(max_workers=1)
sys.path.append(os.getcwd())
ca_class = locate(config['WORKER_AGENT_CLASS'])
ca = ca_class()
heartbeat_sender = HeartbeatSender()
current_jobs = {}


def run_cluster_agent_check():
    for key in list(job_status.keys()):
        if key in job_status.keys():
            js = ast.literal_eval(str(job_status[key]))
            if 'node' in js and js['node'] == socket.gethostname() and js['status'] in ['SUBMITTED', 'WAITING', 'RUNNING', 'UPLOADING']:
                status, reason = ca.check_job_status(js['job_id'])
                current_jobs[key] = {'job_id': js['job_id'], 'status': status, 'timestamp': js['timestamp']}
                if not status:
                    ca.stat_send.send(key, 'ERROR', js['job_id'], node=reason, error='Missing from worker queue')
                    if key in current_jobs.keys():
                        current_jobs.pop(key)
                elif js['status'] != status:
                    ca.stat_send.send(key, status, js['job_id'], node=reason)
                    current_jobs[key] = {'job_id': js['job_id'], 'status': status, 'timestamp': js['timestamp']}
                else:
                    current_jobs[key] = {'job_id': js['job_id'], 'status': status, 'timestamp': js['timestamp']}
            elif key in current_jobs.keys() and js['status'] in ['DONE', 'ERROR']:
                current_jobs.pop(key)
    ca.check_queue_submit()


@app.agent(jobs_topic)
async def process(stream):
    async for event in stream.events():
        job_status[event.key.decode('UTF-8')] = event.value


@app.timer(interval=config['POLL_INTERVAL'])
async def check_statuses(app):
    app.loop.run_in_executor(executor=thread_pool, func=run_cluster_agent_check)


@app.timer(interval=config['HEARTBEAT_INTERVAL'] if config['HEARTBEAT_INTERVAL'] > 0 else 1440000)
async def send_heartbeat(app):
    if config['HEARTBEAT_INTERVAL'] > 0:
        heartbeat_sender.send()


@app.page(config['WORKER_AGENT_CONTEXT_PATH'] + 'stats/')
async def get_jobs(web, request):
    return web.json({
        'accept_jobs': ca.is_accepting_jobs,
        'jobs': current_jobs,
    })


@app.page(config['WORKER_AGENT_CONTEXT_PATH'] + 'pause/')
async def get_stat(web, request):
    ca.logger.warn('PAUSE requested - stopped accepting jobs')
    ca.set_accepting_jobs(False)
    return web.json({
         'accept_jobs': ca.is_accepting_jobs
    })


@app.page(config['WORKER_AGENT_CONTEXT_PATH'] + 'resume/')
async def get_stat(web, request):
    ca.logger.warn('RESUME requested - accepting jobs now')
    ca.set_accepting_jobs(True)
    return web.json({
         'accept_jobs': ca.is_accepting_jobs
    })


if __name__ == '__main__':
    app.main()
