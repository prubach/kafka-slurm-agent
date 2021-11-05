import socket
import faust

from kafka_slurm_agent.kafka_modules import setupLogger
from kafka_slurm_agent.kafka_modules import config
from kafka_slurm_agent.command import Command


app = faust.App(socket.gethostname() + '_monitor_agent_new',
                group_id=1,
                broker='kafka://' + config['BOOTSTRAP_SERVERS'],
                broker_credentials=faust.SASLCredentials(
                    username=config['KAFKA_USERNAME'],
                    password=config['KAFKA_PASSWORD'],
                ),
                #processing_guarantee='exactly_once',
                topic_partitions=1)

#store='rocksdb://',
logger = setupLogger(config['LOGS_DIR'], "monitor_agent")
jobs_topic = app.topic(config['TOPIC_STATUS'])
done_topic = app.topic(config['TOPIC_DONE'], partitions=1)
error_topic = app.topic(config['TOPIC_ERROR'], partitions=1)
new_topic = app.topic(config['TOPIC_NEW'])
job_status = app.Table('job_status', default='')


@app.agent(jobs_topic)
async def process_jobs(stream):
    async for event in stream.events():
        job_status[event.key.decode('UTF-8')] = event.value


@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'sum/')
async def get_stats(web, request):
    done = 0
    running = 0
    error = 0
    waiting = 0
    new_waiting, new_done, new_all = get_new()
    for key in job_status.keys():
        if job_status[key]['status'] == 'DONE':
            done += 1
        elif job_status[key]['status'] == 'ERROR':
            error += 1
        elif job_status[key]['status'] == 'WAITING':
            waiting += 1
        else:
            running += 1
    return web.json({
        'jobs': {'waiting': waiting, 'running': running, 'done': done, 'error': error},
        'new': {'waiting': new_waiting, 'processed': new_done, 'all':  new_all}
    })


@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'check/{organism}/{struct_id}/')
async def get_stats(web, request, organism, struct_id):
    job_id = organism + '/' + struct_id
    if job_id in job_status:
        result = job_status[job_id]
    else:
        result = ''
    return web.json({
        job_id: result,
    })


def get_new():
    cmd = "/opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server 172.17.0.1:9092 --describe --group " + config['CLUSTER_AGENT_NEW_GROUP'] + "| grep " + config['TOPIC_NEW'] + "| awk {'printf (\"%s %s %s\\n\", $4, $5, $6)'}"
    comd = Command(cmd)
    comd.run(10)
    res = comd.getOut()
    waiting = 0
    all = 0
    running_done = 0
    if res:
        lines = res.splitlines()
        for line in lines:
            try:
                run_done, done, left = line.split(' ')
                running_done += int(run_done)
                waiting += int(left)
                all += int(done)
            except Exception as e:
                logger.error('Problem in checking new: {}'.format(e))
    return waiting, running_done, all
