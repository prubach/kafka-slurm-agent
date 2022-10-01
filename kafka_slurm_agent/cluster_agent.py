import ast
import os

import faust
import sys
from pydoc import locate
from kafka_slurm_agent.kafka_modules import config, HeartbeatSender
from concurrent.futures import ThreadPoolExecutor

app = faust.App(config['CLUSTER_NAME'] + '_cluster_agent',
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
ca_class = locate(config['CLUSTER_AGENT_CLASS'])
ca = ca_class()
heartbeat_sender = HeartbeatSender()


def run_cluster_agent_check():
    run_timeout = None
    if 'CLUSTER_JOB_TIMEOUT' in config and config['CLUSTER_JOB_TIMEOUT']:
        run_timeout = config['CLUSTER_JOB_TIMEOUT']
    for key in list(job_status.keys()):
        if key in job_status.keys():
            js = ast.literal_eval(str(job_status[key]))
            if js['cluster'] == config['CLUSTER_NAME'] and js['status'] in ['SUBMITTED', 'WAITING', 'RUNNING', 'UPLOADING']:
                status, reason, run_time = ca.check_job_status(js['job_id'])
                if run_timeout and run_time > run_timeout:
                    cancel_success = ca.cancel_job(js['job_id'])
                    if cancel_success:
                        ca.stat_send.send(key, 'TIMEOUT', js['job_id'], error='Timeout out after {} sec.'.format(run_timeout))
                    else:
                        ca.stat_send.send(key, 'ERROR', js['job_id'], error='Timeout after {} sec but couldnt kill'.format(run_timeout))
                    continue
                if not status:
                    ca.stat_send.send(key, 'ERROR', js['job_id'], error='Missing from slurm queue')
                elif js['status'] != status:
                    ca.stat_send.send(key, status, js['job_id'], node=reason)
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

# @app.page('/stats/')
# async def get_stat(web, request):
#      statuses = {}
#      for key in job_status.keys():
#          statuses[key] = job_status[key]
#      return web.json({
#          'result': statuses,
#      })


if __name__ == '__main__':
    app.main()
