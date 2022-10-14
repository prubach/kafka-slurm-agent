import socket
import faust

from kafka_slurm_agent.kafka_modules import setupLogger, config
from kafka_slurm_agent.command import Command

app = faust.App(config['MONITOR_AGENT_NEW_GROUP'] if 'MONITOR_AGENT_NEW_GROUP' in config else socket.gethostname() + '_monitor_agent_new',
                group_id=1,
                broker='kafka://' + config['BOOTSTRAP_SERVERS'],
                broker_credentials=config['KAFKA_FAUST_BROKER_CREDENTIALS'],
                #group_id=config['MONITOR_AGENT_NEW_GROUP'],
                #processing_guarantee='exactly_once',
                heartbeat_interval_ms=config['MONITOR_HEARTBEAT_INTERVAL_MS'],
                topic_partitions=1)

#store='rocksdb://',
logger = setupLogger(config['LOGS_DIR'], "monitor_agent")
jobs_topic = app.topic(config['TOPIC_STATUS'])
done_topic = app.topic(config['TOPIC_DONE'], partitions=1)
error_topic = app.topic(config['TOPIC_ERROR'], partitions=1)
new_topic = app.topic(config['TOPIC_NEW'])
heartbeat_topic = app.topic(config['TOPIC_HEARTBEAT'])
job_status = app.Table('job_status', default='')


@app.agent(jobs_topic)
async def process_jobs(stream):
    async for event in stream.events():
        job_status[event.key.decode('UTF-8')] = event.value



@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'done/')
async def get_stats_done(web, request):
    cur, log_end, lag = get_monitor_processed()
    return web.json({
            'done': {'current': cur, 'all': log_end, 'lag': lag}
        })


@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'sum/')
async def get_stats(web, request):
    done = 0
    running = 0
    error = 0
    waiting = 0
    timedout = 0
    submitted = 0
    new_waiting, new_done, new_all = get_new()
    for key in job_status.keys():
        if job_status[key]['status'] == 'DONE':
            done += 1
        elif job_status[key]['status'] == 'ERROR':
            error += 1
        elif job_status[key]['status'] == 'SUBMITTED':
            submitted += 1
        elif job_status[key]['status'] == 'WAITING':
            waiting += 1
        elif job_status[key]['status'] == 'TIMEOUT':
            timedout += 1
        else:
            running += 1
    return web.json({
        'jobs': {'submitted': submitted, 'waiting': waiting, 'running': running, 'done': done, 'timeout': timedout, 'error': error},
        'new': {'waiting': new_waiting, 'processed': new_done, 'all':  new_all}
    })


@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'check/{input_job_id}/')
async def get_stats(web, request, input_job_id):
    if input_job_id in job_status:
        result = job_status[input_job_id]
    else:
        result = ''
    return web.json({
        input_job_id: result,
    })


@app.page(config['MONITOR_AGENT_CONTEXT_PATH'] + 'stats/')
async def get_stat(web, request):
      statuses = {}
      for key in job_status.keys():
          statuses[key] = job_status[key]
      return web.json({
          'result': statuses,
      })


def get_new():
    if 'BOOTSTRAP_SERVERS_LOCAL' not in config:
        config['BOOTSTRAP_SERVERS_LOCAL'] = config['BOOTSTRAP_SERVERS']
    cmd = config['KAFKA_HOME'] + "/bin/kafka-consumer-groups.sh --bootstrap-server " + config['BOOTSTRAP_SERVERS_LOCAL'] + " --describe --group " + config['CLUSTER_AGENT_NEW_GROUP'] + "| grep " + config['TOPIC_NEW'] + "| awk {'printf (\"%s %s %s\\n\", $4, $5, $6)'}"
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


def get_monitor_processed():
    if 'BOOTSTRAP_SERVERS_LOCAL' not in config:
        config['BOOTSTRAP_SERVERS_LOCAL'] = config['BOOTSTRAP_SERVERS']
    cmd = config['KAFKA_HOME'] + "/bin/kafka-consumer-groups.sh --bootstrap-server " + config['BOOTSTRAP_SERVERS_LOCAL'] + " --describe --group " + config['MONITOR_AGENT_NEW_GROUP'] + "| grep " + config['TOPIC_DONE'] + "| awk '{print $4, $5, $6}'"
    comd = Command(cmd)
    comd.run(10)
    res = comd.getOut()
    cur = None
    log_end = None
    lag = None
    if res:
        results = res.split(' ')
        cur = int(results[0].strip())
        log_end = int(results[1].strip())
        lag = int(results[2].strip())
    return cur, log_end, lag
